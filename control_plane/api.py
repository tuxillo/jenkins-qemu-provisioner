from datetime import UTC, datetime

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from sqlalchemy.orm import Session

from control_plane.auth import new_session_token, secure_compare_token
from control_plane.config import get_settings
from control_plane.db import SessionLocal
from control_plane.metrics import metrics
from control_plane.models import Host, Lease, LeaseState
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


@router.get("/healthz")
def healthz() -> dict[str, str]:
    return {"status": "ok"}


@router.get("/metrics")
def metrics_endpoint() -> dict[str, int]:
    return metrics.snapshot()


@router.post("/v1/hosts/{host_id}/register", response_model=RegisterHostResponse)
def register_host(
    host_id: str,
    req: RegisterHostRequest,
    authorization: str | None = Header(default=None),
    db: Session = Depends(get_db),
) -> RegisterHostResponse:
    token = _bearer_token(authorization)
    host = db.get(Host, host_id)
    if host is None:
        raise HTTPException(status_code=404, detail="unknown host")
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

    update_host_heartbeat(db, host, req.cpu_free, req.ram_free_mb, req.io_pressure)
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
