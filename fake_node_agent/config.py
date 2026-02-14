from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class FakeAgentSettings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="FAKE_NODE_AGENT_", extra="ignore")

    host_id: str = Field(default="fake-host-1")
    bind_host: str = Field(default="0.0.0.0")
    bind_port: int = Field(default=9000, ge=1)

    os_family: str = Field(default="linux")
    os_version: str = Field(default="dev")
    qemu_binary: str = Field(default="fake-qemu")
    selected_accel: str = Field(default="kvm")
    supported_accels_csv: str = Field(default="kvm,tcg")

    cpu_total: int = Field(default=8, ge=1)
    ram_total_mb: int = Field(default=16384, ge=1)
    io_pressure: float = Field(default=0.0, ge=0.0)

    control_plane_url: str = Field(default="http://control-plane:8000")
    bootstrap_token: str = Field(default="fake-bootstrap-token")
    heartbeat_interval_sec: int = Field(default=5, ge=1)
    enable_heartbeat_worker: bool = Field(default=True)

    @property
    def supported_accels(self) -> list[str]:
        return [x.strip() for x in self.supported_accels_csv.split(",") if x.strip()]


@lru_cache(maxsize=1)
def get_settings() -> FakeAgentSettings:
    return FakeAgentSettings()
