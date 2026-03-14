import json
import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from control_plane.clients.http import RequestFailure
from control_plane.clients.jenkins import JenkinsClient
from control_plane.clients.node_agent import NodeAgentClient
from control_plane.config import get_settings
from control_plane.db import session_scope
from control_plane.guest_images import (
    AvailableImage,
    ResolvedImageSelection,
    resolve_image_catalog_entry,
    resolve_image_selection,
    resolve_label_policy,
)
from control_plane.metrics import metrics
from control_plane.models import Host, Lease, LeaseState
from control_plane.repositories import write_event
from control_plane.services.provisioning import (
    NODE_PROFILES,
    ProvisioningError,
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


def _host_available_images(host: Host) -> list[AvailableImage]:
    if not host.available_images_json:
        return []
    try:
        payload = json.loads(host.available_images_json)
    except json.JSONDecodeError:
        return []
    if not isinstance(payload, list):
        return []
    images: list[AvailableImage] = []
    for item in payload:
        try:
            images.append(AvailableImage.model_validate(item))
        except Exception:  # noqa: BLE001
            continue
    return images


def _host_image_bucket(host: Host, image: ResolvedImageSelection) -> tuple[int, str]:
    for available in _host_available_images(host):
        if available.state != "READY":
            continue
        if available.guest_image != image.guest_image:
            continue
        if available.base_image_id != image.base_image_id:
            continue
        return 0, "warm_ready"
    if image.source_kind == "remote_cache" and image.cache_policy == "prefer_warm":
        return 1, "cold_fetch_ok"
    return 2, "image_not_ready"


def _host_meets_capability(host: Host, image: ResolvedImageSelection) -> bool:
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
    if image.required_accel and host.selected_accel != image.required_accel:
        return False
    if (
        image.required_cpu_arch
        and host.cpu_arch
        and host.cpu_arch != image.required_cpu_arch
    ):
        return False
    if host.cpu_arch and host.cpu_arch != image.cpu_arch:
        return False
    bucket, _ = _host_image_bucket(host, image)
    if bucket >= 2:
        return False
    return True


def _host_capability_reason(host: Host, image: ResolvedImageSelection) -> str | None:
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
    if image.required_accel and host.selected_accel != image.required_accel:
        return "accel_mismatch"
    if (
        image.required_cpu_arch
        and host.cpu_arch
        and host.cpu_arch != image.required_cpu_arch
    ):
        return "cpu_arch_mismatch"
    if host.cpu_arch and host.cpu_arch != image.cpu_arch:
        return "image_cpu_arch_mismatch"
    bucket, bucket_reason = _host_image_bucket(host, image)
    if bucket >= 2:
        return bucket_reason
    return None


def _eligible_hosts(image: ResolvedImageSelection, hosts: list[Host]) -> list[Host]:
    profile = NODE_PROFILES[image.profile]
    eligible = [
        h
        for h in hosts
        if _host_schedulable(h)
        and _host_meets_capability(h, image)
        and h.cpu_free >= profile["vcpu"]
        and h.ram_free_mb >= profile["ram_mb"]
    ]
    eligible.sort(
        key=lambda h: (
            *_host_image_bucket(h, image),
            h.io_pressure,
            -h.cpu_free,
            -h.ram_free_mb,
        )
    )
    return eligible


def _eligible_hosts_with_reasons(
    image: ResolvedImageSelection, hosts: list[Host]
) -> tuple[list[Host], dict[str, int]]:
    settings = get_settings()
    now = datetime.now(UTC).replace(tzinfo=None)
    profile = NODE_PROFILES[image.profile]
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
            reason = _host_capability_reason(host, image)
            if reason is None and host.cpu_free < profile["vcpu"]:
                reason = "cpu_insufficient"
            if reason is None and host.ram_free_mb < profile["ram_mb"]:
                reason = "ram_insufficient"

        if reason:
            reasons[reason] = reasons.get(reason, 0) + 1
        else:
            eligible.append(host)

    eligible.sort(
        key=lambda h: (
            *_host_image_bucket(h, image),
            h.io_pressure,
            -h.cpu_free,
            -h.ram_free_mb,
        )
    )
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
        active_by_node: dict[str, Lease] = {}
        active_by_label: dict[str, int] = {}
        inflight_by_label: dict[str, int] = {}
        for lease in active:
            active_by_label[lease.label] = active_by_label.get(lease.label, 0) + 1
            active_by_node[lease.jenkins_node] = lease
            if lease.state in (
                LeaseState.PROVISIONING.value,
                LeaseState.BOOTING.value,
                LeaseState.CONNECTED.value,
            ):
                inflight_by_label[lease.label] = (
                    inflight_by_label.get(lease.label, 0) + 1
                )

    active_global = len(active)
    effective_queued_by_label = dict(snapshot.queued_by_label)
    for node_name, queued in snapshot.queued_by_node.items():
        lease = active_by_node.get(node_name)
        if not lease or queued <= 0:
            continue
        effective_queued_by_label[lease.label] = (
            effective_queued_by_label.get(lease.label, 0) + queued
        )
        _throttled_diag_event(
            event_type="scale.node_wait_mapped",
            payload={
                "node": node_name,
                "label": lease.label,
                "queued": queued,
                "lease_id": lease.lease_id,
            },
            now=now,
        )

    if not effective_queued_by_label:
        metrics.inc("scale_no_queue_labels_total")
    for label, queued in effective_queued_by_label.items():
        if queued <= 0:
            continue
        label_policy = resolve_label_policy(label)
        if label_policy is None:
            image = resolve_image_selection(label)
            if image is None:
                metrics.inc("scale_label_policy_missing_total")
                _throttled_diag_event(
                    event_type="scale.label_policy_missing",
                    payload={"label": label, "queued": queued},
                    now=now,
                )
                continue
            _throttled_diag_event(
                event_type="scale.label_policy_compat",
                payload={"label": label, "guest_image": image.guest_image},
                now=now,
            )
        else:
            catalog_entry = resolve_image_catalog_entry(label_policy.guest_image)
            if catalog_entry is None:
                metrics.inc("scale_image_catalog_missing_total")
                _throttled_diag_event(
                    event_type="scale.image_catalog_missing",
                    payload={"label": label, "guest_image": label_policy.guest_image},
                    now=now,
                )
                continue
            image = ResolvedImageSelection(
                guest_image=label_policy.guest_image,
                profile=label_policy.profile,
                required_accel=label_policy.required_accel,
                required_cpu_arch=label_policy.required_cpu_arch,
                base_image_id=catalog_entry.base_image_id,
                os_family=catalog_entry.os_family,
                os_flavor=catalog_entry.os_flavor,
                os_version=catalog_entry.os_version,
                cpu_arch=catalog_entry.cpu_arch,
                source_kind=catalog_entry.source_kind,
                source_url=catalog_entry.source_url,
                source_digest=catalog_entry.source_digest,
                format=catalog_entry.format,
                cache_policy=catalog_entry.cache_policy,
            )
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

        candidates, reject_reasons = _eligible_hosts_with_reasons(image, hosts)
        if not candidates:
            metrics.inc("scale_no_eligible_hosts_total")
            for reason, count in reject_reasons.items():
                metrics.inc(f"scale_reject_{reason}_total", count)
            emitted = _throttled_diag_event(
                event_type="scale.no_eligible_hosts",
                payload={
                    "label": label,
                    "guest_image": image.guest_image,
                    "base_image_id": image.base_image_id,
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

        profile = NODE_PROFILES[image.profile]
        for _ in range(launchable):
            if not candidates:
                break
            host = candidates[0]
            host.cpu_free = max(host.cpu_free - profile["vcpu"], 0)
            host.ram_free_mb = max(host.ram_free_mb - profile["ram_mb"], 0)
            node_agent = node_agent_factory(host.host_id)
            try:
                provision_one(
                    label=label,
                    host_id=host.host_id,
                    jenkins=jenkins,
                    node_agent=node_agent,
                    image_selection=image,
                )
                metrics.inc("launch_attempts_total")
                write_payload = {
                    "label": label,
                    "host_id": host.host_id,
                    "guest_image": image.guest_image,
                    "base_image_id": image.base_image_id,
                }
                with session_scope() as session:
                    write_event(session, "scale.launch", write_payload)
            except Exception as exc:  # noqa: BLE001
                metrics.inc("scale_launch_failed_total")
                error_payload = {
                    "label": label,
                    "host_id": host.host_id,
                    "guest_image": image.guest_image,
                    "base_image_id": image.base_image_id,
                    "node_agent_url": node_agent.base_url,
                    "error": str(exc),
                }
                if isinstance(exc, RequestFailure):
                    error_payload.update(
                        {
                            "error_type": exc.error_type,
                            "error_detail": exc.detail,
                            "status_code": exc.status_code,
                            "response_text": exc.response_text,
                            "request_url": exc.url,
                        }
                    )
                if isinstance(exc, ProvisioningError):
                    error_payload.update(
                        {
                            "lease_id": exc.lease_id,
                            "vm_id": exc.vm_id,
                            "stage": exc.stage,
                            "provision_detail": exc.detail,
                        }
                    )
                with session_scope() as session:
                    write_event(
                        session,
                        "scale.launch_failed",
                        error_payload,
                    )
                logger.exception(
                    "launch failed label=%s host_id=%s node_agent_url=%s error=%s",
                    label,
                    host.host_id,
                    node_agent.base_url,
                    exc,
                )
            candidates = _eligible_hosts(image, hosts)

        _cooldowns[label] = now + timedelta(seconds=settings.loop_interval_sec * 3)
