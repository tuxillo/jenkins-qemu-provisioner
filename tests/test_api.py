import os
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from control_plane.auth import hash_token
from control_plane.config import get_settings
from control_plane.db import Base, SessionLocal, engine
from control_plane.models import Host


os.environ["DISABLE_BACKGROUND_LOOPS"] = "true"
get_settings.cache_clear()

from control_plane.main import app  # noqa: E402


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_healthz():
    client = TestClient(app)
    response = client.get("/healthz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_host_register_and_heartbeat():
    db = SessionLocal()
    db.add(
        Host(
            host_id="host-a",
            enabled=True,
            bootstrap_token_hash=hash_token("bootstrap"),
            cpu_total=16,
            cpu_free=16,
            ram_total_mb=32768,
            ram_free_mb=32768,
        )
    )
    db.commit()
    db.close()

    client = TestClient(app)
    reg = client.post(
        "/v1/hosts/host-a/register",
        headers={"Authorization": "Bearer bootstrap"},
        json={
            "agent_version": "0.1.0",
            "qemu_version": "9.0",
            "cpu_total": 16,
            "ram_total_mb": 32768,
            "base_image_ids": ["base-a"],
            "addr": "10.0.0.10",
            "os_family": "linux",
            "os_version": "6.8",
            "qemu_binary": "/usr/bin/qemu-system-x86_64",
            "supported_accels": ["kvm", "tcg"],
            "selected_accel": "kvm",
        },
    )
    assert reg.status_code == 200
    session_token = reg.json()["session_token"]

    hb = client.post(
        "/v1/hosts/host-a/heartbeat",
        headers={"Authorization": f"Bearer {session_token}"},
        json={
            "cpu_free": 12,
            "ram_free_mb": 24576,
            "io_pressure": 0.15,
            "running_vm_ids": ["vm-1"],
        },
    )
    assert hb.status_code == 200
    assert hb.json() == {"ok": True}


def test_heartbeat_rejects_expired_session():
    db = SessionLocal()
    db.add(
        Host(
            host_id="host-b",
            enabled=True,
            session_token_hash=hash_token("session"),
            session_expires_at=(datetime.now(UTC) - timedelta(hours=1)).replace(
                tzinfo=None
            ),
            cpu_total=8,
            cpu_free=8,
            ram_total_mb=16384,
            ram_free_mb=16384,
        )
    )
    db.commit()
    db.close()

    client = TestClient(app)
    hb = client.post(
        "/v1/hosts/host-b/heartbeat",
        headers={"Authorization": "Bearer session"},
        json={
            "cpu_free": 6,
            "ram_free_mb": 12000,
            "io_pressure": 0.2,
            "running_vm_ids": [],
        },
    )
    assert hb.status_code == 401


def test_disable_then_enable_host():
    db = SessionLocal()
    db.add(
        Host(
            host_id="host-c",
            enabled=True,
            bootstrap_token_hash=hash_token("boot"),
            session_token_hash=hash_token("session"),
            cpu_total=8,
            cpu_free=8,
            ram_total_mb=16384,
            ram_free_mb=16384,
        )
    )
    db.commit()
    db.close()

    client = TestClient(app)
    r1 = client.post("/v1/hosts/host-c/disable")
    assert r1.status_code == 200
    r2 = client.post("/v1/hosts/host-c/enable")
    assert r2.status_code == 200


def test_heartbeat_rejects_accel_mismatch():
    db = SessionLocal()
    db.add(
        Host(
            host_id="host-d",
            enabled=True,
            bootstrap_token_hash=hash_token("boot"),
            session_token_hash=hash_token("session"),
            session_expires_at=(datetime.now(UTC) + timedelta(hours=1)).replace(
                tzinfo=None
            ),
            cpu_total=8,
            cpu_free=8,
            ram_total_mb=16384,
            ram_free_mb=16384,
        )
    )
    db.commit()
    db.close()

    client = TestClient(app)
    hb = client.post(
        "/v1/hosts/host-d/heartbeat",
        headers={"Authorization": "Bearer session"},
        json={
            "cpu_free": 6,
            "ram_free_mb": 12000,
            "io_pressure": 0.2,
            "running_vm_ids": [],
            "supported_accels": ["kvm"],
            "selected_accel": "nvmm",
        },
    )
    assert hb.status_code == 400
