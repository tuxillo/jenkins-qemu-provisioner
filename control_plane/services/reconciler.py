from datetime import UTC, datetime

from sqlalchemy import select

from control_plane.clients.jenkins import JenkinsClient, NodeRuntimeStatus
from control_plane.clients.node_agent import NodeAgentClient
from control_plane.config import get_settings
from control_plane.db import session_scope
from control_plane.metrics import metrics
from control_plane.models import Lease, LeaseState
from control_plane.repositories import now_utc, write_event


TERMINAL_RESULTS = {"SUCCESS", "FAILURE", "ABORTED", "UNSTABLE", "NOT_BUILT"}


def terminate_lease(
    lease: Lease, jenkins: JenkinsClient, node_agent: NodeAgentClient, reason: str
) -> None:
    delete_error: str | None = None
    try:
        node_agent.delete_vm(lease.vm_id, reason=reason)
    except Exception as exc:  # noqa: BLE001
        delete_error = str(exc)

    if delete_error is not None:
        with session_scope() as session:
            db_lease = session.get(Lease, lease.lease_id)
            if db_lease:
                db_lease.state = LeaseState.TERMINATING.value
                db_lease.updated_at = now_utc()
                db_lease.last_error = f"{reason}: delete_vm_failed: {delete_error}"
                write_event(
                    session,
                    "lease.terminate_retry",
                    {"reason": reason, "error": delete_error},
                    lease.lease_id,
                )
        return

    try:
        jenkins.delete_node(lease.jenkins_node)
    except Exception:  # noqa: BLE001
        pass
    with session_scope() as session:
        db_lease = session.get(Lease, lease.lease_id)
        if db_lease:
            db_lease.state = LeaseState.TERMINATED.value
            db_lease.updated_at = now_utc()
            write_event(session, "lease.terminated", {"reason": reason}, lease.lease_id)
            metrics.inc("leases_terminated_total")


def reconcile_once(jenkins: JenkinsClient, node_agent_factory) -> None:
    settings = get_settings()
    now = datetime.now(UTC).replace(tzinfo=None)
    with session_scope() as session:
        leases = list(
            session.scalars(
                select(Lease).where(Lease.state.not_in([LeaseState.TERMINATED.value]))
            )
        )

    for lease in leases:
        node_agent = node_agent_factory(lease.host_id or "")

        if lease.state == LeaseState.TERMINATING.value:
            terminate_lease(lease, jenkins, node_agent, reason="terminate_retry")
            continue

        if now > lease.connect_deadline and lease.state in {
            LeaseState.REQUESTED.value,
            LeaseState.PROVISIONING.value,
            LeaseState.BOOTING.value,
        }:
            terminate_lease(lease, jenkins, node_agent, reason="never_connected")
            continue

        if now > lease.ttl_deadline:
            terminate_lease(lease, jenkins, node_agent, reason="ttl_expired")
            continue

        if lease.state in {
            LeaseState.BOOTING.value,
            LeaseState.CONNECTED.value,
            LeaseState.RUNNING.value,
        }:
            try:
                status = jenkins.node_runtime_status(lease.jenkins_node)
                _apply_runtime_transitions(lease, status)
                if lease.state != LeaseState.RUNNING.value:
                    continue

                if not status.connected:
                    if _mark_disconnect_detected(lease):
                        continue
                    if not _disconnect_grace_expired(
                        lease, now, settings.disconnected_grace_sec
                    ):
                        continue
                    offline_for_sec = _offline_for_seconds(lease, now)
                    with session_scope() as session:
                        write_event(
                            session,
                            "lease.disconnected_grace_expired",
                            {
                                "jenkins_node": lease.jenkins_node,
                                "offline_for_sec": offline_for_sec,
                            },
                            lease.lease_id,
                        )
                    terminate_lease(
                        lease, jenkins, node_agent, reason="unexpected_disconnect"
                    )
                    continue

                _clear_disconnect_detected(lease, now)
                bound_build_url = _ensure_bound_build_url(lease, jenkins)
                if not bound_build_url:
                    continue

                if status.busy:
                    _record_unexpected_reuse_if_needed(lease, jenkins, bound_build_url)
                    continue

                if jenkins.is_build_running(bound_build_url):
                    continue

                with session_scope() as session:
                    write_event(
                        session,
                        "lease.job_terminal_detected",
                        {
                            "jenkins_node": lease.jenkins_node,
                            "bound_build_url": bound_build_url,
                        },
                        lease.lease_id,
                    )
                terminate_lease(lease, jenkins, node_agent, reason="job_terminal")
            except Exception:  # noqa: BLE001
                continue


