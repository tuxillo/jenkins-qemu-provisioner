import json
import logging
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
_diag_throttle: dict[str, datetime] = {}
logger = logging.getLogger(__name__)


def _host_schedulable(host: Host) -> bool:
    settings = get_settings()
    if not host.enabled:
        return False
    if host.last_seen is None:
        return False
    return datetime.now(UTC).replace(tzinfo=None) - host.last_seen <= timedelta(
        seconds=settings.host_stale_timeout_sec
    )


def _label_requirements(label: str) -> tuple[str | None, str | None]:
    lowered = label.lower()
    required_accel = None
    required_os = None
    if "nvmm" in lowered:
        required_accel = "nvmm"
    elif "kvm" in lowered:
        required_accel = "kvm"

    if "dragonflybsd" in lowered or "dfly" in lowered:
        required_os = "dragonflybsd"
    elif "linux" in lowered:
        required_os = "linux"
    return required_accel, required_os


def _host_meets_capability(host: Host, label: str) -> bool:
    required_accel, required_os = _label_requirements(label)

    supported: list[str] = []
    if host.supported_accels:
        try:
            parsed = json.loads(host.supported_accels)
            if isinstance(parsed, list):
                supported = [str(x) for x in parsed]
        except json.JSONDecodeError:
            supported = []

    if host.selected_accel and supported and host.selected_accel not in supported:
        return False
    if required_accel and host.selected_accel and host.selected_accel != required_accel:
        return False
    if required_os and host.os_family and (host.os_family or "").lower() != required_os:
        return False
    return True


def _host_capability_reason(host: Host, label: str) -> str | None:
    required_accel, required_os = _label_requirements(label)

    supported: list[str] = []
    if host.supported_accels:
        try:
            parsed = json.loads(host.supported_accels)
            if isinstance(parsed, list):
                supported = [str(x) for x in parsed]
        except json.JSONDecodeError:
            supported = []

    if host.selected_accel and supported and host.selected_accel not in supported:
        return "accel_invalid"
    if required_accel and host.selected_accel and host.selected_accel != required_accel:
        return "accel_mismatch"
    if required_os and host.os_family and (host.os_family or "").lower() != required_os:
        return "os_mismatch"
    return None


def _eligible_hosts(label: str, hosts: list[Host]) -> list[Host]:
    profile_name = choose_profile(label)
    profile = NODE_PROFILES[profile_name]
    eligible = [
        h
        for h in hosts
        if _host_schedulable(h)
        and _host_meets_capability(h, label)
        and h.cpu_free >= profile["vcpu"]
        and h.ram_free_mb >= profile["ram_mb"]
    ]
    eligible.sort(key=lambda h: (h.io_pressure, -h.cpu_free, -h.ram_free_mb))
    return eligible


def _eligible_hosts_with_reasons(
    label: str, hosts: list[Host]
) -> tuple[list[Host], dict[str, int]]:
    settings = get_settings()
    now = datetime.now(UTC).replace(tzinfo=None)
    profile_name = choose_profile(label)
    profile = NODE_PROFILES[profile_name]
    reasons: dict[str, int] = {}
    eligible: list[Host] = []

    for host in hosts:
        reason: str | None = None
        if not host.enabled:
            reason = "disabled"
        elif host.last_seen is None or now - host.last_seen > timedelta(
            seconds=settings.host_stale_timeout_sec
        ):
            reason = "stale"
        else:
            reason = _host_capability_reason(host, label)
            if reason is None and host.cpu_free < profile["vcpu"]:
                reason = "cpu_insufficient"
            if reason is None and host.ram_free_mb < profile["ram_mb"]:
                reason = "ram_insufficient"

        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            eligible.append(host)

    eligible.sort(key=lambda h: (h.io_pressure, -h.cpu_free, -h.ram_free_mb))
    return eligible, reasons


def _throttled_diag_event(
    *,
    event_type: str,
    payload: dict,
    now: datetime,
    throttle_sec: int = 30,
) -> bool:
    key = f"{event_type}:{payload.get('label', '_')}"
    last = _diag_throttle.get(key)
    if last and (now - last).total_seconds() < throttle_sec:
        return False
    _diag_throttle[key] = now
    with session_scope() as session:
        write_event(session, event_type, payload)
    return True


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
    if not snapshot.queued_by_label:
        metrics.inc("scale_no_queue_labels_total")
    for label, queued in snapshot.queued_by_label.items():
        if queued <= 0:
            continue
        if _cooldowns.get(label, now) > now:
            metrics.inc("scale_cooldown_skip_total")
            _throttled_diag_event(
                event_type="scale.cooldown_active",
                payload={"label": label, "queued": queued},
                now=now,
            )
            continue

        inflight = inflight_by_label.get(label, 0)
        ready_unused = 0
        raw_deficit = queued - inflight - ready_unused
        if raw_deficit <= 0:
            continue
        if inflight >= settings.label_max_inflight:
            metrics.inc("scale_inflight_limit_skip_total")
            _throttled_diag_event(
                event_type="scale.inflight_limit",
                payload={
                    "label": label,
                    "queued": queued,
                    "inflight": inflight,
                    "max_inflight": settings.label_max_inflight,
                },
                now=now,
            )
            continue

        remaining_global = max(settings.global_max_vms - active_global, 0)
        launchable = min(raw_deficit, settings.label_burst, remaining_global)
        if launchable <= 0:
            metrics.inc("scale_global_limit_skip_total")
            _throttled_diag_event(
                event_type="scale.global_limit",
                payload={
                    "label": label,
                    "queued": queued,
                    "raw_deficit": raw_deficit,
                    "remaining_global": remaining_global,
                },
                now=now,
            )
            continue

        candidates, reject_reasons = _eligible_hosts_with_reasons(label, hosts)
        if not candidates:
            metrics.inc("scale_no_eligible_hosts_total")
            for reason, count in reject_reasons.items():
                metrics.inc(f"scale_reject_{reason}_total", count)
            emitted = _throttled_diag_event(
                event_type="scale.no_eligible_hosts",
                payload={
                    "label": label,
                    "queued": queued,
                    "inflight": inflight,
                    "host_count": len(hosts),
                    "reject_reasons": reject_reasons,
                },
                now=now,
            )
            if emitted:
                logger.warning(
                    "no eligible hosts label=%s queued=%s inflight=%s reasons=%s",
                    label,
                    queued,
                    inflight,
                    reject_reasons,
                )
            continue

        for _ in range(launchable):
            if not candidates:
                break
            host = candidates[0]
            node_agent = node_agent_factory(host.host_id)
            try:
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
            except Exception as exc:  # noqa: BLE001
                metrics.inc("scale_launch_failed_total")
                with session_scope() as session:
                    write_event(
                        session,
                        "scale.launch_failed",
                        {
                            "label": label,
                            "host_id": host.host_id,
                            "error": str(exc),
                        },
                    )
                logger.exception(
                    "launch failed label=%s host_id=%s error=%s",
                    label,
                    host.host_id,
                    exc,
                )

        _cooldowns[label] = now + timedelta(seconds=settings.loop_interval_sec * 3)
