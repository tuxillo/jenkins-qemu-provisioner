from datetime import UTC, datetime, timedelta

from control_plane.db import Base, SessionLocal, engine
from control_plane.models import Lease, LeaseState
from control_plane.services.provisioning import provision_one


class FakeJenkins:
    def __init__(self, fail_create=False):
        self.created = []
        self.deleted = []
        self.fail_create = fail_create

    def create_ephemeral_node(self, node_name: str, _label: str):
        if self.fail_create:
            raise RuntimeError("create failed")
        self.created.append(node_name)

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
    lease_id = provision_one("linux-small", "host1", FakeJenkins(), FakeNodeAgent())
    db = SessionLocal()
    lease = db.get(Lease, lease_id)
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.BOOTING.value


def test_provision_failure_marks_failed_and_rolls_back_node():
    jenkins = FakeJenkins()
    node_agent = FakeNodeAgent(fail=True)
    try:
        provision_one("linux-small", "host1", jenkins, node_agent)
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
        "linux-small", "host1", jenkins, node_agent, lease_id="existinglease"
    )
    assert out == "existinglease"
    assert not node_agent.calls
