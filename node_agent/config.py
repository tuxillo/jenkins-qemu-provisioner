from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class AgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="NODE_AGENT_", extra="ignore")

    host_id: str = Field(default="dev-host")
    bootstrap_token: str = Field(default="dev-bootstrap-token")
    control_plane_url: str = Field(default="http://localhost:8000")

    bind_host: str = Field(default="0.0.0.0")
    bind_port: int = Field(default=9000, ge=1)

    state_db_path: str = Field(default="./node_agent.db")
    base_image_dir: str = Field(default="/var/lib/jenkins-qemu/base")
    overlay_dir: str = Field(default="/var/lib/jenkins-qemu/overlays")
    cloud_init_dir: str = Field(default="/var/lib/jenkins-qemu/cloud-init")

    os_family: str = Field(default="linux")
    os_version: str = Field(default="unknown")

    qemu_binary: str = Field(default="qemu-system-x86_64")
    qemu_accel: str = Field(default="kvm")
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

    def ensure_dirs(self) -> None:
        for path in (self.base_image_dir, self.overlay_dir, self.cloud_init_dir):
            Path(path).mkdir(parents=True, exist_ok=True)

    def validate_platform(self) -> None:
        allowed = {"linux", "dragonflybsd"}
        if self.os_family not in allowed:
            raise ValueError(f"unsupported os_family {self.os_family}")
        if self.os_family == "linux" and self.qemu_accel == "nvmm":
            raise ValueError("nvmm accelerator is not valid default for linux")
        if self.os_family == "dragonflybsd" and self.qemu_accel == "kvm":
            raise ValueError("kvm accelerator is not valid default for dragonflybsd")


@lru_cache(maxsize=1)
def get_agent_settings() -> AgentSettings:
    settings = AgentSettings()
    settings.validate_platform()
    settings.ensure_dirs()
    return settings
