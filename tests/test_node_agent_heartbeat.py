import os

from node_agent.config import get_agent_settings
from node_agent.heartbeat import ControlPlaneSession, send_heartbeat

os.environ["NODE_AGENT_HOST_ID"] = "dev-host"
os.environ["NODE_AGENT_CONTROL_PLANE_URL"] = "http://control-plane:8000"
os.environ["NODE_AGENT_BIND_HOST"] = "0.0.0.0"
os.environ["NODE_AGENT_BIND_PORT"] = "9000"
get_agent_settings.cache_clear()


class _FakeResponse:
    def raise_for_status(self) -> None:
        return


class _FakeClient:
    def __init__(self) -> None:
        self.last_path = ""
        self.last_json: dict | None = None
        self.last_headers: dict | None = None

    def post(self, path: str, headers: dict, json: dict):
        self.last_path = path
        self.last_headers = headers
        self.last_json = json
        return _FakeResponse()


def test_send_heartbeat_reports_free_capacity_after_vm_reservations(monkeypatch):
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

    send_heartbeat(client, state)

    assert client.last_path.endswith("/heartbeat")
    assert client.last_json is not None
    assert client.last_json["cpu_free"] == 5
    assert client.last_json["ram_free_mb"] == 13312
    assert client.last_json["running_vm_ids"] == ["vm1", "vm2"]


def test_send_heartbeat_clamps_negative_free_capacity(monkeypatch):
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

    send_heartbeat(client, state)

    assert client.last_json is not None
    assert client.last_json["cpu_free"] == 0
    assert client.last_json["ram_free_mb"] == 0
