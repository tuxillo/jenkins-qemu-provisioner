from datetime import UTC, datetime
from threading import Lock
import threading

from fastapi import FastAPI, HTTPException, Query
import httpx

from fake_node_agent.config import get_settings


app = FastAPI(title="Fake Node Agent")

_lock = Lock()
_vms: dict[str, dict] = {}
_stop_event = threading.Event()
_worker_thread: threading.Thread | None = None


def _running_vm_ids() -> list[str]:
    with _lock:
        return [vm_id for vm_id, row in _vms.items() if row.get("state") == "RUNNING"]


def _register_and_heartbeat_worker(stop_event: threading.Event) -> None:
    settings = get_settings()
    if not settings.enable_heartbeat_worker:
        return

    session_token: str | None = None
    client = httpx.Client(base_url=settings.control_plane_url, timeout=10.0)
    try:
        while not stop_event.is_set():
            try:
                if session_token is None:
                    reg_payload = {
                        "agent_version": "fake-0.1.0",
                        "qemu_version": "fake",
                        "cpu_total": settings.cpu_total,
                        "ram_total_mb": settings.ram_total_mb,
                        "base_image_ids": ["fake-base"],
                        "addr": f"{settings.bind_host}:{settings.bind_port}",
                        "os_family": settings.os_family,
                        "os_version": settings.os_version,
                        "qemu_binary": settings.qemu_binary,
                        "supported_accels": settings.supported_accels,
                        "selected_accel": settings.selected_accel,
                    }
                    reg = client.post(
                        f"/v1/hosts/{settings.host_id}/register",
                        headers={"Authorization": f"Bearer {settings.bootstrap_token}"},
                        json=reg_payload,
                    )
                    reg.raise_for_status()
                    session_token = reg.json().get("session_token")

                hb_payload = {
                    "cpu_free": max(settings.cpu_total - len(_running_vm_ids()), 0),
                    "ram_free_mb": max(
                        settings.ram_total_mb - (len(_running_vm_ids()) * 1024), 0
                    ),
                    "io_pressure": settings.io_pressure,
                    "running_vm_ids": _running_vm_ids(),
                    "os_family": settings.os_family,
                    "os_version": settings.os_version,
                    "qemu_binary": settings.qemu_binary,
                    "supported_accels": settings.supported_accels,
                    "selected_accel": settings.selected_accel,
                }
                hb = client.post(
                    f"/v1/hosts/{settings.host_id}/heartbeat",
                    headers={"Authorization": f"Bearer {session_token}"},
                    json=hb_payload,
                )
                if hb.status_code == 401:
                    session_token = None
                else:
                    hb.raise_for_status()
            except Exception:
                session_token = None
            stop_event.wait(settings.heartbeat_interval_sec)
    finally:
        client.close()


@app.on_event("startup")
def startup() -> None:
    global _worker_thread
    settings = get_settings()
    if settings.enable_heartbeat_worker:
        _worker_thread = threading.Thread(
            target=_register_and_heartbeat_worker,
            args=(_stop_event,),
            name="fake-node-agent-heartbeat",
            daemon=True,
        )
        _worker_thread.start()


@app.on_event("shutdown")
def shutdown() -> None:
    _stop_event.set()
    if _worker_thread:
        _worker_thread.join(timeout=1)


@app.get("/healthz")
def healthz() -> dict:
    settings = get_settings()
    return {
        "status": "ok",
        "host_id": settings.host_id,
        "os_family": settings.os_family,
        "selected_accel": settings.selected_accel,
    }


@app.put("/v1/vms/{vm_id}")
def ensure_vm(vm_id: str, payload: dict) -> dict:
    now = datetime.now(UTC).isoformat()
    with _lock:
        existing = _vms.get(vm_id)
        if existing:
            return {
                "vm_id": vm_id,
                "state": existing["state"],
                "last_transition_at": existing["updated_at"],
                "reason": existing.get("reason"),
                "lease_expires_at": existing.get("lease_expires_at"),
            }

        record = {
            "vm_id": vm_id,
            "state": "RUNNING",
            "created_at": now,
            "updated_at": now,
            "lease_expires_at": payload.get("lease_expires_at"),
            "reason": None,
            "label": payload.get("label"),
            "host_id": get_settings().host_id,
            "jenkins_node_name": payload.get("jenkins_node_name"),
        }
        _vms[vm_id] = record
    return {
        "vm_id": vm_id,
        "state": "RUNNING",
        "last_transition_at": now,
        "lease_expires_at": payload.get("lease_expires_at"),
    }


@app.get("/v1/vms/{vm_id}")
def vm_state(vm_id: str) -> dict:
    with _lock:
        row = _vms.get(vm_id)
    if not row:
        raise HTTPException(status_code=404, detail="unknown vm")
    return {
        "vm_id": vm_id,
        "state": row["state"],
        "last_transition_at": row["updated_at"],
        "reason": row.get("reason"),
        "lease_expires_at": row.get("lease_expires_at"),
    }


@app.get("/v1/vms")
def list_vms(
    state: str | None = Query(default=None), host_id: str | None = Query(default=None)
) -> list[dict]:
    with _lock:
        items = list(_vms.values())
    if state:
        items = [x for x in items if x.get("state") == state]
    if host_id:
        items = [x for x in items if x.get("host_id") == host_id]
    return items


@app.delete("/v1/vms/{vm_id}")
def delete_vm(vm_id: str, reason: str = "requested", force: bool = False) -> dict:
    _ = force
    with _lock:
        existed = vm_id in _vms
        if existed:
            del _vms[vm_id]
    return {
        "vm_id": vm_id,
        "state": "TERMINATED",
        "deleted_overlay": bool(existed),
        "reason": reason,
    }


@app.get("/v1/capacity")
def capacity() -> dict:
    settings = get_settings()
    with _lock:
        running = len([x for x in _vms.values() if x.get("state") == "RUNNING"])
    return {
        "host_id": settings.host_id,
        "os_family": settings.os_family,
        "selected_accel": settings.selected_accel,
        "supported_accels": settings.supported_accels,
        "cpu_total": settings.cpu_total,
        "cpu_free": max(settings.cpu_total - running, 0),
        "ram_total_mb": settings.ram_total_mb,
        "ram_free_mb": max(settings.ram_total_mb - running * 1024, 0),
        "io_pressure": settings.io_pressure,
        "running_vms": running,
    }
