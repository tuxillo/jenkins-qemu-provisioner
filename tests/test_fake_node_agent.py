import os

from fastapi.testclient import TestClient

from fake_node_agent.config import get_settings


os.environ["FAKE_NODE_AGENT_ENABLE_HEARTBEAT_WORKER"] = "false"
get_settings.cache_clear()

from fake_node_agent.app import app  # noqa: E402


def test_fake_agent_vm_lifecycle() -> None:
    client = TestClient(app)
    payload = {
        "vm_id": "vm-a",
        "label": "linux-kvm",
        "lease_expires_at": "2099-01-01T00:00:00+00:00",
        "jenkins_node_name": "node-a",
    }
    put = client.put("/v1/vms/vm-a", json=payload)
    assert put.status_code == 200
    get = client.get("/v1/vms/vm-a")
    assert get.status_code == 200
    assert get.json()["state"] == "RUNNING"
    delete = client.delete("/v1/vms/vm-a")
    assert delete.status_code == 200
    assert delete.json()["state"] == "TERMINATED"


def test_fake_agent_capacity() -> None:
    client = TestClient(app)
    cap = client.get("/v1/capacity")
    assert cap.status_code == 200
    body = cap.json()
    assert "host_id" in body
    assert "selected_accel" in body
