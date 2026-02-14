import json
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from control_plane.models import Event, Host, Lease
from control_plane.state_machine import can_transition


def now_utc() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def write_event(
    session: Session, event_type: str, payload: dict, lease_id: str | None = None
) -> None:
    session.add(
        Event(
            lease_id=lease_id,
            event_type=event_type,
            payload_json=json.dumps(payload, sort_keys=True),
        )
    )


def get_host(session: Session, host_id: str) -> Host | None:
    return session.get(Host, host_id)


def list_hosts(session: Session) -> list[Host]:
    return list(session.scalars(select(Host)))


def upsert_host(
    session: Session,
    host_id: str,
    cpu_total: int,
    ram_total_mb: int,
    bootstrap_token_hash: str | None = None,
) -> Host:
    host = get_host(session, host_id)
    if host is None:
        host = Host(
            host_id=host_id,
            enabled=True,
            cpu_total=cpu_total,
            cpu_free=cpu_total,
            ram_total_mb=ram_total_mb,
            ram_free_mb=ram_total_mb,
            bootstrap_token_hash=bootstrap_token_hash,
        )
        session.add(host)
    else:
        host.cpu_total = cpu_total
        host.ram_total_mb = ram_total_mb
    host.last_seen = now_utc()
    return host


def update_host_heartbeat(
    session: Session, host: Host, cpu_free: int, ram_free_mb: int, io_pressure: float
) -> None:
    host.cpu_free = cpu_free
    host.ram_free_mb = ram_free_mb
    host.io_pressure = io_pressure
    host.last_seen = now_utc()


def get_lease(session: Session, lease_id: str) -> Lease | None:
    return session.get(Lease, lease_id)


def list_leases(
    session: Session,
    label: str | None = None,
    state: str | None = None,
    host_id: str | None = None,
) -> list[Lease]:
    query = select(Lease)
    if label:
        query = query.where(Lease.label == label)
    if state:
        query = query.where(Lease.state == state)
    if host_id:
        query = query.where(Lease.host_id == host_id)
    return list(session.scalars(query.order_by(Lease.created_at.desc())))


def cas_lease_state(
    session: Session,
    lease: Lease,
    expected: str,
    target: str,
    last_error: str | None = None,
) -> bool:
    if lease.state != expected:
        return False
    if not can_transition(expected, target):
        return False
    lease.state = target
    lease.updated_at = now_utc()
    if last_error:
        lease.last_error = last_error
    return True
