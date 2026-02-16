import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.auth import new_session_token, secure_compare_token
from control_plane.config import get_settings
from control_plane.db import SessionLocal
from control_plane.metrics import metrics
from control_plane.models import Event, Host, Lease, LeaseState
from control_plane.repositories import (
    list_leases,
    now_utc,
    update_host_heartbeat,
    write_event,
)
from control_plane.schemas import (
    HeartbeatRequest,
    LeaseRead,
    ManualTerminateRequest,
    RegisterHostRequest,
    RegisterHostResponse,
    VMStatusRequest,
)


router = APIRouter()
NOISY_EVENT_TYPES = {"host.heartbeat"}


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _bearer_token(authorization: str | None) -> str:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="missing bearer token")
    return authorization.split(" ", 1)[1]


def _to_iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _host_availability(host: Host, now: datetime, stale_timeout_sec: int) -> str:
    if not host.enabled:
        return "DISABLED"
    if host.last_seen is None:
        return "UNAVAILABLE"
    age = now.replace(tzinfo=None) - host.last_seen
    if age.total_seconds() > stale_timeout_sec:
        return "STALE"
    return "AVAILABLE"


def _build_snapshot(db: Session) -> dict:
    settings = get_settings()
    now = datetime.now(UTC)
    hosts = list(db.scalars(select(Host).order_by(Host.host_id.asc())))
    leases = list(db.scalars(select(Lease).order_by(Lease.created_at.desc())))
    raw_events = list(db.scalars(select(Event).order_by(Event.id.desc()).limit(250)))
    events = [
        event for event in raw_events if event.event_type not in NOISY_EVENT_TYPES
    ][:50]

    leases_by_state: dict[str, int] = {}
    for lease in leases:
        leases_by_state[lease.state] = leases_by_state.get(lease.state, 0) + 1

    return {
        "generated_at": datetime.now(UTC).isoformat(),
        "counts": {
            "hosts_total": len(hosts),
            "leases_total": len(leases),
            "events_total": len(events),
            "leases_by_state": leases_by_state,
        },
        "hosts": [
            {
                "host_id": h.host_id,
                "enabled": h.enabled,
                "availability": _host_availability(
                    h, now, settings.host_stale_timeout_sec
                ),
                "os_family": h.os_family,
                "os_flavor": h.os_flavor,
                "cpu_arch": h.cpu_arch,
                "addr": h.addr,
                "last_seen": _to_iso(h.last_seen),
                "cpu_total": h.cpu_total,
                "cpu_free": h.cpu_free,
                "ram_total_mb": h.ram_total_mb,
                "ram_free_mb": h.ram_free_mb,
                "io_pressure": h.io_pressure,
            }
            for h in hosts
        ],
        "leases": [
            {
                "lease_id": l.lease_id,
                "vm_id": l.vm_id,
                "label": l.label,
                "jenkins_node": l.jenkins_node,
                "state": l.state,
                "host_id": l.host_id,
                "created_at": _to_iso(l.created_at),
                "updated_at": _to_iso(l.updated_at),
                "connect_deadline": _to_iso(l.connect_deadline),
                "ttl_deadline": _to_iso(l.ttl_deadline),
                "bound_build_url": l.bound_build_url,
                "last_error": l.last_error,
            }
            for l in leases
        ],
        "events": [
            {
                "id": e.id,
                "timestamp": _to_iso(e.timestamp),
                "lease_id": e.lease_id,
                "event_type": e.event_type,
                "payload_json": e.payload_json,
            }
            for e in events
        ],
        "metrics": metrics.snapshot(),
    }


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/metrics")
def metrics_endpoint() -> dict[str, int]:
    return metrics.snapshot()


@router.get("/ui", response_class=HTMLResponse)
def ui_dashboard(db: Session = Depends(get_db)) -> HTMLResponse:
    snapshot = _build_snapshot(db)
    snapshot_json = json.dumps(snapshot).replace("<", "\\u003c")
    html = f"""<!doctype html>
<html lang=\"en\">
  <head>
    <meta charset=\"utf-8\" />
    <meta name=\"viewport\" content=\"width=device-width, initial-scale=1\" />
    <title>Control Plane Dashboard</title>
    <link rel=\"stylesheet\" href=\"/static/ui.css\" />
  </head>
  <body>
    <div id=\"app\"></div>
    <script id=\"cp-snapshot\" type=\"application/json\">{snapshot_json}</script>
    <script src=\"/static/ui.js\" defer></script>
  </body>
</html>"""
    return HTMLResponse(content=html)


