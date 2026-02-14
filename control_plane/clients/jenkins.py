import json
from dataclasses import dataclass

import httpx

from control_plane.clients.http import RetryPolicy, request_with_retry


@dataclass
class QueueSnapshot:
    queued_by_label: dict[str, int]


class JenkinsClient:
    def __init__(self, base_url: str, user: str, api_token: str, retry: RetryPolicy):
        self.base_url = base_url.rstrip("/")
        self.retry = retry
        self.client = httpx.Client(auth=(user, api_token), timeout=10.0)

    def queue_snapshot(self) -> QueueSnapshot:
        url = f"{self.base_url}/queue/api/json"
        response = request_with_retry(self.client, "GET", url, self.retry)
        data = response.json()
        queued_by_label: dict[str, int] = {}
        for item in data.get("items", []):
            labels = item.get("assignedLabel", {})
            label_name = labels.get("name") if isinstance(labels, dict) else None
            if label_name:
                queued_by_label[label_name] = queued_by_label.get(label_name, 0) + 1
        return QueueSnapshot(queued_by_label=queued_by_label)

    def create_ephemeral_node(self, node_name: str, label: str) -> None:
        url = f"{self.base_url}/computer/doCreateItem"
        node_definition = {
            "name": node_name,
            "nodeDescription": "ephemeral vm node",
            "numExecutors": "1",
            "remoteFS": "/home/jenkins",
            "labelString": label,
            "mode": "EXCLUSIVE",
            "launcher": {
                "stapler-class": "hudson.slaves.JNLPLauncher",
                "$class": "hudson.slaves.JNLPLauncher",
            },
            "retentionStrategy": {
                "stapler-class": "hudson.slaves.RetentionStrategy$Always",
                "$class": "hudson.slaves.RetentionStrategy$Always",
            },
            "nodeProperties": {"stapler-class-bag": "true"},
        }
        payload = {
            "name": node_name,
            "type": "hudson.slaves.DumbSlave$DescriptorImpl",
            "json": json.dumps(node_definition),
        }
        request_with_retry(self.client, "POST", url, self.retry, data=payload)

    def delete_node(self, node_name: str) -> None:
        url = f"{self.base_url}/computer/{node_name}/doDelete"
        request_with_retry(self.client, "POST", url, self.retry)

    def get_inbound_secret(self, node_name: str) -> str:
        # Jenkins API shape depends on version; this endpoint works for common setups.
        url = f"{self.base_url}/computer/{node_name}/slave-agent.jnlp"
        response = request_with_retry(self.client, "GET", url, self.retry)
        text = response.text
        start = text.find("<argument>")
        end = text.find("</argument>", start)
        if start == -1 or end == -1:
            raise RuntimeError(f"could not parse inbound secret for node {node_name}")
        return text[start + len("<argument>") : end]

    def is_node_connected(self, node_name: str) -> bool:
        url = f"{self.base_url}/computer/{node_name}/api/json"
        response = request_with_retry(self.client, "GET", url, self.retry)
        data = response.json()
        return bool(data.get("offline") is False)
