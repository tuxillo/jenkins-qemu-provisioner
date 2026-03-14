from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class AvailableImageResponse(BaseModel):
    guest_image: str
    base_image_id: str
    source_digest: str | None = None
    cpu_arch: str | None = None
    state: str = "READY"


class BaseImageRequest(BaseModel):
    guest_image: str
    base_image_id: str
    source_kind: Literal["manual_local", "remote_cache"]
    source_url: str | None = None
    source_digest: str | None = None
    format: str = Field(default="qcow2")

    @model_validator(mode="after")
    def validate_source_fields(self) -> "BaseImageRequest":
        if self.source_kind == "remote_cache":
            if not self.source_url:
                raise ValueError("remote_cache images require source_url")
            if not self.source_digest:
                raise ValueError("remote_cache images require source_digest")
        return self


class VMEnsureRequest(BaseModel):
    vm_id: str
    label: str
    guest_image: str
    base_image: BaseImageRequest
    overlay_path: str
    vcpu: int
    ram_mb: int
    disk_gb: int
    lease_expires_at: datetime
    connect_deadline: datetime
    jenkins_url: str
    jenkins_node_name: str
    jnlp_secret: str
    cloud_init_user_data_b64: str
    metadata: dict


class VMStateResponse(BaseModel):
    vm_id: str
    state: str
    last_transition_at: str
    reason: str | None = None
    lease_expires_at: str | None = None
    serial_log_path: str | None = None


class HostCapacityResponse(BaseModel):
    host_id: str
    os_family: str | None = None
    os_flavor: str | None = None
    cpu_arch: str | None = None
    selected_accel: str | None = None
    supported_accels: list[str]
    cpu_total: int
    cpu_allocatable: int
    cpu_free: int
    ram_total_mb: int
    ram_allocatable_mb: int
    ram_free_mb: int
    io_pressure: float
    stats_collected_at: datetime
    disk_busy_frac: float | None = None
    disk_read_mb_s: float | None = None
    disk_write_mb_s: float | None = None
    running_vms: int
