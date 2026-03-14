import os
from datetime import UTC, datetime, timedelta

from fastapi.testclient import TestClient

from control_plane.auth import hash_token
from control_plane.config import get_settings
from control_plane.db import Base, SessionLocal, engine
from control_plane.models import Host, Lease, LeaseState


os.environ["DISABLE_BACKGROUND_LOOPS"] = "true"
os.environ["ALLOW_UNKNOWN_HOST_REGISTRATION"] = "true"
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
            cpu_allocatable=16,
            cpu_free=16,
            ram_total_mb=32768,
            ram_allocatable_mb=32768,
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
            "cpu_allocatable": 12,
            "ram_allocatable_mb": 24576,
            "base_image_ids": ["base-a"],
            "available_images": [
                {
                    "guest_image": "default",
                    "base_image_id": "base-a",
                    "source_digest": None,
                    "cpu_arch": "x86_64",
                    "state": "READY",
                }
            ],
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
            "cpu_total": 16,
            "ram_total_mb": 32768,
            "cpu_allocatable": 12,
            "ram_allocatable_mb": 24576,
            "cpu_free": 12,
            "ram_free_mb": 24576,
            "io_pressure": 0.15,
            "running_vm_ids": ["vm-1"],
            "available_images": [
                {
                    "guest_image": "default",
                    "base_image_id": "base-a",
                    "source_digest": None,
                    "cpu_arch": "x86_64",
                    "state": "READY",
                }
            ],
        },
    )
    assert hb.status_code == 200
    assert hb.json() == {"ok": True}

    with SessionLocal() as db_verify:
        host = db_verify.get(Host, "host-a")
        assert host is not None
        assert host.cpu_total == 16
        assert host.cpu_allocatable == 12
        assert host.cpu_free == 12
        assert host.ram_total_mb == 32768
        assert host.ram_allocatable_mb == 24576
        assert host.ram_free_mb == 24576
        assert host.available_images_json is not None


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
            cpu_allocatable=8,
            cpu_free=8,
            ram_total_mb=16384,
            ram_allocatable_mb=16384,
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
            cpu_allocatable=8,
            cpu_free=8,
            ram_total_mb=16384,
            ram_allocatable_mb=16384,
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
            cpu_allocatable=8,
            cpu_free=8,
            ram_total_mb=16384,
            ram_allocatable_mb=16384,
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


def test_register_unknown_host_allowed_in_dev_mode():
    client = TestClient(app)
    reg = client.post(
        "/v1/hosts/auto-host/register",
        headers={"Authorization": "Bearer auto-bootstrap"},
        json={
            "agent_version": "fake-0.1.0",
            "qemu_version": "fake",
            "cpu_total": 8,
            "ram_total_mb": 16384,
            "base_image_ids": ["fake-base"],
            "available_images": [
                {
                    "guest_image": "default",
                    "base_image_id": "fake-base",
                    "source_digest": None,
                    "cpu_arch": "x86_64",
                    "state": "READY",
                }
            ],
            "addr": "fake:9000",
            "os_family": "linux",
            "os_version": "dev",
            "qemu_binary": "fake-qemu",
            "supported_accels": ["kvm", "tcg"],
            "selected_accel": "kvm",
        },
    )
    assert reg.status_code == 200
    assert reg.json()["host_id"] == "auto-host"

    with SessionLocal() as db_verify:
        host = db_verify.get(Host, "auto-host")
        assert host is not None
        assert host.cpu_allocatable == 8
        assert host.ram_allocatable_mb == 16384


def test_get_leases_includes_guest_image_fields():
    now = datetime.now(UTC).replace(tzinfo=None)
    with SessionLocal() as db:
        db.add(
            Lease(
                lease_id="lease-1",
                vm_id="vm-1",
                label="linux-small",
                jenkins_node="ephemeral-lease-1",
                state=LeaseState.BOOTING.value,
                host_id="host-a",
                guest_image="default",
                base_image_id="default",
                created_at=now,
                updated_at=now,
                connect_deadline=now + timedelta(minutes=5),
                ttl_deadline=now + timedelta(hours=1),
            )
        )
        db.commit()

    client = TestClient(app)
    response = client.get("/v1/leases")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["guest_image"] == "default"
    assert body[0]["base_image_id"] == "default"
