from dataclasses import dataclass

import httpx

from control_plane.clients.http import RetryPolicy, request_with_retry


@dataclass
class VMEnsureRequest:
    vm_id: str
    label: str
    base_image_id: str
    overlay_path: str
    vcpu: int
    ram_mb: int
    disk_gb: int
    lease_expires_at: str
    connect_deadline: str
    jenkins_url: str
    jenkins_node_name: str
    jnlp_secret: str
    cloud_init_user_data_b64: str
    metadata: dict


class NodeAgentClient:
    def __init__(
        self, base_url: str, retry: RetryPolicy, auth_token: str | None = None
    ):
        headers = {"Authorization": f"Bearer {auth_token}"} if auth_token else {}
        self.client = httpx.Client(
            base_url=base_url.rstrip("/"), timeout=10.0, headers=headers
        )
        self.retry = retry

    def ensure_vm(self, vm_id: str, payload: dict) -> dict:
        response = request_with_retry(
            self.client, "PUT", f"/v1/vms/{vm_id}", self.retry, json=payload
        )
        return response.json()

    def get_vm(self, vm_id: str) -> dict:
        response = request_with_retry(
            self.client, "GET", f"/v1/vms/{vm_id}", self.retry
        )
        return response.json()

    def delete_vm(self, vm_id: str, reason: str, force: bool = False) -> dict:
        response = request_with_retry(
            self.client,
            "DELETE",
            f"/v1/vms/{vm_id}",
            self.retry,
            params={"reason": reason, "force": str(force).lower()},
        )
        return response.json()

    def capacity(self) -> dict:
        response = request_with_retry(self.client, "GET", "/v1/capacity", self.retry)
        return response.json()
