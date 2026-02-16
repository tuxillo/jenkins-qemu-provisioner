from datetime import UTC, datetime

from sqlalchemy import select

from control_plane.clients.jenkins import JenkinsClient
from control_plane.clients.node_agent import NodeAgentClient
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
                connected = jenkins.is_node_connected(lease.jenkins_node)
                if not connected and lease.state == LeaseState.RUNNING.value:
                    terminate_lease(
                        lease, jenkins, node_agent, reason="unexpected_disconnect"
                    )
            except Exception:  # noqa: BLE001
                continue


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
