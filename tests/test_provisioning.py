from datetime import UTC, datetime, timedelta
import base64
from typing import Any, cast

from control_plane.db import Base, SessionLocal, engine
from control_plane.models import Lease, LeaseState
from control_plane.services.provisioning import (
    build_jenkins_cloud_init_user_data,
    normalize_node_label,
    provision_one,
)


class FakeJenkins:
    def __init__(self, fail_create=False):
        self.created = []
        self.deleted = []
        self.fail_create = fail_create

    def create_ephemeral_node(self, node_name: str, label: str):
        if self.fail_create:
            raise RuntimeError("create failed")
        self.created.append((node_name, label))

    def get_inbound_secret(self, _node_name: str) -> str:
        return "secret"

    def delete_node(self, node_name: str):
        self.deleted.append(node_name)


class FakeNodeAgent:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def ensure_vm(self, vm_id: str, payload: dict):
        if self.fail:
            raise RuntimeError("launch failed")
        self.calls.append((vm_id, payload))
        return {"vm_id": vm_id, "state": "BOOTING"}


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_provision_success_transitions_to_booting():
    node_agent = FakeNodeAgent()
    lease_id = provision_one(
        "linux-small", "host1", cast(Any, FakeJenkins()), cast(Any, node_agent)
    )
    db = SessionLocal()
    lease = db.get(Lease, lease_id)
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.BOOTING.value
    assert node_agent.calls
    payload = node_agent.calls[0][1]
    user_data = base64.b64decode(payload["cloud_init_user_data_b64"]).decode("utf-8")
    assert "start-jenkins-inbound-agent.sh" in user_data
    assert "JENKINS_JNLP_SECRET='secret'" in user_data


def test_cloud_init_user_data_contains_inbound_bootstrap_script():
    user_data = build_jenkins_cloud_init_user_data(
        jenkins_url="http://jenkins:8080",
        jenkins_node_name="ephemeral-node-1",
        jnlp_secret="abc123",
    )
    assert user_data.startswith("#cloud-config")
    assert 'curl -fsSL "$JENKINS_URL/jnlpJars/agent.jar"' in user_data
    assert '-name "$JENKINS_NODE_NAME"' in user_data
    assert "JENKINS_JNLP_SECRET='abc123'" in user_data
    assert "/usr/local/etc/jenkins-qemu/jenkins-agent.env" in user_data
    assert "missing jenkins agent env file" in user_data
    assert "[ /usr/bin/env, bash, -c," in user_data
    assert " -lc, " not in user_data
    assert 'echo "$line" | tee -a "$BOOTSTRAP_LOG"' in user_data
    assert "printf '%s" not in user_data


def test_normalize_node_label_strips_expression_operators():
    assert normalize_node_label("linux-kvm || dragonflybsd-nvmm") == (
        "linux-kvm dragonflybsd-nvmm"
    )
    assert normalize_node_label("(linux && x86_64) || (dragonflybsd && nvmm)") == (
        "linux x86_64 dragonflybsd nvmm"
    )


def test_provision_failure_marks_failed_and_rolls_back_node():
    jenkins = FakeJenkins()
    node_agent = FakeNodeAgent(fail=True)
    try:
        provision_one("linux-small", "host1", cast(Any, jenkins), cast(Any, node_agent))
        raise AssertionError("expected failure")
    except RuntimeError:
        pass

    db = SessionLocal()
    leases = db.query(Lease).all()
    db.close()
    assert leases
    assert leases[0].state == LeaseState.FAILED.value
    assert len(jenkins.deleted) == 1


def test_provision_idempotent_for_existing_booting_lease():
    db = SessionLocal()
    lease = Lease(
        lease_id="existinglease",
        vm_id="vm-existingleas",
        label="linux-small",
        jenkins_node="ephemeral-existingleas",
        state=LeaseState.BOOTING.value,
        host_id="host1",
        created_at=datetime.now(UTC).replace(tzinfo=None),
        updated_at=datetime.now(UTC).replace(tzinfo=None),
        connect_deadline=(datetime.now(UTC) + timedelta(minutes=1)).replace(
            tzinfo=None
        ),
        ttl_deadline=(datetime.now(UTC) + timedelta(hours=1)).replace(tzinfo=None),
    )
    db.add(lease)
    db.commit()
    db.close()

    jenkins = FakeJenkins()
    node_agent = FakeNodeAgent()
    out = provision_one(
        "linux-small",
        "host1",
        cast(Any, jenkins),
        cast(Any, node_agent),
        lease_id="existinglease",
    )
    assert out == "existinglease"
    assert not node_agent.calls