@router.post("/v1/hosts/{host_id}/register", response_model=RegisterHostResponse)
def register_host(
    host_id: str,
    req: RegisterHostRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> RegisterHostResponse:
    settings = get_settings()
    token = _bearer_token(authorization)
    host = db.get(Host, host_id)
    if host is None:
        if not settings.allow_unknown_host_registration:
            raise HTTPException(status_code=404, detail="unknown host")
        from control_plane.auth import hash_token

        host = Host(
            host_id=host_id,
            enabled=True,
            bootstrap_token_hash=hash_token(token),
            cpu_total=req.cpu_total,
            cpu_free=req.cpu_total,
            ram_total_mb=req.ram_total_mb,
            ram_free_mb=req.ram_total_mb,
        )
        db.add(host)
        db.flush()
    if not host.enabled:
        raise HTTPException(status_code=403, detail="host disabled")
    if not secure_compare_token(token, host.bootstrap_token_hash):
        raise HTTPException(status_code=401, detail="invalid bootstrap token")

    session_token, session_expires_at = new_session_token(hours=1)
    from control_plane.auth import hash_token

    host.session_token_hash = hash_token(session_token)
    host.session_expires_at = session_expires_at.replace(tzinfo=None)
    host.cpu_total = req.cpu_total
    host.cpu_free = req.cpu_total
    host.ram_total_mb = req.ram_total_mb
    host.ram_free_mb = req.ram_total_mb
    host.os_family = req.os_family
    host.os_flavor = req.os_flavor
    host.os_version = req.os_version
    host.cpu_arch = req.cpu_arch
    host.addr = req.addr
    host.qemu_binary = req.qemu_binary
    host.supported_accels = json.dumps(req.supported_accels)
    host.selected_accel = req.selected_accel
    host.last_seen = now_utc()
    db.add(host)
    write_event(
        db, "host.registered", {"host_id": host_id, "agent_version": req.agent_version}
    )
    db.commit()

    return RegisterHostResponse(
        host_id=host.host_id,
        enabled=host.enabled,
        session_token=session_token,
        session_expires_at=session_expires_at,
        heartbeat_interval_sec=5,
    )


@router.post("/v1/hosts/{host_id}/heartbeat")
def heartbeat(
    host_id: str,
    req: HeartbeatRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> dict[str, bool]:
    token = _bearer_token(authorization)
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="unknown host")
    if not host.enabled:
        raise HTTPException(status_code=403, detail="host disabled")
    if (
        host.session_expires_at is None
        or datetime.now(UTC).replace(tzinfo=None) > host.session_expires_at
    ):
        raise HTTPException(status_code=401, detail="session expired")
    if not secure_compare_token(token, host.session_token_hash):
        raise HTTPException(status_code=401, detail="invalid session token")

    if (
        req.selected_accel
        and req.supported_accels
        and req.selected_accel not in req.supported_accels
    ):
        raise HTTPException(
            status_code=400, detail="selected_accel not supported by host"
        )

    update_host_heartbeat(db, host, req.cpu_free, req.ram_free_mb, req.io_pressure)
    host.os_family = req.os_family or host.os_family
    host.os_flavor = req.os_flavor or host.os_flavor
    host.os_version = req.os_version or host.os_version
    host.cpu_arch = req.cpu_arch or host.cpu_arch
    host.qemu_binary = req.qemu_binary or host.qemu_binary
    host.selected_accel = req.selected_accel or host.selected_accel
    if req.supported_accels:
        host.supported_accels = json.dumps(req.supported_accels)
    write_event(
        db, "host.heartbeat", {"host_id": host_id, "running_vm_ids": req.running_vm_ids}
    )
    db.commit()
    return {"ok": True}


@router.post("/v1/hosts/{host_id}/disable")
def disable_host(host_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="unknown host")
    host.enabled = False
    host.session_token_hash = None
    host.session_expires_at = None
    db.add(host)
    write_event(db, "host.disabled", {"host_id": host_id})
    db.commit()
    return {"ok": True}


@router.post("/v1/hosts/{host_id}/enable")
def enable_host(host_id: str, db: Session = Depends(get_db)) -> dict[str, bool]:
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="unknown host")
    host.enabled = True
    db.add(host)
    write_event(db, "host.enabled", {"host_id": host_id})
    db.commit()
    return {"ok": True}


@router.post("/v1/vms/{vm_id}/status")
def vm_status(
    vm_id: str, req: VMStatusRequest, db: Session = Depends(get_db)
) -> dict[str, bool]:
    lease = db.query(Lease).filter(Lease.vm_id == vm_id).first()
    if lease is None:
        raise HTTPException(status_code=404, detail="unknown vm")
    lease.state = req.state
    lease.updated_at = now_utc()
    if req.reason:
        lease.last_error = req.reason
    db.add(lease)
    write_event(
        db,
        "vm.status",
        {"vm_id": vm_id, "state": req.state, "reason": req.reason},
        lease.lease_id,
    )
    db.commit()
    return {"ok": True}


@router.get("/v1/leases", response_model=list[LeaseRead])
def get_leases(
    label: str | None = Query(default=None),
    state: str | None = Query(default=None),
    host_id: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> list[LeaseRead]:
    leases = list_leases(db, label=label, state=state, host_id=host_id)
    return [
        LeaseRead(
            lease_id=l.lease_id,
            vm_id=l.vm_id,
            label=l.label,
            jenkins_node=l.jenkins_node,
            state=l.state,
            host_id=l.host_id,
            connect_deadline=l.connect_deadline,
            ttl_deadline=l.ttl_deadline,
            bound_build_url=l.bound_build_url,
            last_error=l.last_error,
        )
        for l in leases
    ]


@router.post("/v1/leases/{lease_id}/terminate")
def terminate_lease(
    lease_id: str, req: ManualTerminateRequest, db: Session = Depends(get_db)
) -> dict[str, bool]:
    lease = db.get(Lease, lease_id)
    if lease is None:
        raise HTTPException(status_code=404, detail="unknown lease")
    if lease.state != LeaseState.TERMINATED.value:
        lease.state = LeaseState.TERMINATING.value
        lease.updated_at = now_utc()
        lease.last_error = req.reason
        db.add(lease)
        write_event(db, "lease.manual_terminate", {"reason": req.reason}, lease_id)
        db.commit()
    return {"ok": True}
