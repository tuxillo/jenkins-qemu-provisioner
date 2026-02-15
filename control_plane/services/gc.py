from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from control_plane.config import get_settings
from control_plane.db import session_scope
from control_plane.models import Host
from control_plane.repositories import write_event


def gc_hosts_once() -> None:
    settings = get_settings()
    cutoff = datetime.now(UTC).replace(tzinfo=None) - timedelta(
        seconds=settings.host_stale_timeout_sec
    )
    with session_scope() as session:
        hosts = list(session.scalars(select(Host)))
        for host in hosts:
            if not host.enabled:
                continue
            if host.last_seen and host.last_seen < cutoff:
                write_event(
                    session,
                    "host.stale",
                    {"host_id": host.host_id, "last_seen": host.last_seen.isoformat()},
                )
