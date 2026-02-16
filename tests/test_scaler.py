from datetime import UTC, datetime, timedelta
from typing import Any, cast

from control_plane.db import Base, SessionLocal, engine
from control_plane.models import Host, Lease, LeaseState
from control_plane.services import scaler


class FakeJenkins:
    class Snapshot:
        def __init__(self, queued_by_label, queued_by_node=None):
            self.queued_by_label = queued_by_label
            self.queued_by_node = queued_by_node or {}

    def __init__(self, queued_by_label, queued_by_node=None):
        self._queued = queued_by_label
        self._queued_by_node = queued_by_node or {}

    def queue_snapshot(self):
        return self.Snapshot(self._queued, self._queued_by_node)


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    scaler._cooldowns.clear()
    scaler._diag_throttle.clear()


def test_scaler_launches_with_caps(monkeypatch):
    db = SessionLocal()
    db.add(
        Host(
            host_id="h1",
            enabled=True,
            cpu_total=16,
            cpu_free=16,
            ram_total_mb=32768,
            ram_free_mb=32768,
            io_pressure=0.1,
            last_seen=datetime.now(UTC).replace(tzinfo=None),
        )
    )
    db.commit()
    db.close()

    calls = []

    def fake_provision_one(**kwargs):
        calls.append(kwargs)
        return "lease-1"

    monkeypatch.setattr(scaler, "provision_one", fake_provision_one)

    jenkins = FakeJenkins({"linux-medium": 10})

    def node_agent_factory(_host_id):
        return object()

    scaler.scale_once(cast(Any, jenkins), node_agent_factory)
    assert len(calls) >= 1
    assert all(call["label"] == "linux-medium" for call in calls)


def test_scaler_maps_node_wait_queue_to_active_lease_label(monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Host(
            host_id="h1",
            enabled=True,
            cpu_total=16,
            cpu_free=16,
            ram_total_mb=32768,
            ram_free_mb=32768,
            io_pressure=0.1,
            last_seen=now,
            os_family="bsd",
            os_flavor="dragonflybsd",
            selected_accel="nvmm",
            supported_accels='["nvmm","tcg"]',
        )
    )
    db.add(
        Lease(
            lease_id="l1",
            vm_id="vm1",
            label="dragonflybsd-nvmm",
            jenkins_node="ephemeral-abc",
            state=LeaseState.RUNNING.value,
            host_id="h1",
            created_at=now,
            updated_at=now,
            connect_deadline=now + timedelta(minutes=5),
            ttl_deadline=now + timedelta(hours=1),
        )
    )
    db.commit()
    db.close()

    calls = []

    def fake_provision_one(**kwargs):
        calls.append(kwargs)
        return "lease-2"

    monkeypatch.setattr(scaler, "provision_one", fake_provision_one)

    jenkins = FakeJenkins({}, {"ephemeral-abc": 1})

    def node_agent_factory(_host_id):
        return object()

    scaler.scale_once(cast(Any, jenkins), node_agent_factory)

    assert len(calls) == 1
    assert calls[0]["label"] == "dragonflybsd-nvmm"
