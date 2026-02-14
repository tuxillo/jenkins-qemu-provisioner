from node_agent.config import AgentSettings
from node_agent.qemu_runtime import build_qemu_command


def test_qemu_builder_linux_kvm() -> None:
    settings = AgentSettings(os_family="linux", qemu_accel="kvm", disable_workers=True)
    cmd = build_qemu_command(
        settings,
        vm_id="vm1",
        base_image_path="/tmp/base.qcow2",
        overlay_path="/tmp/overlay.qcow2",
        cloud_init_iso="/tmp/cidata.iso",
        vcpu=2,
        ram_mb=4096,
    )
    assert "-accel" in cmd
    assert "kvm" in cmd


def test_qemu_builder_dragonflybsd_nvmm() -> None:
    settings = AgentSettings(
        os_family="dragonflybsd", qemu_accel="nvmm", disable_workers=True
    )
    cmd = build_qemu_command(
        settings,
        vm_id="vm2",
        base_image_path="/tmp/base.qcow2",
        overlay_path="/tmp/overlay.qcow2",
        cloud_init_iso="/tmp/cidata.iso",
        vcpu=2,
        ram_mb=4096,
    )
    assert "-accel" in cmd
    assert "nvmm" in cmd
