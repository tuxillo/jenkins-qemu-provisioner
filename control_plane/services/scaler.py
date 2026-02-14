from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from control_plane.clients.jenkins import JenkinsClient
from control_plane.clients.node_agent import NodeAgentClient
from control_plane.config import get_settings
from control_plane.db import session_scope
from control_plane.metrics import metrics
from control_plane.models import Host, Lease, LeaseState
from control_plane.repositories import write_event
from control_plane.services.provisioning import (
    NODE_PROFILES,
    choose_profile,
    provision_one,
)


_cooldowns: dict[str, datetime] = {}


def _host_schedulable(host: Host) -> bool:
    settings = get_settings()
    if not host.enabled:
        return False
    if host.last_seen is None:
        return False
    return datetime.now(UTC).replace(tzinfo=None) - host.last_seen <= timedelta(
        seconds=settings.host_stale_timeout_sec
    )


def _eligible_hosts(label: str, hosts: list[Host]) -> list[Host]:
    profile_name = choose_profile(label)
    profile = NODE_PROFILES[profile_name]
    eligible = [
        h
        for h in hosts
        if _host_schedulable(h)
        and h.cpu_free >= profile["vcpu"]
        and h.ram_free_mb >= profile["ram_mb"]
    ]
    eligible.sort(key=lambda h: (h.io_pressure, -h.cpu_free, -h.ram_free_mb))
    return eligible


def scale_once(jenkins: JenkinsClient, node_agent_factory) -> None:
    settings = get_settings()
    snapshot = jenkins.queue_snapshot()
    now = datetime.now(UTC).replace(tzinfo=None)

    with session_scope() as session:
        hosts = list(session.scalars(select(Host)))
        active = list(
            session.scalars(
                select(Lease).where(
                    Lease.state.in_(
                        [
                            LeaseState.PROVISIONING.value,
                            LeaseState.BOOTING.value,
                            LeaseState.CONNECTED.value,
                            LeaseState.RUNNING.value,
                        ]
                    )
                )
            )
        )
        active_by_label: dict[str, int] = {}
        inflight_by_label: dict[str, int] = {}
        for lease in active:
            active_by_label[lease.label] = active_by_label.get(lease.label, 0) + 1
            if lease.state in (
                LeaseState.PROVISIONING.value,
                LeaseState.BOOTING.value,
                LeaseState.CONNECTED.value,
            ):
                inflight_by_label[lease.label] = (
                    inflight_by_label.get(lease.label, 0) + 1
                )

    active_global = len(active)
    for label, queued in snapshot.queued_by_label.items():
        if queued <= 0:
            continue
        if _cooldowns.get(label, now) > now:
            continue

        inflight = inflight_by_label.get(label, 0)
        ready_unused = 0
        raw_deficit = queued - inflight - ready_unused
        if raw_deficit <= 0:
            continue
        if inflight >= settings.label_max_inflight:
            continue

        remaining_global = max(settings.global_max_vms - active_global, 0)
        launchable = min(raw_deficit, settings.label_burst, remaining_global)
        if launchable <= 0:
            continue

        candidates = _eligible_hosts(label, hosts)
        if not candidates:
            continue

        for _ in range(launchable):
            if not candidates:
                break
            host = candidates[0]
            node_agent = node_agent_factory(host.host_id)
            provision_one(
                label=label,
                host_id=host.host_id,
                jenkins=jenkins,
                node_agent=node_agent,
            )
            metrics.inc("launch_attempts_total")
            write_payload = {"label": label, "host_id": host.host_id}
            with session_scope() as session:
                write_event(session, "scale.launch", write_payload)

        _cooldowns[label] = now + timedelta(seconds=settings.loop_interval_sec * 3)
