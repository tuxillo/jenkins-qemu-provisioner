import logging
import os
import threading
import time
from datetime import UTC, datetime, timedelta
from typing import TypedDict

import httpx

from node_agent.base_images import available_images
from node_agent.config import get_agent_settings
from node_agent.host_stats import get_host_stats_service
from node_agent.state import list_vms


logger = logging.getLogger(__name__)
ACTIVE_VM_STATES = {"RUNNING", "BOOTING", "PROVISIONING"}


class CapacitySnapshot(TypedDict):
    cpu_total: int
    ram_total_mb: int
    cpu_allocatable: int
    ram_allocatable_mb: int
    cpu_free: int
    ram_free_mb: int
    running_vm_ids: list[str]


class ControlPlaneSession:
    def __init__(self) -> None:
        self.session_token: str | None = None
        self.session_expires_at: datetime | None = None
        self.register_disabled_until: datetime | None = None


def _detect_total_ram_mb() -> int:
    fallback_mb = 1024
    try:
        page_size = os.sysconf("SC_PAGE_SIZE")
        page_count = os.sysconf("SC_PHYS_PAGES")
        if isinstance(page_size, int) and isinstance(page_count, int):
            total_mb = (page_size * page_count) // (1024 * 1024)
            if total_mb > 0:
                return total_mb
    except (ValueError, OSError, AttributeError):
        pass
    return fallback_mb


def _base_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def _physical_capacity() -> tuple[int, int]:
    cpu_total = os.cpu_count() or 1
    ram_total_mb = max(_detect_total_ram_mb(), 256)
    return cpu_total, ram_total_mb


def _allocatable_capacity(
    settings, cpu_total: int, ram_total_mb: int
) -> tuple[int, int]:
    cpu_allocatable = settings.allocatable_vcpu or cpu_total
    ram_allocatable_mb = settings.allocatable_ram_mb or ram_total_mb
    cpu_allocatable = max(min(cpu_allocatable, cpu_total), 1)
    ram_allocatable_mb = max(min(ram_allocatable_mb, ram_total_mb), 256)
    return cpu_allocatable, ram_allocatable_mb


def current_capacity_snapshot() -> CapacitySnapshot:
    settings = get_agent_settings()
    cpu_total, ram_total_mb = _physical_capacity()
    cpu_allocatable, ram_allocatable_mb = _allocatable_capacity(
        settings, cpu_total, ram_total_mb
    )

    rows = list_vms()
    running_ids = [
        vm_id
        for row in rows
        for vm_id in [row.get("vm_id")]
        if row.get("state") in ACTIVE_VM_STATES and isinstance(vm_id, str) and vm_id
    ]
    reserved_cpu, reserved_ram_mb = _reserved_capacity(rows)
    cpu_free = max(cpu_allocatable - reserved_cpu, 0)
    ram_free_mb = max(ram_allocatable_mb - reserved_ram_mb, 0)
    return {
        "cpu_total": cpu_total,
        "ram_total_mb": ram_total_mb,
        "cpu_allocatable": cpu_allocatable,
        "ram_allocatable_mb": ram_allocatable_mb,
        "cpu_free": cpu_free,
        "ram_free_mb": ram_free_mb,
        "running_vm_ids": running_ids,
    }


def register_host(client: httpx.Client, state: ControlPlaneSession) -> None:
    settings = get_agent_settings()
    capacity = current_capacity_snapshot()
    image_inventory = available_images(settings)
    advertised_addr = (
        settings.advertise_addr or f"{settings.bind_host}:{settings.bind_port}"
    )
    payload = {
        "agent_version": "0.1.0",
        "qemu_version": "unknown",
        "cpu_total": capacity["cpu_total"],
        "ram_total_mb": capacity["ram_total_mb"],
        "cpu_allocatable": capacity["cpu_allocatable"],
        "ram_allocatable_mb": capacity["ram_allocatable_mb"],
        "base_image_ids": [image.base_image_id for image in image_inventory],
        "available_images": [image.model_dump() for image in image_inventory],
        "addr": advertised_addr,
        "os_family": settings.os_family,
        "os_flavor": settings.os_flavor,
        "os_version": settings.os_version,
        "cpu_arch": settings.cpu_arch,
        "qemu_binary": settings.qemu_binary,
        "supported_accels": settings.supported_accels,
        "selected_accel": settings.qemu_accel,
    }
    response = client.post(
        f"/v1/hosts/{settings.host_id}/register",
        headers={"Authorization": f"Bearer {settings.bootstrap_token}"},
        json=payload,
    )
    response.raise_for_status()
    body = response.json()
    state.session_token = body["session_token"]
    state.session_expires_at = datetime.fromisoformat(
        body["session_expires_at"].replace("Z", "+00:00")
    )
    state.register_disabled_until = None


