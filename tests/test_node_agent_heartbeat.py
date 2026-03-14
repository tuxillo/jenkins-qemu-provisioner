import os
from types import SimpleNamespace
from typing import Any, cast

from node_agent.config import get_agent_settings
from node_agent.heartbeat import ControlPlaneSession, register_host, send_heartbeat

os.environ["NODE_AGENT_HOST_ID"] = "dev-host"
os.environ["NODE_AGENT_CONTROL_PLANE_URL"] = "http://control-plane:8000"
os.environ["NODE_AGENT_BIND_HOST"] = "0.0.0.0"
os.environ["NODE_AGENT_BIND_PORT"] = "9000"
get_agent_settings.cache_clear()


class _FakeResponse:
    def __init__(self, payload: dict | None = None) -> None:
        self._payload = payload or {}

    def raise_for_status(self) -> None:
        return

    def json(self) -> dict:
        return self._payload


class _FakeClient:
    def __init__(self) -> None:
        self.last_path = ""
        self.last_json: dict | None = None
        self.last_headers: dict | None = None
        self.response_payload: dict = {
            "session_token": "sess-token",
            "session_expires_at": "2099-01-01T00:00:00Z",
        }

    def post(self, path: str, headers: dict, json: dict):
        self.last_path = path
        self.last_headers = headers
        self.last_json = json
        return _FakeResponse(self.response_payload)


class _FakeHostStatsService:
    def __init__(self, io_pressure: float) -> None:
        self._io_pressure = io_pressure

    def latest(self):
        return SimpleNamespace(io_pressure=self._io_pressure)


def _fake_available_images(_settings):
    return [
        SimpleNamespace(
            guest_image="default",
            base_image_id="default",
            source_digest=None,
            cpu_arch="x86_64",
            state="READY",
            model_dump=lambda: {
                "guest_image": "default",
                "base_image_id": "default",
                "source_digest": None,
                "cpu_arch": "x86_64",
                "state": "READY",
            },
        )
    ]


def test_send_heartbeat_reports_free_capacity_after_vm_reservations(monkeypatch):
    monkeypatch.setattr("node_agent.heartbeat.available_images", _fake_available_images)
    monkeypatch.setattr(
        "node_agent.heartbeat.get_host_stats_service",
        lambda: _FakeHostStatsService(0.25),
    )
    monkeypatch.setattr(
        "node_agent.heartbeat.get_agent_settings",
        lambda: SimpleNamespace(
            host_id="dev-host",
            advertise_addr=None,
            bind_host="0.0.0.0",
            bind_port=9000,
            allocatable_vcpu=None,
            allocatable_ram_mb=None,
            os_family="linux",
            os_flavor="linux",
            os_version="test",
            cpu_arch="x86_64",
            qemu_binary="qemu-system-x86_64",
            supported_accels=["kvm", "tcg"],
            qemu_accel="kvm",
        ),
    )
    monkeypatch.setattr("node_agent.heartbeat.os.cpu_count", lambda: 8)
    monkeypatch.setattr("node_agent.heartbeat._detect_total_ram_mb", lambda: 16384)
    monkeypatch.setattr(
        "node_agent.heartbeat.list_vms",
        lambda: [
            {"vm_id": "vm1", "state": "RUNNING", "vcpu": 2, "ram_mb": 2048},
            {"vm_id": "vm2", "state": "BOOTING", "vcpu": 1, "ram_mb": 1024},
            {"vm_id": "vm3", "state": "FAILED", "vcpu": 4, "ram_mb": 4096},
        ],
    )
    client = _FakeClient()
    state = ControlPlaneSession()
    state.session_token = "sess"

    send_heartbeat(cast(Any, client), state)

    assert client.last_path.endswith("/heartbeat")
    assert client.last_json is not None
    assert client.last_json["cpu_free"] == 5
    assert client.last_json["ram_free_mb"] == 13312
    assert client.last_json["io_pressure"] == 0.25
    assert client.last_json["running_vm_ids"] == ["vm1", "vm2"]
    assert client.last_json["available_images"][0]["base_image_id"] == "default"


