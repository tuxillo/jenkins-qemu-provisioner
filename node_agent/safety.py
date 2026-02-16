import logging
import shutil
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
            cleanup_vm_artifacts(vm, settings=settings)
            delete_vm(vm["vm_id"])


def cleanup_vm_artifacts(vm: dict, settings=None) -> dict[str, bool]:
    if settings is None:
        settings = get_agent_settings()

    retention = max(int(settings.debug_artifact_retention_sec), 0)
    if retention > 0:
        return {
            "deleted_overlay": False,
            "deleted_cloud_init_iso": False,
            "deleted_vm_dir": False,
        }

    deleted_overlay = _unlink_if_exists(vm.get("overlay_path"))
    deleted_cloud_init_iso = _unlink_if_exists(vm.get("cloud_init_iso"))
    deleted_vm_dir = _remove_vm_runtime_dir(vm)
    return {
        "deleted_overlay": deleted_overlay,
        "deleted_cloud_init_iso": deleted_cloud_init_iso,
        "deleted_vm_dir": deleted_vm_dir,
    }


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
            cleanup_vm_artifacts(vm, settings=settings)
            delete_vm(vm["vm_id"])


def cleanup_orphan_files_once() -> None:
    settings = get_agent_settings()
    retention = max(int(settings.debug_artifact_retention_sec), 0)
    now = time.time()

    known = {vm.get("overlay_path") for vm in list_vms() if vm.get("overlay_path")}
    overlay_dir = Path(settings.overlay_dir)
    if not overlay_dir.exists():
        overlay_files: list[Path] = []
    else:
        overlay_files = list(overlay_dir.glob("*.qcow2"))

    for file in overlay_files:
        if str(file) not in known:
            if retention <= 0 or _older_than(file, now, retention):
                file.unlink(missing_ok=True)

    known_vm_dirs = {
        p
        for p in (_runtime_dir_path(vm) for vm in list_vms())
        if p is not None and p.exists()
    }
    cloud_init_dir = Path(settings.cloud_init_dir)
    if not cloud_init_dir.exists():
        return

    for entry in cloud_init_dir.iterdir():
        if not entry.is_dir() or entry in known_vm_dirs:
            continue
        if retention <= 0 or _older_than(entry, now, retention):
            shutil.rmtree(entry, ignore_errors=True)


def _unlink_if_exists(path_value: str | None) -> bool:
    if not path_value:
        return False
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return False
    path.unlink(missing_ok=True)
    return True


def _runtime_dir_path(vm: dict) -> Path | None:
    serial_log_path = vm.get("serial_log_path")
    if isinstance(serial_log_path, str) and serial_log_path:
        return Path(serial_log_path).parent

    cloud_init_iso = vm.get("cloud_init_iso")
    if isinstance(cloud_init_iso, str) and cloud_init_iso:
        return Path(cloud_init_iso).parent
    return None


def _remove_vm_runtime_dir(vm: dict) -> bool:
    vm_dir = _runtime_dir_path(vm)
    if vm_dir is None or not vm_dir.exists() or not vm_dir.is_dir():
        return False
    shutil.rmtree(vm_dir, ignore_errors=True)
    return True


def _older_than(path: Path, now_epoch: float, age_sec: int) -> bool:
    try:
        st = path.stat()
    except OSError:
        return False
    return (now_epoch - st.st_mtime) >= age_sec


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