def send_heartbeat(client: httpx.Client, state: ControlPlaneSession) -> None:
    settings = get_agent_settings()
    if not state.session_token:
        raise RuntimeError("missing session token")

    capacity = current_capacity_snapshot()
    host_stats = get_host_stats_service().latest()
    image_inventory = available_images(settings)
    payload = {
        "cpu_total": capacity["cpu_total"],
        "ram_total_mb": capacity["ram_total_mb"],
        "cpu_allocatable": capacity["cpu_allocatable"],
        "ram_allocatable_mb": capacity["ram_allocatable_mb"],
        "cpu_free": capacity["cpu_free"],
        "ram_free_mb": capacity["ram_free_mb"],
        "io_pressure": host_stats.io_pressure,
        "running_vm_ids": capacity["running_vm_ids"],
        "available_images": [image.model_dump() for image in image_inventory],
        "os_family": settings.os_family,
        "os_flavor": settings.os_flavor,
        "os_version": settings.os_version,
        "cpu_arch": settings.cpu_arch,
        "qemu_binary": settings.qemu_binary,
        "supported_accels": settings.supported_accels,
        "selected_accel": settings.qemu_accel,
    }
    response = client.post(
        f"/v1/hosts/{settings.host_id}/heartbeat",
        headers=_base_headers(state.session_token),
        json=payload,
    )
    response.raise_for_status()


def _reserved_capacity(rows: list[dict]) -> tuple[int, int]:
    reserved_cpu = 0
    reserved_ram_mb = 0
    for row in rows:
        if row.get("state") not in ACTIVE_VM_STATES:
            continue
        reserved_cpu += _coerce_nonnegative_int(row.get("vcpu"))
        reserved_ram_mb += _coerce_nonnegative_int(row.get("ram_mb"))
    return reserved_cpu, reserved_ram_mb


def _coerce_nonnegative_int(value: object) -> int:
    if isinstance(value, bool):
        return int(value)
    if isinstance(value, (str, int, float)):
        candidate: str | int | float = value
    else:
        return 0
    try:
        coerced = int(candidate)
    except (TypeError, ValueError):
        return 0
    return coerced if coerced > 0 else 0


def heartbeat_worker(stop_event: threading.Event) -> None:
    settings = get_agent_settings()
    state = ControlPlaneSession()
    client = httpx.Client(base_url=settings.control_plane_url, timeout=10.0)

    while not stop_event.is_set():
        try:
            if (
                state.register_disabled_until
                and datetime.now(UTC) < state.register_disabled_until
            ):
                stop_event.wait(settings.heartbeat_interval_sec)
                continue

            if state.session_token is None:
                register_host(client, state)

            if (
                state.session_expires_at
                and datetime.now(UTC) >= state.session_expires_at
            ):
                state.session_token = None
                continue

            send_heartbeat(client, state)
        except httpx.HTTPStatusError as exc:
            if (
                exc.response.status_code == 403
                and "host disabled" in exc.response.text.lower()
            ):
                state.register_disabled_until = datetime.now(UTC) + timedelta(
                    seconds=60
                )
                logger.warning(
                    "heartbeat paused: host disabled by control-plane host_id=%s",
                    settings.host_id,
                )
            logger.warning(
                "heartbeat cycle failed: status=%s url=%s body=%s",
                exc.response.status_code,
                exc.request.url,
                exc.response.text,
            )
            state.session_token = None
        except httpx.HTTPError as exc:
            logger.warning("heartbeat cycle failed: %s", exc)
            state.session_token = None
        except Exception as exc:  # noqa: BLE001
            logger.warning("heartbeat cycle failed: %s", exc)
            state.session_token = None
        stop_event.wait(settings.heartbeat_interval_sec)

    client.close()


def start_heartbeat_thread(stop_event: threading.Event) -> threading.Thread:
    thread = threading.Thread(
        target=heartbeat_worker,
        args=(stop_event,),
        name="heartbeat-worker",
        daemon=True,
    )
    thread.start()
    time.sleep(0.01)
    return thread
