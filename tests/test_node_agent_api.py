import base64
import os
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from node_agent.config import get_agent_settings

os.environ["NODE_AGENT_DISABLE_WORKERS"] = "true"
os.environ["NODE_AGENT_DRY_RUN"] = "true"
os.environ["NODE_AGENT_OVERLAY_DIR"] = "/tmp/node-agent-overlays"
os.environ["NODE_AGENT_CLOUD_INIT_DIR"] = "/tmp/node-agent-cloud-init"
os.environ["NODE_AGENT_BASE_IMAGE_DIR"] = "/tmp/node-agent-base"
os.environ["NODE_AGENT_STATE_DB_PATH"] = "/tmp/node-agent.db"
get_agent_settings.cache_clear()

from node_agent.main import app  # noqa: E402
from node_agent.state import initialize_state  # noqa: E402


def setup_function() -> None:
    db_path = os.environ["NODE_AGENT_STATE_DB_PATH"]
    if os.path.exists(db_path):
        os.remove(db_path)
    initialize_state()


def _payload(vm_id: str) -> dict:
    return {
        "vm_id": vm_id,
        "label": "linux-kvm",
        "base_image_id": "base",
        "overlay_path": f"/tmp/node-agent-overlays/{vm_id}.qcow2",
        "vcpu": 2,
        "ram_mb": 2048,
        "disk_gb": 20,
        "lease_expires_at": (datetime.now(UTC) + timedelta(minutes=10)).isoformat(),
        "connect_deadline": (datetime.now(UTC) + timedelta(minutes=2)).isoformat(),
        "jenkins_url": "http://jenkins:8080",
        "jenkins_node_name": "node-1",
        "jnlp_secret": "s",
        "cloud_init_user_data_b64": base64.b64encode(b"#cloud-config\n").decode(
            "ascii"
        ),
        "metadata": {"lease_id": "lease-1"},
    }


def test_ensure_vm_idempotent() -> None:
    client = TestClient(app)
    vm_id = "vm-test-idempotent"
    p = _payload(vm_id)
    r1 = client.put(f"/v1/vms/{vm_id}", json=p)
    assert r1.status_code == 200
    assert r1.json()["serial_log_path"].endswith(f"/{vm_id}/serial.log")
    r2 = client.put(f"/v1/vms/{vm_id}", json=p)
    assert r2.status_code == 200
    assert r2.json()["vm_id"] == vm_id
    assert r2.json()["serial_log_path"].endswith(f"/{vm_id}/serial.log")


def test_delete_vm_endpoint() -> None:
    client = TestClient(app)
    vm_id = "vm-test-delete"
    p = _payload(vm_id)
    client.put(f"/v1/vms/{vm_id}", json=p)
    out = client.delete(f"/v1/vms/{vm_id}")
    assert out.status_code == 200
    assert out.json()["state"] == "TERMINATED"
