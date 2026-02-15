from datetime import datetime

from pydantic import BaseModel, Field


class RegisterHostRequest(BaseModel):
    agent_version: str
    qemu_version: str
    cpu_total: int = Field(ge=1)
    ram_total_mb: int = Field(ge=256)
    base_image_ids: list[str] = Field(default_factory=list)
    addr: str
    os_family: str | None = None
    os_flavor: str | None = None
    os_version: str | None = None
    cpu_arch: str | None = None
    qemu_binary: str | None = None
    supported_accels: list[str] = Field(default_factory=list)
    selected_accel: str | None = None


class RegisterHostResponse(BaseModel):
    host_id: str
    enabled: bool
    session_token: str
    session_expires_at: datetime
    heartbeat_interval_sec: int


class HeartbeatRequest(BaseModel):
    cpu_free: int = Field(ge=0)
    ram_free_mb: int = Field(ge=0)
    io_pressure: float = Field(ge=0.0)
    running_vm_ids: list[str] = Field(default_factory=list)
    os_family: str | None = None
    os_flavor: str | None = None
    os_version: str | None = None
    cpu_arch: str | None = None
    qemu_binary: str | None = None
    supported_accels: list[str] = Field(default_factory=list)
    selected_accel: str | None = None


class VMStatusRequest(BaseModel):
    state: str
    reason: str | None = None
    lease_expires_at: datetime | None = None


class LeaseRead(BaseModel):
    lease_id: str
    vm_id: str
    label: str
    jenkins_node: str
    state: str
    host_id: str | None
    connect_deadline: datetime
    ttl_deadline: datetime
    last_error: str | None


class ManualTerminateRequest(BaseModel):
    reason: str = "manual_terminate"
