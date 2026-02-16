import os
import shutil
from pathlib import Path
from datetime import UTC, datetime, timedelta

from node_agent.config import get_agent_settings

os.environ["NODE_AGENT_DISABLE_WORKERS"] = "true"
os.environ["NODE_AGENT_DRY_RUN"] = "true"
os.environ["NODE_AGENT_OVERLAY_DIR"] = "/tmp/node-agent-overlays"
os.environ["NODE_AGENT_CLOUD_INIT_DIR"] = "/tmp/node-agent-cloud-init"
os.environ["NODE_AGENT_BASE_IMAGE_DIR"] = "/tmp/node-agent-base"
os.environ["NODE_AGENT_STATE_DB_PATH"] = "/tmp/node-agent-safety.db"
os.environ["NODE_AGENT_DEBUG_ARTIFACT_RETENTION_SEC"] = "0"
get_agent_settings.cache_clear()

from node_agent.safety import cleanup_orphan_files_once, enforce_ttl_once  # noqa: E402
from node_agent.state import initialize_state, list_vms, upsert_vm  # noqa: E402


def setup_function() -> None:
    db_path = os.environ["NODE_AGENT_STATE_DB_PATH"]
    if os.path.exists(db_path):
        os.remove(db_path)
    overlay_dir = Path(os.environ["NODE_AGENT_OVERLAY_DIR"])
    cloud_init_dir = Path(os.environ["NODE_AGENT_CLOUD_INIT_DIR"])
    if overlay_dir.exists():
        for file in overlay_dir.glob("*.qcow2"):
            file.unlink(missing_ok=True)
    if cloud_init_dir.exists():
        for child in cloud_init_dir.iterdir():
            if child.is_dir():
                shutil.rmtree(child, ignore_errors=True)
    initialize_state()


def test_enforce_ttl_handles_mixed_naive_and_aware_timestamps() -> None:
    now = datetime.now(UTC)
    aware_vm_dir = Path("/tmp/node-agent-cloud-init/vm-aware")
    aware_vm_dir.mkdir(parents=True, exist_ok=True)
    Path("/tmp/node-agent-overlays/vm-aware.qcow2").parent.mkdir(
        parents=True, exist_ok=True
    )
    Path("/tmp/node-agent-overlays/vm-aware.qcow2").write_text(
        "overlay", encoding="utf-8"
    )
    Path("/tmp/node-agent-cloud-init/vm-aware/cidata.iso").write_text(
        "iso", encoding="utf-8"
    )
    Path("/tmp/node-agent-cloud-init/vm-aware/serial.log").write_text(
        "serial", encoding="utf-8"
    )

    naive_vm_dir = Path("/tmp/node-agent-cloud-init/vm-naive")
    naive_vm_dir.mkdir(parents=True, exist_ok=True)
    Path("/tmp/node-agent-overlays/vm-naive.qcow2").write_text(
        "overlay", encoding="utf-8"
    )
    Path("/tmp/node-agent-cloud-init/vm-naive/cidata.iso").write_text(
        "iso", encoding="utf-8"
    )
    Path("/tmp/node-agent-cloud-init/vm-naive/serial.log").write_text(
        "serial", encoding="utf-8"
    )

    upsert_vm(
        vm_id="vm-aware",
        state="RUNNING",
        host_id="h1",
        lease_id="l1",
        qemu_pid=0,
        overlay_path="/tmp/node-agent-overlays/vm-aware.qcow2",
        cloud_init_iso="/tmp/node-agent-cloud-init/vm-aware/cidata.iso",
        serial_log_path="/tmp/node-agent-cloud-init/vm-aware/serial.log",
        connect_deadline=(now + timedelta(minutes=1)).isoformat(),
        lease_expires_at=(now - timedelta(minutes=1)).isoformat(),
        reason=None,
    )

    upsert_vm(
        vm_id="vm-naive",
        state="RUNNING",
        host_id="h1",
        lease_id="l2",
        qemu_pid=0,
        overlay_path="/tmp/node-agent-overlays/vm-naive.qcow2",
        cloud_init_iso="/tmp/node-agent-cloud-init/vm-naive/cidata.iso",
        serial_log_path="/tmp/node-agent-cloud-init/vm-naive/serial.log",
        connect_deadline=(now + timedelta(minutes=1)).replace(tzinfo=None).isoformat(),
        lease_expires_at=(now - timedelta(minutes=1)).replace(tzinfo=None).isoformat(),
        reason=None,
    )

    enforce_ttl_once()
    assert list_vms() == []
    assert not aware_vm_dir.exists()
    assert not naive_vm_dir.exists()
    assert not Path("/tmp/node-agent-overlays/vm-aware.qcow2").exists()
    assert not Path("/tmp/node-agent-overlays/vm-naive.qcow2").exists()


def test_cleanup_orphan_files_once_prunes_orphan_runtime_directories() -> None:
    cloud_init_root = Path("/tmp/node-agent-cloud-init")
    cloud_init_root.mkdir(parents=True, exist_ok=True)
    orphan_dir = cloud_init_root / "orphan-vm"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "serial.log").write_text("stale", encoding="utf-8")

    cleanup_orphan_files_once()

    assert not orphan_dir.exists()


def test_cleanup_orphan_files_once_respects_artifact_retention() -> None:
    os.environ["NODE_AGENT_DEBUG_ARTIFACT_RETENTION_SEC"] = "3600"
    get_agent_settings.cache_clear()

    cloud_init_root = Path("/tmp/node-agent-cloud-init")
    cloud_init_root.mkdir(parents=True, exist_ok=True)
    orphan_dir = cloud_init_root / "retained-vm"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    (orphan_dir / "serial.log").write_text("stale", encoding="utf-8")

    cleanup_orphan_files_once()

    assert orphan_dir.exists()

    os.environ["NODE_AGENT_DEBUG_ARTIFACT_RETENTION_SEC"] = "0"
    get_agent_settings.cache_clear()
