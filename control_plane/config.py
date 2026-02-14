from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    jenkins_url: str = Field(default="http://localhost:8080")
    jenkins_user: str = Field(default="admin")
    jenkins_api_token: str = Field(default="admin")

    database_url: str = Field(default="sqlite:///./control_plane.db")

    loop_interval_sec: int = Field(default=5, ge=1)
    gc_interval_sec: int = Field(default=30, ge=5)

    global_max_vms: int = Field(default=100, ge=1)
    label_max_inflight: int = Field(default=5, ge=1)
    label_burst: int = Field(default=3, ge=1)

    connect_deadline_sec: int = Field(default=240, ge=5)
    disconnected_grace_sec: int = Field(default=60, ge=5)
    vm_ttl_sec: int = Field(default=7200, ge=60)
    host_stale_timeout_sec: int = Field(default=20, ge=5)

    retry_attempts: int = Field(default=3, ge=1)
    retry_sleep_sec: int = Field(default=10, ge=1)
    allow_unknown_host_registration: bool = Field(default=False)

    node_agent_url: str = Field(default="http://localhost:9000")
    node_agent_auth_token: str | None = Field(default=None)

    disable_background_loops: bool = Field(default=False)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
