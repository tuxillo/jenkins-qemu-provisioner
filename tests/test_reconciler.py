from datetime import UTC, datetime, timedelta

from control_plane.db import Base, SessionLocal, engine
from control_plane.models import Lease, LeaseState
from control_plane.services.reconciler import reconcile_once


class FakeJenkins:
    def __init__(self):
        self.deleted = []

    def delete_node(self, node_name: str):
        self.deleted.append(node_name)

    def is_node_connected(self, _node_name: str) -> bool:
        return False


class FakeNodeAgent:
    def __init__(self):
        self.deleted = []

    def delete_vm(self, vm_id: str, reason: str):
        self.deleted.append((vm_id, reason))


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_reconcile_cleans_never_connected_expired_deadline():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l1",
            vm_id="vm1",
            label="linux",
            jenkins_node="n1",
            state=LeaseState.BOOTING.value,
            host_id="h1",
            created_at=now,
            updated_at=now,
            connect_deadline=now - timedelta(seconds=5),
            ttl_deadline=now + timedelta(hours=1),
        )
    )
    db.commit()
    db.close()

    jenkins = FakeJenkins()
    node_agent = FakeNodeAgent()

    reconcile_once(jenkins, lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l1")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.TERMINATED.value
    assert jenkins.deleted == ["n1"]
    assert node_agent.deleted[0][0] == "vm1"
