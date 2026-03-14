from datetime import UTC, datetime, timedelta
import json
from types import SimpleNamespace
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


def _available_images_json(
    *, guest_image: str = "default", base_image_id: str = "default"
) -> str:
    return json.dumps(
        [
            {
                "guest_image": guest_image,
                "base_image_id": base_image_id,
                "source_digest": None,
                "cpu_arch": "x86_64",
                "state": "READY",
            }
        ]
    )


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
            cpu_allocatable=16,
            cpu_free=16,
            ram_total_mb=32768,
            ram_allocatable_mb=32768,
            ram_free_mb=32768,
            io_pressure=0.1,
            last_seen=datetime.now(UTC).replace(tzinfo=None),
            selected_accel="kvm",
            supported_accels='["kvm","tcg"]',
            available_images_json=_available_images_json(),
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
            cpu_allocatable=16,
            cpu_free=16,
            ram_total_mb=32768,
            ram_allocatable_mb=32768,
            ram_free_mb=32768,
            io_pressure=0.1,
            last_seen=now,
            os_family="bsd",
            os_flavor="dragonflybsd",
            selected_accel="nvmm",
            supported_accels='["nvmm","tcg"]',
            available_images_json=_available_images_json(),
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


def test_scaler_respects_allocatable_budget_during_same_tick_burst(monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Host(
            host_id="h1",
            enabled=True,
            cpu_total=16,
            cpu_allocatable=2,
            cpu_free=2,
            ram_total_mb=32768,
            ram_allocatable_mb=4096,
            ram_free_mb=4096,
            io_pressure=0.1,
            last_seen=now,
            os_family="linux",
            os_flavor="linux",
            selected_accel="kvm",
            supported_accels='["kvm","tcg"]',
            available_images_json=_available_images_json(),
        )
    )
    db.commit()
    db.close()

    calls = []

    def fake_provision_one(**kwargs):
        calls.append(kwargs)
        return f"lease-{len(calls)}"

    monkeypatch.setattr(scaler, "provision_one", fake_provision_one)
    monkeypatch.setattr(
        scaler,
        "get_settings",
        lambda: SimpleNamespace(
            host_stale_timeout_sec=20,
            label_max_inflight=5,
            global_max_vms=100,
            label_burst=3,
            loop_interval_sec=5,
        ),
    )

    jenkins = FakeJenkins({"linux-small": 3})

    def node_agent_factory(_host_id):
        return object()

    scaler.scale_once(cast(Any, jenkins), node_agent_factory)

    assert len(calls) == 1
    assert calls[0]["host_id"] == "h1"


def test_scaler_prefers_warm_cached_image_hosts(monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add_all(
        [
            Host(
                host_id="cold-host",
                enabled=True,
                cpu_total=16,
                cpu_allocatable=16,
                cpu_free=16,
                ram_total_mb=32768,
                ram_allocatable_mb=32768,
                ram_free_mb=32768,
                io_pressure=0.1,
                last_seen=now,
                selected_accel="kvm",
                supported_accels='["kvm","tcg"]',
            ),
            Host(
                host_id="warm-host",
                enabled=True,
                cpu_total=16,
                cpu_allocatable=16,
                cpu_free=16,
                ram_total_mb=32768,
                ram_allocatable_mb=32768,
                ram_free_mb=32768,
                io_pressure=0.3,
                last_seen=now,
                selected_accel="kvm",
                supported_accels='["kvm","tcg"]',
                available_images_json=_available_images_json(
                    guest_image="debian-12-ci",
                    base_image_id="debian-12-20260301",
                ),
            ),
        ]
    )
    db.commit()
    db.close()

    calls = []

    def fake_provision_one(**kwargs):
        calls.append(kwargs)
        return "lease-1"

    monkeypatch.setattr(scaler, "provision_one", fake_provision_one)
    monkeypatch.setattr(
        scaler,
        "resolve_label_policy",
        lambda _label: SimpleNamespace(
            guest_image="debian-12-ci",
            profile="small",
            required_accel="kvm",
            required_cpu_arch=None,
        ),
    )
    monkeypatch.setattr(
        scaler,
        "resolve_image_catalog_entry",
        lambda _guest_image: SimpleNamespace(
            base_image_id="debian-12-20260301",
            os_family="linux",
            os_flavor="debian",
            os_version="12",
            cpu_arch="x86_64",
            source_kind="remote_cache",
            source_url="https://example.invalid/debian.qcow2",
            source_digest="sha256:abc",
            format="qcow2",
            cache_policy="prefer_warm",
        ),
    )

    scaler.scale_once(
        cast(Any, FakeJenkins({"linux-medium": 1})), lambda _host_id: object()
    )

    assert len(calls) == 1
    assert calls[0]["host_id"] == "warm-host"


def test_scaler_skips_unknown_label_policy(monkeypatch):
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    db.add(
        Host(
            host_id="h1",
            enabled=True,
            cpu_total=16,
            cpu_allocatable=16,
            cpu_free=16,
            ram_total_mb=32768,
            ram_allocatable_mb=32768,
            ram_free_mb=32768,
            io_pressure=0.1,
            last_seen=now,
            selected_accel="kvm",
            supported_accels='["kvm","tcg"]',
            available_images_json=_available_images_json(),
        )
    )
    db.commit()
    db.close()

    calls = []

    def fake_provision_one(**kwargs):
        calls.append(kwargs)
        return "lease-1"

    monkeypatch.setattr(scaler, "provision_one", fake_provision_one)
    monkeypatch.setattr(scaler, "resolve_label_policy", lambda _label: None)

    scaler.scale_once(
        cast(Any, FakeJenkins({"unknown-label": 1})), lambda _host_id: object()
    )

    assert calls == []
