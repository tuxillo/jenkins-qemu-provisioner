import logging
import threading
import time
from datetime import UTC, datetime
from pathlib import Path

from node_agent.config import get_agent_settings
from node_agent.qemu_runtime import terminate_pid
from node_agent.state import (
    delete_vm,
    get_vm,
    initialize_state,
    list_vms,
    update_vm_state,
)


logger = logging.getLogger(__name__)


def _parse_dt(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is not None:
            return parsed.astimezone(UTC).replace(tzinfo=None)
        return parsed
    except ValueError:
        return None


def enforce_ttl_once() -> None:
    settings = get_agent_settings()
    now = datetime.now(UTC).replace(tzinfo=None)
    for vm in list_vms():
        ttl = _parse_dt(vm.get("lease_expires_at"))
        if ttl and now > ttl:
            pid = int(vm.get("qemu_pid") or 0)
            terminate_pid(pid, dry_run=settings.dry_run)
            update_vm_state(vm["vm_id"], "TERMINATED", reason="ttl_expired", qemu_pid=0)
            cleanup_vm_artifacts(vm)
            delete_vm(vm["vm_id"])


def cleanup_vm_artifacts(vm: dict) -> None:
    for path_field in ("overlay_path", "cloud_init_iso"):
        path = vm.get(path_field)
        if path:
            Path(path).unlink(missing_ok=True)


def reconcile_once() -> None:
    settings = get_agent_settings()
    for vm in list_vms():
        pid = int(vm.get("qemu_pid") or 0)
        if pid <= 0:
            continue
        alive = True
        if not settings.dry_run:
            try:
                import os

                os.kill(pid, 0)
            except ProcessLookupError:
                alive = False
            except PermissionError:
                alive = True
        if not alive:
            update_vm_state(vm["vm_id"], "FAILED", reason="missing_process", qemu_pid=0)
            cleanup_vm_artifacts(vm)
            delete_vm(vm["vm_id"])


def cleanup_orphan_files_once() -> None:
    settings = get_agent_settings()
    known = {vm.get("overlay_path") for vm in list_vms() if vm.get("overlay_path")}
    overlay_dir = Path(settings.overlay_dir)
    if not overlay_dir.exists():
        return
    for file in overlay_dir.glob("*.qcow2"):
        if str(file) not in known:
            file.unlink(missing_ok=True)


def safety_worker(stop_event: threading.Event) -> None:
    settings = get_agent_settings()
    while not stop_event.is_set():
        try:
            enforce_ttl_once()
            reconcile_once()
            cleanup_orphan_files_once()
        except Exception as exc:  # noqa: BLE001
            logger.warning("safety cycle failed: %s", exc)
        stop_event.wait(
            min(settings.ttl_check_interval_sec, settings.reconcile_interval_sec)
        )


def startup_reconcile() -> None:
    initialize_state()
    reconcile_once()


def start_safety_thread(stop_event: threading.Event) -> threading.Thread:
    thread = threading.Thread(
        target=safety_worker, args=(stop_event,), name="safety-worker", daemon=True
    )
    thread.start()
    time.sleep(0.01)
    return thread
