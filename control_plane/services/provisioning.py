import base64
import uuid
from datetime import UTC, datetime, timedelta

from control_plane.clients.jenkins import JenkinsClient
from control_plane.clients.node_agent import NodeAgentClient
from control_plane.config import get_settings
from control_plane.db import session_scope
from control_plane.models import Lease, LeaseState
from control_plane.repositories import now_utc, write_event


NODE_PROFILES = {
    "small": {"vcpu": 2, "ram_mb": 4096, "disk_gb": 40},
    "medium": {"vcpu": 4, "ram_mb": 8192, "disk_gb": 80},
    "large": {"vcpu": 8, "ram_mb": 16384, "disk_gb": 120},
}


def choose_profile(label: str) -> str:
    if "large" in label:
        return "large"
    if "medium" in label:
        return "medium"
    return "small"


def create_lease(label: str) -> Lease:
    settings = get_settings()
    now = datetime.now(UTC).replace(tzinfo=None)
    lease_id = uuid.uuid4().hex
    vm_id = f"vm-{lease_id[:12]}"
    node_name = f"ephemeral-{lease_id[:12]}"
    return Lease(
        lease_id=lease_id,
        vm_id=vm_id,
        label=label,
        jenkins_node=node_name,
        state=LeaseState.REQUESTED.value,
        created_at=now,
        updated_at=now,
        connect_deadline=now + timedelta(seconds=settings.connect_deadline_sec),
        ttl_deadline=now + timedelta(seconds=settings.vm_ttl_sec),
    )


def provision_one(
    label: str,
    host_id: str,
    jenkins: JenkinsClient,
    node_agent: NodeAgentClient,
    base_image_id: str = "default",
    lease_id: str | None = None,
) -> str:
    settings = get_settings()
    lease = create_lease(label)
    if lease_id:
        lease.lease_id = lease_id
        lease.vm_id = f"vm-{lease_id[:12]}"
        lease.jenkins_node = f"ephemeral-{lease_id[:12]}"
    profile = NODE_PROFILES[choose_profile(label)]
    with session_scope() as session:
        existing = session.get(Lease, lease.lease_id)
        if existing and existing.state in {
            LeaseState.BOOTING.value,
            LeaseState.CONNECTED.value,
            LeaseState.RUNNING.value,
            LeaseState.TERMINATING.value,
            LeaseState.TERMINATED.value,
        }:
            return existing.lease_id

        lease.host_id = host_id
        persisted_lease = session.merge(lease)
        session.flush()
        write_event(
            session,
            "lease.created",
            {"label": label, "host_id": host_id},
            persisted_lease.lease_id,
        )

    try:
        jenkins.create_ephemeral_node(lease.jenkins_node, label)
        secret = jenkins.get_inbound_secret(lease.jenkins_node)
        payload = {
            "vm_id": lease.vm_id,
            "label": label,
            "base_image_id": base_image_id,
            "overlay_path": f"/var/lib/jenkins-qemu/{lease.vm_id}.qcow2",
            "vcpu": profile["vcpu"],
            "ram_mb": profile["ram_mb"],
            "disk_gb": profile["disk_gb"],
            "lease_expires_at": lease.ttl_deadline.isoformat(),
            "connect_deadline": lease.connect_deadline.isoformat(),
            "jenkins_url": settings.jenkins_url,
            "jenkins_node_name": lease.jenkins_node,
            "jnlp_secret": secret,
            "cloud_init_user_data_b64": base64.b64encode(b"#cloud-config\n").decode(
                "ascii"
            ),
            "metadata": {"lease_id": lease.lease_id},
        }
        node_agent.ensure_vm(lease.vm_id, payload)
        with session_scope() as session:
            db_lease = session.get(Lease, lease.lease_id)
            if db_lease:
                db_lease.state = LeaseState.BOOTING.value
                db_lease.updated_at = now_utc()
                write_event(
                    session, "lease.booting", {"host_id": host_id}, lease.lease_id
                )
        return lease.lease_id
    except Exception as exc:  # noqa: BLE001
        with session_scope() as session:
            db_lease = session.get(Lease, lease.lease_id)
            if db_lease:
                db_lease.state = LeaseState.FAILED.value
                db_lease.last_error = str(exc)
                db_lease.updated_at = now_utc()
                write_event(
                    session, "lease.failed", {"error": str(exc)}, lease.lease_id
                )
        try:
            jenkins.delete_node(lease.jenkins_node)
        except Exception:  # noqa: BLE001
            pass
        raise
