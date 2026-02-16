from datetime import datetime

from pydantic import BaseModel


class VMEnsureRequest(BaseModel):
    vm_id: str
    label: str
    base_image_id: str
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
