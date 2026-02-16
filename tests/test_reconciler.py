from datetime import UTC, datetime, timedelta
from typing import Any, cast

from control_plane.db import Base, SessionLocal, engine
from control_plane.models import Lease, LeaseState
from control_plane.services.reconciler import reconcile_once


class FakeJenkins:
    def __init__(
        self,
        *,
        connected: bool = False,
        busy: bool = False,
        current_build_url: str | None = None,
        build_running: bool = False,
    ):
        self.deleted = []
        self.connected = connected
        self.busy = busy
        self.current_build_url = current_build_url
        self.build_running = build_running

    def delete_node(self, node_name: str):
        self.deleted.append(node_name)

    def node_runtime_status(self, _node_name: str):
        return type(
            "Status",
            (),
            {"connected": self.connected, "busy": self.busy},
        )()

    def node_current_build_url(self, _node_name: str) -> str | None:
        return self.current_build_url

    def is_build_running(self, _build_url: str) -> bool:
        return self.build_running


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

    jenkins = FakeJenkins(
        connected=True,
        busy=True,
        current_build_url="http://jenkins:8080/job/fake/5/",
        build_running=True,
    )
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l5")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.RUNNING.value
    assert lease.bound_build_url == "http://jenkins:8080/job/fake/5/"


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
            bound_build_url="http://jenkins:8080/job/fake/6/",
        )
    )
    db.commit()
    db.close()

    jenkins = FakeJenkins(
        connected=True,
        busy=False,
        current_build_url=None,
        build_running=False,
    )
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l6")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.TERMINATED.value
    assert node_agent.deleted == [("vm6", "job_terminal")]


def test_reconcile_does_not_kill_running_lease_on_first_disconnect_tick():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l7",
            vm_id="vm7",
            label="linux",
            jenkins_node="n7",
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

    jenkins = FakeJenkins(connected=False, busy=False)
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l7")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.RUNNING.value
    assert lease.disconnected_at is not None
    assert node_agent.deleted == []


def test_reconcile_keeps_running_lease_when_bound_build_still_running():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l10",
            vm_id="vm10",
            label="linux",
            jenkins_node="n10",
            state=LeaseState.RUNNING.value,
            host_id="h1",
            created_at=now,
            updated_at=now,
            connect_deadline=now + timedelta(minutes=1),
            ttl_deadline=now + timedelta(hours=1),
            bound_build_url="http://jenkins:8080/job/fake/10/",
        )
    )
    db.commit()
    db.close()

    jenkins = FakeJenkins(
        connected=True,
        busy=False,
        current_build_url=None,
        build_running=True,
    )
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l10")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.RUNNING.value
    assert node_agent.deleted == []


def test_reconcile_terminates_running_lease_after_disconnect_grace_expires():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l8",
            vm_id="vm8",
            label="linux",
            jenkins_node="n8",
            state=LeaseState.RUNNING.value,
            host_id="h1",
            created_at=now,
            updated_at=now,
            connect_deadline=now + timedelta(minutes=1),
            ttl_deadline=now + timedelta(hours=1),
            disconnected_at=now - timedelta(seconds=120),
        )
    )
    db.commit()
    db.close()

    jenkins = FakeJenkins(connected=False, busy=False)
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l8")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.TERMINATED.value
    assert node_agent.deleted == [("vm8", "unexpected_disconnect")]


def test_reconcile_clears_disconnect_marker_after_recovery():
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Lease(
            lease_id="l9",
            vm_id="vm9",
            label="linux",
            jenkins_node="n9",
            state=LeaseState.RUNNING.value,
            host_id="h1",
            created_at=now,
            updated_at=now,
            connect_deadline=now + timedelta(minutes=1),
            ttl_deadline=now + timedelta(hours=1),
            disconnected_at=now - timedelta(seconds=10),
        )
    )
    db.commit()
    db.close()

    jenkins = FakeJenkins(connected=True, busy=True)
    node_agent = FakeNodeAgent()

    reconcile_once(cast(Any, jenkins), lambda _host_id: node_agent)

    db = SessionLocal()
    lease = db.get(Lease, "l9")
    db.close()
    assert lease is not None
    assert lease.state == LeaseState.RUNNING.value
    assert lease.disconnected_at is None
    assert node_agent.deleted == []