def _apply_runtime_transitions(lease: Lease, status: NodeRuntimeStatus) -> None:
    events: list[str] = []
    target_state = lease.state

    if status.connected and lease.state == LeaseState.BOOTING.value:
        target_state = LeaseState.CONNECTED.value
        events.append("lease.connected")

    if (
        status.connected
        and status.busy
        and target_state
        in {
            LeaseState.BOOTING.value,
            LeaseState.CONNECTED.value,
        }
    ):
        if target_state == LeaseState.BOOTING.value:
            events.append("lease.connected")
        target_state = LeaseState.RUNNING.value
        events.append("lease.running")

    if target_state == lease.state:
        return

    with session_scope() as session:
        db_lease = session.get(Lease, lease.lease_id)
        if not db_lease:
            return
        db_lease.state = target_state
        db_lease.updated_at = now_utc()
        for event_type in events:
            write_event(
                session,
                event_type,
                {"jenkins_node": lease.jenkins_node},
                lease.lease_id,
            )
    lease.state = target_state


def _mark_disconnect_detected(lease: Lease) -> bool:
    if lease.disconnected_at is not None:
        return False

    detected_at = now_utc()
    with session_scope() as session:
        db_lease = session.get(Lease, lease.lease_id)
        if not db_lease or db_lease.disconnected_at is not None:
            return False
        db_lease.disconnected_at = detected_at
        db_lease.updated_at = detected_at
        write_event(
            session,
            "lease.disconnected_detected",
            {"jenkins_node": lease.jenkins_node},
            lease.lease_id,
        )
    lease.disconnected_at = detected_at
    return True


def _clear_disconnect_detected(lease: Lease, now: datetime) -> None:
    if lease.disconnected_at is None:
        return

    offline_for_sec = _offline_for_seconds(lease, now)
    cleared_at = now_utc()
    with session_scope() as session:
        db_lease = session.get(Lease, lease.lease_id)
        if not db_lease:
            return
        db_lease.disconnected_at = None
        db_lease.updated_at = cleared_at
        write_event(
            session,
            "lease.disconnected_recovered",
            {
                "jenkins_node": lease.jenkins_node,
                "offline_for_sec": offline_for_sec,
            },
            lease.lease_id,
        )
    lease.disconnected_at = None


def _disconnect_grace_expired(lease: Lease, now: datetime, grace_sec: int) -> bool:
    if lease.disconnected_at is None:
        return False
    return (now - lease.disconnected_at).total_seconds() >= grace_sec


def _offline_for_seconds(lease: Lease, now: datetime) -> int:
    if lease.disconnected_at is None:
        return 0
    return max(int((now - lease.disconnected_at).total_seconds()), 0)


def _ensure_bound_build_url(lease: Lease, jenkins: JenkinsClient) -> str | None:
    if lease.bound_build_url:
        return lease.bound_build_url

    build_url = jenkins.node_current_build_url(lease.jenkins_node)
    if not build_url:
        return None

    bound_at = now_utc()
    with session_scope() as session:
        db_lease = session.get(Lease, lease.lease_id)
        if not db_lease:
            return None
        if db_lease.bound_build_url:
            lease.bound_build_url = db_lease.bound_build_url
            return db_lease.bound_build_url
        db_lease.bound_build_url = build_url
        db_lease.updated_at = bound_at
        write_event(
            session,
            "lease.job_bound",
            {"jenkins_node": lease.jenkins_node, "build_url": build_url},
            lease.lease_id,
        )

    lease.bound_build_url = build_url
    return build_url


def _record_unexpected_reuse_if_needed(
    lease: Lease, jenkins: JenkinsClient, bound_build_url: str
) -> None:
    current_url = jenkins.node_current_build_url(lease.jenkins_node)
    if not current_url or current_url == bound_build_url:
        return

    with session_scope() as session:
        write_event(
            session,
            "lease.unexpected_reuse",
            {
                "jenkins_node": lease.jenkins_node,
                "bound_build_url": bound_build_url,
                "current_build_url": current_url,
            },
            lease.lease_id,
        )


def teardown_on_terminal_build_result(
    jenkins: JenkinsClient, node_agent_factory, terminal_nodes: list[str]
) -> None:
    if not terminal_nodes:
        return
    with session_scope() as session:
        leases = list(
            session.scalars(select(Lease).where(Lease.jenkins_node.in_(terminal_nodes)))
        )
    for lease in leases:
        node_agent = node_agent_factory(lease.host_id or "")
        terminate_lease(lease, jenkins, node_agent, reason="job_terminal")
