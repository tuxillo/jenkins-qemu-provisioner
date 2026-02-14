import logging
import threading
import time
from datetime import UTC, datetime

import httpx

from node_agent.config import get_agent_settings
from node_agent.state import list_vms


logger = logging.getLogger(__name__)


class ControlPlaneSession:
    def __init__(self) -> None:
        self.session_token: str | None = None
        self.session_expires_at: datetime | None = None


def _base_headers(token: str | None) -> dict[str, str]:
    if not token:
        return {}
    return {"Authorization": f"Bearer {token}"}


def register_host(client: httpx.Client, state: ControlPlaneSession) -> None:
    settings = get_agent_settings()
    payload = {
        "agent_version": "0.1.0",
        "qemu_version": "unknown",
        "cpu_total": (
            1
            if not hasattr(__import__("os"), "cpu_count")
            else (__import__("os").cpu_count() or 1)
        ),
        "ram_total_mb": 0,
        "base_image_ids": [],
        "addr": f"{settings.bind_host}:{settings.bind_port}",
        "os_family": settings.os_family,
        "os_version": settings.os_version,
        "qemu_binary": settings.qemu_binary,
        "supported_accels": [settings.qemu_accel, "tcg"],
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


def send_heartbeat(client: httpx.Client, state: ControlPlaneSession) -> None:
    settings = get_agent_settings()
    if not state.session_token:
        raise RuntimeError("missing session token")

    running_ids = [
        r.get("vm_id")
        for r in list_vms()
        if r.get("state") in {"RUNNING", "BOOTING", "PROVISIONING"}
    ]
    payload = {
        "cpu_free": 0,
        "ram_free_mb": 0,
        "io_pressure": 0.0,
        "running_vm_ids": running_ids,
        "os_family": settings.os_family,
        "os_version": settings.os_version,
        "qemu_binary": settings.qemu_binary,
        "supported_accels": [settings.qemu_accel, "tcg"],
        "selected_accel": settings.qemu_accel,
    }
    response = client.post(
        f"/v1/hosts/{settings.host_id}/heartbeat",
        headers=_base_headers(state.session_token),
        json=payload,
    )
    response.raise_for_status()


def heartbeat_worker(stop_event: threading.Event) -> None:
    settings = get_agent_settings()
    state = ControlPlaneSession()
    client = httpx.Client(base_url=settings.control_plane_url, timeout=10.0)

    while not stop_event.is_set():
        try:
            if state.session_token is None:
                register_host(client, state)

            if (
                state.session_expires_at
                and datetime.now(UTC) >= state.session_expires_at
            ):
                state.session_token = None
                continue

            send_heartbeat(client, state)
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
