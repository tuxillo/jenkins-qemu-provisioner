import base64
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

from node_agent.config import AgentSettings


@dataclass
class RuntimePaths:
    overlay_path: str
    cloud_init_iso: str


def decode_user_data(encoded: str) -> str:
    return base64.b64decode(encoded).decode("utf-8")


def write_cloud_init_files(
    settings: AgentSettings,
    vm_id: str,
    user_data_b64: str,
    node_name: str,
    jenkins_url: str,
    jnlp_secret: str,
) -> RuntimePaths:
    vm_dir = Path(settings.cloud_init_dir) / vm_id
    vm_dir.mkdir(parents=True, exist_ok=True)

    user_data_path = vm_dir / "user-data"
    meta_data_path = vm_dir / "meta-data"
    iso_path = vm_dir / "cidata.iso"

    user_data_path.write_text(decode_user_data(user_data_b64), encoding="utf-8")
    meta_data_path.write_text(
        f"instance-id: {vm_id}\nlocal-hostname: {node_name}\n", encoding="utf-8"
    )

    # Keep Jenkins values available for diagnostics/troubleshooting in dev.
    env_path = vm_dir / "jenkins-agent.env"
    env_path.write_text(
        f"JENKINS_URL={jenkins_url}\nJENKINS_NODE_NAME={node_name}\nJENKINS_JNLP_SECRET={jnlp_secret}\n",
        encoding="utf-8",
    )

    # Prefer xorriso/genisoimage if present. Fall back to empty file for dry_run/dev.
    mkisofs = shutil_which_first(["xorriso", "genisoimage", "mkisofs"])
    if mkisofs:
        if "xorriso" in mkisofs:
            cmd = [
                mkisofs,
                "-as",
                "mkisofs",
                "-output",
                str(iso_path),
                "-volid",
                "cidata",
                "-joliet",
                "-rock",
                str(user_data_path),
                str(meta_data_path),
            ]
        else:
            cmd = [
                mkisofs,
                "-output",
                str(iso_path),
                "-volid",
                "cidata",
                "-joliet",
                "-rock",
                str(user_data_path),
                str(meta_data_path),
            ]
        subprocess.run(cmd, check=True)
    else:
        iso_path.write_bytes(b"")

    overlay_path = str(Path(settings.overlay_dir) / f"{vm_id}.qcow2")
    return RuntimePaths(overlay_path=overlay_path, cloud_init_iso=str(iso_path))


def shutil_which_first(candidates: list[str]) -> str | None:
    for name in candidates:
        path = shutil_which(name)
        if path:
            return path
    return None


def shutil_which(cmd: str) -> str | None:
    for p in os.environ.get("PATH", "").split(os.pathsep):
        c = Path(p) / cmd
        if c.exists() and os.access(c, os.X_OK):
            return str(c)
    return None


def build_qemu_command(
    settings: AgentSettings,
    *,
    vm_id: str,
    base_image_path: str,
    overlay_path: str,
    cloud_init_iso: str,
    vcpu: int,
    ram_mb: int,
    disk_interface: str | None = None,
) -> list[str]:
    disk_if = disk_interface or settings.disk_interface
    qmp_path = str(Path(settings.overlay_dir) / f"{vm_id}.qmp.sock")

    cmd = [
        settings.qemu_binary,
        "-name",
        vm_id,
        "-accel",
        settings.qemu_accel,
        "-machine",
        settings.qemu_machine,
        "-cpu",
        settings.qemu_cpu,
        "-smp",
        str(vcpu),
        "-m",
        str(ram_mb),
        "-display",
        "none",
        "-serial",
        "mon:stdio",
        "-qmp",
        f"unix:{qmp_path},server,nowait",
        "-drive",
        f"if={disk_if},file={overlay_path},format=qcow2,cache=none",
        "-drive",
        f"if={disk_if},file={cloud_init_iso},format=raw,readonly=on",
    ]

    if settings.network_backend == "bridge":
        cmd.extend(
            [
                "-netdev",
                f"bridge,id=net0,br={settings.network_interface}",
                "-device",
                "virtio-net-pci,netdev=net0",
            ]
        )
    elif settings.network_backend == "tap":
        cmd.extend(
            [
                "-netdev",
                f"tap,id=net0,ifname={settings.network_interface},script=no,downscript=no",
                "-device",
                "virtio-net-pci,netdev=net0",
            ]
        )
    else:
        cmd.extend(["-netdev", "user,id=net0", "-device", "virtio-net-pci,netdev=net0"])

    _ = base_image_path  # retained for traceability and future direct drive model.
    return cmd


def create_overlay(base_image_path: str, overlay_path: str) -> None:
    qemu_img = shutil_which_first(["qemu-img"])
    if not qemu_img:
        raise RuntimeError("qemu-img not found in PATH")
    Path(overlay_path).parent.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            qemu_img,
            "create",
            "-f",
            "qcow2",
            "-F",
            "qcow2",
            "-b",
            base_image_path,
            overlay_path,
        ],
        check=True,
    )


def launch_qemu(cmd: list[str], dry_run: bool) -> int:
    if dry_run:
        return 0
    proc = subprocess.Popen(cmd)
    return int(proc.pid)


def terminate_pid(pid: int, dry_run: bool) -> None:
    if pid <= 0 or dry_run:
        return
    try:
        os.kill(pid, 15)
    except ProcessLookupError:
        return
