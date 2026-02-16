import os
from datetime import UTC, datetime, timedelta

from node_agent.config import get_agent_settings

os.environ["NODE_AGENT_DISABLE_WORKERS"] = "true"
os.environ["NODE_AGENT_DRY_RUN"] = "true"
os.environ["NODE_AGENT_OVERLAY_DIR"] = "/tmp/node-agent-overlays"
os.environ["NODE_AGENT_CLOUD_INIT_DIR"] = "/tmp/node-agent-cloud-init"
os.environ["NODE_AGENT_BASE_IMAGE_DIR"] = "/tmp/node-agent-base"
os.environ["NODE_AGENT_STATE_DB_PATH"] = "/tmp/node-agent-safety.db"
get_agent_settings.cache_clear()

from node_agent.safety import enforce_ttl_once  # noqa: E402
from node_agent.state import initialize_state, list_vms, upsert_vm  # noqa: E402


def setup_function() -> None:
    db_path = os.environ["NODE_AGENT_STATE_DB_PATH"]
    if os.path.exists(db_path):
        os.remove(db_path)
    initialize_state()


def test_enforce_ttl_handles_mixed_naive_and_aware_timestamps() -> None:
    now = datetime.now(UTC)
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
