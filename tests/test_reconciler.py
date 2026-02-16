from datetime import UTC, datetime, timedelta
from typing import Any, cast

from control_plane.db import Base, SessionLocal, engine
from control_plane.models import Lease, LeaseState
from control_plane.services.reconciler import reconcile_once


class FakeJenkins:
    def __init__(self, *, connected: bool = False, busy: bool = False):
        self.deleted = []
        self.connected = connected
        self.busy = busy

    def delete_node(self, node_name: str):
        self.deleted.append(node_name)

    def node_runtime_status(self, _node_name: str):
        return type(
            "Status",
            (),
            {"connected": self.connected, "busy": self.busy},
        )()


class FakeNodeAgent:
    def __init__(self, fail_delete: bool = False):
        self.deleted = []
        self.fail_delete = fail_delete

    def delete_vm(self, vm_id: str, reason: str):
        if self.fail_delete:
            raise RuntimeError("node-agent unavailable")
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

    jenkins = FakeJenkins(connected=False, busy=False)
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l1")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.TERMINATED.value
    assert jenkins.deleted == ["n1"]
    assert node_agent.deleted[0][0] == "vm1"


def test_reconcile_keeps_lease_terminating_when_delete_fails():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l2",
            vm_id="vm2",
            label="linux",
            jenkins_node="n2",
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

    jenkins = FakeJenkins(connected=False, busy=False)
    node_agent = FakeNodeAgent(fail_delete=True)

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l2")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.TERMINATING.value
    assert "delete_vm_failed" in (lease.last_error or "")
    assert jenkins.deleted == []


def test_reconcile_retries_terminating_leases():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l3",
            vm_id="vm3",
            label="linux",
            jenkins_node="n3",
            state=LeaseState.TERMINATING.value,
            host_id="h1",
            created_at=now,
            updated_at=now,
            connect_deadline=now + timedelta(minutes=1),
            ttl_deadline=now + timedelta(hours=1),
        )
    )
    db.commit()
    db.close()

    jenkins = FakeJenkins(connected=False, busy=False)
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l3")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.TERMINATED.value
    assert node_agent.deleted == [("vm3", "terminate_retry")]


def test_reconcile_marks_booting_connected_when_node_online_idle():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l4",
            vm_id="vm4",
            label="linux",
            jenkins_node="n4",
            state=LeaseState.BOOTING.value,
            host_id="h1",
            created_at=now,
            updated_at=now,
            connect_deadline=now + timedelta(minutes=1),
            ttl_deadline=now + timedelta(hours=1),
        )
    )
    db.commit()
    db.close()

    jenkins = FakeJenkins(connected=True, busy=False)
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l4")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.CONNECTED.value


def test_reconcile_marks_connected_running_when_node_becomes_busy():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l5",
            vm_id="vm5",
            label="linux",
            jenkins_node="n5",
            state=LeaseState.CONNECTED.value,
            host_id="h1",
            created_at=now,
            updated_at=now,
            connect_deadline=now + timedelta(minutes=1),
            ttl_deadline=now + timedelta(hours=1),
        )
    )
    db.commit()
    db.close()

    jenkins = FakeJenkins(connected=True, busy=True)
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l5")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.RUNNING.value


def test_reconcile_terminates_running_lease_when_job_becomes_idle():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l6",
            vm_id="vm6",
            label="linux",
            jenkins_node="n6",
            state=LeaseState.RUNNING.value,
            host_id="h1",
            created_at=now,
            updated_at=now,
            connect_deadline=now + timedelta(minutes=1),
            ttl_deadline=now + timedelta(hours=1),
        )
    )
    db.commit()
    db.close()

    jenkins = FakeJenkins(connected=True, busy=False)
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l6")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.TERMINATED.value
    assert node_agent.deleted == [("vm6", "job_terminal")]
