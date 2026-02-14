from datetime import UTC, datetime

from control_plane.db import Base, SessionLocal, engine
from control_plane.models import Host
from control_plane.services import scaler


class FakeJenkins:
    class Snapshot:
        def __init__(self, queued_by_label):
            self.queued_by_label = queued_by_label

    def __init__(self, queued_by_label):
        self._queued = queued_by_label

    def queue_snapshot(self):
        return self.Snapshot(self._queued)


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


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

    scaler.scale_once(jenkins, node_agent_factory)
    assert len(calls) >= 1
    assert all(call["label"] == "linux-medium" for call in calls)
