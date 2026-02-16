from functools import lru_cache
import platform
from pathlib import Path
import subprocess

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NODE_AGENT_", extra="ignore")

    host_id: str = Field(default="dev-host")
    bootstrap_token: str = Field(default="dev-bootstrap-token")
    control_plane_url: str = Field(default="http://localhost:8000")

    bind_host: str = Field(default="0.0.0.0")
    bind_port: int = Field(default=9000, ge=1)
    advertise_addr: str | None = Field(default=None)

    state_db_path: str = Field(default="./node_agent.db")
    base_image_dir: str = Field(default="/var/lib/jenkins-qemu/base")
    overlay_dir: str = Field(default="/var/lib/jenkins-qemu/overlays")
    cloud_init_dir: str = Field(default="/var/lib/jenkins-qemu/cloud-init")

    os_family: str | None = Field(default=None)
    os_flavor: str | None = Field(default=None)
    os_version: str | None = Field(default=None)
    cpu_arch: str | None = Field(default=None)

    qemu_binary: str = Field(default="qemu-system-x86_64")
    qemu_accel: str | None = Field(default=None)
    supported_accels: list[str] = Field(default_factory=list)
    qemu_machine: str = Field(default="q35")
    qemu_cpu: str = Field(default="host")
    network_backend: str = Field(default="bridge")
    network_interface: str = Field(default="br0")
    disk_interface: str = Field(default="virtio")

    service_manager: str = Field(default="systemd")
    heartbeat_interval_sec: int = Field(default=5, ge=1)
    ttl_check_interval_sec: int = Field(default=5, ge=1)
    reconcile_interval_sec: int = Field(default=10, ge=1)

    node_agent_auth_token: str | None = Field(default=None)
    dry_run: bool = Field(default=False)
    disable_workers: bool = Field(default=False)
    debug_artifact_retention_sec: int = Field(default=0, ge=0)

    def ensure_dirs(self) -> None:
        for path in (self.base_image_dir, self.overlay_dir, self.cloud_init_dir):
            Path(path).mkdir(parents=True, exist_ok=True)

    def validate_platform(self) -> None:
        if self.os_family not in {"linux", "bsd", "other"}:
            raise ValueError(f"unsupported os_family {self.os_family}")
        if self.qemu_accel and self.qemu_accel not in {"kvm", "nvmm", "tcg"}:
            raise ValueError(f"unsupported qemu_accel {self.qemu_accel}")

        allowed_backends = {"bridge", "tap", "user"}
        if self.network_backend not in allowed_backends:
            raise ValueError(
                f"unsupported network_backend {self.network_backend}; expected one of {sorted(allowed_backends)}"
            )
        if self.network_backend in {"bridge", "tap"} and not self.network_interface:
            raise ValueError(
                f"network_interface is required for network_backend={self.network_backend}"
            )


def _detect_os() -> tuple[str, str, str, str]:
    system = platform.system().lower()
    release = platform.release().lower()
    arch = platform.machine().lower() or "unknown"

    if system == "linux":
        return "linux", "linux", release or "unknown", arch
    if system in {"dragonfly", "freebsd", "openbsd", "netbsd"}:
        flavor = {
            "dragonfly": "dragonflybsd",
            "freebsd": "freebsd",
            "openbsd": "openbsd",
            "netbsd": "netbsd",
        }[system]
        return "bsd", flavor, release or "unknown", arch
    return "other", system or "unknown", release or "unknown", arch


def _detect_supported_accels(qemu_binary: str) -> list[str]:
    detected: list[str] = []
    try:
        completed = subprocess.run(
            [qemu_binary, "-accel", "help"],
            check=True,
            capture_output=True,
            text=True,
        )
        text = f"{completed.stdout}\n{completed.stderr}".lower()
        for accel in ("kvm", "nvmm", "tcg"):
            if accel in text:
                detected.append(accel)
    except Exception:
        pass

    if "tcg" not in detected:
        detected.append("tcg")
    return detected


def _select_accel(os_family: str, os_flavor: str, supported: list[str]) -> str:
    if os_family == "linux" and "kvm" in supported:
        return "kvm"
    if os_flavor in {"dragonflybsd", "freebsd"} and "nvmm" in supported:
        return "nvmm"
    return "tcg"


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    settings = AgentSettings()

    os_family, os_flavor, os_version, cpu_arch = _detect_os()
    settings.os_family = os_family
    settings.os_flavor = os_flavor
    settings.os_version = os_version
    settings.cpu_arch = cpu_arch

    settings.supported_accels = _detect_supported_accels(settings.qemu_binary)
    if settings.qemu_accel is None:
        settings.qemu_accel = _select_accel(
            settings.os_family, settings.os_flavor, settings.supported_accels
        )
    elif settings.qemu_accel not in settings.supported_accels:
        settings.qemu_accel = _select_accel(
            settings.os_family, settings.os_flavor, settings.supported_accels
        )

    settings.validate_platform()
    settings.ensure_dirs()
    return settings