def test_send_heartbeat_clamps_negative_free_capacity(monkeypatch):
    monkeypatch.setattr("node_agent.heartbeat.available_images", _fake_available_images)
    monkeypatch.setattr(
        "node_agent.heartbeat.get_host_stats_service",
        lambda: _FakeHostStatsService(0.0),
    )
    monkeypatch.setattr(
        "node_agent.heartbeat.get_agent_settings",
        lambda: SimpleNamespace(
            host_id="dev-host",
            advertise_addr=None,
            bind_host="0.0.0.0",
            bind_port=9000,
            allocatable_vcpu=None,
            allocatable_ram_mb=None,
            os_family="linux",
            os_flavor="linux",
            os_version="test",
            cpu_arch="x86_64",
            qemu_binary="qemu-system-x86_64",
            supported_accels=["kvm", "tcg"],
            qemu_accel="kvm",
        ),
    )
    monkeypatch.setattr("node_agent.heartbeat.os.cpu_count", lambda: 2)
    monkeypatch.setattr("node_agent.heartbeat._detect_total_ram_mb", lambda: 1024)
    monkeypatch.setattr(
        "node_agent.heartbeat.list_vms",
        lambda: [
            {"vm_id": "vm1", "state": "RUNNING", "vcpu": 4, "ram_mb": 4096},
            {"vm_id": "vm2", "state": "PROVISIONING", "vcpu": "bad", "ram_mb": None},
        ],
    )
    client = _FakeClient()
    state = ControlPlaneSession()
    state.session_token = "sess"

    send_heartbeat(cast(Any, client), state)

    assert client.last_json is not None
    assert client.last_json["cpu_free"] == 0
    assert client.last_json["ram_free_mb"] == 0


def test_send_heartbeat_uses_allocatable_capacity_when_configured(monkeypatch):
    monkeypatch.setattr("node_agent.heartbeat.available_images", _fake_available_images)
    monkeypatch.setattr(
        "node_agent.heartbeat.get_host_stats_service",
        lambda: _FakeHostStatsService(0.1),
    )
    monkeypatch.setattr(
        "node_agent.heartbeat.get_agent_settings",
        lambda: SimpleNamespace(
            host_id="dev-host",
            advertise_addr=None,
            bind_host="0.0.0.0",
            bind_port=9000,
            allocatable_vcpu=6,
            allocatable_ram_mb=12288,
            os_family="linux",
            os_flavor="linux",
            os_version="test",
            cpu_arch="x86_64",
            qemu_binary="qemu-system-x86_64",
            supported_accels=["kvm", "tcg"],
            qemu_accel="kvm",
        ),
    )
    monkeypatch.setattr("node_agent.heartbeat.os.cpu_count", lambda: 8)
    monkeypatch.setattr("node_agent.heartbeat._detect_total_ram_mb", lambda: 16384)
    monkeypatch.setattr(
        "node_agent.heartbeat.list_vms",
        lambda: [
            {"vm_id": "vm1", "state": "RUNNING", "vcpu": 2, "ram_mb": 2048},
            {"vm_id": "vm2", "state": "BOOTING", "vcpu": 1, "ram_mb": 1024},
        ],
    )
    client = _FakeClient()
    state = ControlPlaneSession()
    state.session_token = "sess"

    send_heartbeat(cast(Any, client), state)

    assert client.last_json is not None
    assert client.last_json["cpu_total"] == 8
    assert client.last_json["cpu_allocatable"] == 6
    assert client.last_json["cpu_free"] == 3
    assert client.last_json["ram_total_mb"] == 16384
    assert client.last_json["ram_allocatable_mb"] == 12288
    assert client.last_json["ram_free_mb"] == 9216


def test_register_host_falls_back_to_physical_totals_when_allocatable_unset(
    monkeypatch,
):
    monkeypatch.setattr("node_agent.heartbeat.available_images", _fake_available_images)
    monkeypatch.setattr(
        "node_agent.heartbeat.get_agent_settings",
        lambda: SimpleNamespace(
            host_id="dev-host",
            bootstrap_token="bootstrap-token",
            advertise_addr="10.0.0.10:9000",
            bind_host="0.0.0.0",
            bind_port=9000,
            allocatable_vcpu=None,
            allocatable_ram_mb=None,
            os_family="linux",
            os_flavor="linux",
            os_version="test",
            cpu_arch="x86_64",
            qemu_binary="qemu-system-x86_64",
            supported_accels=["kvm", "tcg"],
            qemu_accel="kvm",
        ),
    )
    monkeypatch.setattr("node_agent.heartbeat.os.cpu_count", lambda: 8)
    monkeypatch.setattr("node_agent.heartbeat._detect_total_ram_mb", lambda: 16384)
    monkeypatch.setattr("node_agent.heartbeat.list_vms", lambda: [])
    client = _FakeClient()
    state = ControlPlaneSession()

    register_host(cast(Any, client), state)

    assert client.last_json is not None
    assert client.last_json["cpu_total"] == 8
    assert client.last_json["cpu_allocatable"] == 8
    assert client.last_json["ram_total_mb"] == 16384
    assert client.last_json["ram_allocatable_mb"] == 16384
    assert client.last_json["available_images"][0]["guest_image"] == "default"
