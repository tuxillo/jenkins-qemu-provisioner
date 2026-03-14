import base64
import os
from pathlib import Path
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from node_agent.config import get_agent_settings
from node_agent.host_stats import reset_host_stats_service

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
    get_agent_settings.cache_clear()
    reset_host_stats_service()
    base_dir = Path(os.environ["NODE_AGENT_BASE_IMAGE_DIR"])
    base_dir.mkdir(parents=True, exist_ok=True)
    (base_dir / "base.qcow2").write_bytes(b"fake-base")
    initialize_state()


def _payload(vm_id: str) -> dict:
    return {
        "vm_id": vm_id,
        "label": "linux-kvm",
        "guest_image": "default",
        "base_image": {
            "guest_image": "default",
            "base_image_id": "base",
            "source_kind": "manual_local",
            "source_url": None,
            "source_digest": None,
            "format": "qcow2",
        },
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
    vm_dir = Path(os.environ["NODE_AGENT_CLOUD_INIT_DIR"]) / vm_id
    assert vm_dir.exists()
    out = client.delete(f"/v1/vms/{vm_id}")
    assert out.status_code == 200
    assert out.json()["state"] == "TERMINATED"
    assert not vm_dir.exists()


def test_vm_debug_endpoint_exposes_serial_and_seed_artifacts() -> None:
    client = TestClient(app)
    vm_id = "vm-test-debug"
    p = _payload(vm_id)
    put = client.put(f"/v1/vms/{vm_id}", json=p)
    assert put.status_code == 200
    serial_path = put.json()["serial_log_path"]
    assert serial_path

    with open(serial_path, "w", encoding="utf-8") as f:
        f.write("BOOTSTRAP_STAGE=start\nBOOTSTRAP_STAGE=agent_download_ok\n")

    dbg = client.get(f"/v1/vms/{vm_id}/debug")
    assert dbg.status_code == 200
    data = dbg.json()
    assert data["vm_id"] == vm_id
    assert "agent_download_ok" in (data["serial_tail"] or "")
    assert "qemu-system" in (data["launch_command"] or "")
    assert "JENKINS_JNLP_SECRET=***" in (data["jenkins_env"] or "")


def test_capacity_endpoint_reports_allocatable_pool(monkeypatch) -> None:
    monkeypatch.setenv("NODE_AGENT_ALLOCATABLE_VCPU", "6")
    monkeypatch.setenv("NODE_AGENT_ALLOCATABLE_RAM_MB", "12288")
    monkeypatch.setattr("node_agent.heartbeat.os.cpu_count", lambda: 8)
    monkeypatch.setattr("node_agent.heartbeat._detect_total_ram_mb", lambda: 16384)
    get_agent_settings.cache_clear()

    client = TestClient(app)
    vm_id = "vm-test-capacity"
    put = client.put(f"/v1/vms/{vm_id}", json=_payload(vm_id))
    assert put.status_code == 200

    cap = client.get("/v1/capacity")
    assert cap.status_code == 200
    body = cap.json()
    assert body["cpu_total"] >= body["cpu_allocatable"]
    assert body["cpu_allocatable"] == 6
    assert body["cpu_free"] == 4
    assert body["ram_total_mb"] >= body["ram_allocatable_mb"]
    assert body["ram_allocatable_mb"] == 12288
    assert body["ram_free_mb"] == 10240
    assert body["io_pressure"] == 0.0
    assert body["disk_busy_frac"] is None
    assert body["disk_read_mb_s"] is None
    assert body["disk_write_mb_s"] is None
    assert body["stats_collected_at"]
