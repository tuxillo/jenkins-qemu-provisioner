import json
from dataclasses import dataclass
from typing import Any

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
        url = f"{self.base_url}/queue/api/json?depth=2"
        response = request_with_retry(self.client, "GET", url, self.retry)
        data = response.json()
        queued_by_label: dict[str, int] = {}
        for item in data.get("items", []):
            label_name = self._extract_queue_label(item)
            if label_name:
                queued_by_label[label_name] = queued_by_label.get(label_name, 0) + 1
        return QueueSnapshot(queued_by_label=queued_by_label)

    @staticmethod
    def _extract_queue_label(item: dict) -> str | None:
        assigned_label = item.get("assignedLabel")
        if isinstance(assigned_label, dict):
            name = assigned_label.get("name")
            if isinstance(name, str) and name:
                return name

        task = item.get("task")
        if isinstance(task, dict):
            label_expr = task.get("labelExpression")
            if isinstance(label_expr, str) and label_expr:
                return label_expr

            task_label = task.get("assignedLabel")
            if isinstance(task_label, dict):
                name = task_label.get("name")
                if isinstance(name, str) and name:
                    return name

        return None

    def create_ephemeral_node(
        self, node_name: str, label: str, *, use_websocket: bool = True
    ) -> None:
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
                "webSocket": use_websocket,
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
        self._post_with_crumb(url, data=payload)

    def delete_node(self, node_name: str) -> None:
        url = f"{self.base_url}/computer/{node_name}/doDelete"
        self._post_with_crumb(url)

    def get_inbound_secret(self, node_name: str) -> str:
        api_url = f"{self.base_url}/computer/{node_name}/api/json?tree=jnlpMac"
        try:
            response = request_with_retry(self.client, "GET", api_url, self.retry)
            payload = response.json()
            token = payload.get("jnlpMac")
            if isinstance(token, str) and token:
                return token
        except Exception:  # noqa: BLE001
            pass

        # Fallback for Jenkins variants that do not expose jnlpMac in JSON API.
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

    def _post_with_crumb(self, url: str, **kwargs: Any) -> None:
        request_kwargs = dict(kwargs)
        request_kwargs.setdefault("follow_redirects", True)
        raw_headers = request_kwargs.get("headers")
        headers: dict[str, str] = {}
        if isinstance(raw_headers, dict):
            headers = {
                str(key): str(value)
                for key, value in raw_headers.items()
                if isinstance(key, str) and isinstance(value, str)
            }

        try:
            crumb = self._fetch_crumb()
            headers[crumb["field"]] = crumb["value"]
            request_kwargs["headers"] = headers
        except Exception:  # noqa: BLE001
            if headers:
                request_kwargs["headers"] = headers

        request_with_retry(self.client, "POST", url, self.retry, **request_kwargs)

    def _fetch_crumb(self) -> dict[str, str]:
        url = f"{self.base_url}/crumbIssuer/api/json"
        response = request_with_retry(self.client, "GET", url, self.retry)
        payload = response.json()
        field = payload.get("crumbRequestField")
        value = payload.get("crumb")
        if not isinstance(field, str) or not field:
            raise RuntimeError("jenkins crumb request field missing")
        if not isinstance(value, str) or not value:
            raise RuntimeError("jenkins crumb value missing")
        return {"field": field, "value": value}
