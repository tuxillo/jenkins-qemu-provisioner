from types import SimpleNamespace

import httpx

from control_plane.clients.http import RetryPolicy
from control_plane.clients.jenkins import JenkinsClient


def test_queue_snapshot_uses_task_label_expression(monkeypatch):
    payload = {
        "items": [
            {
                "task": {
                    "labelExpression": "linux-kvm",
                }
            }
        ]
    }

    def fake_request(_client, _method, _url, _retry, **_kwargs):
        return SimpleNamespace(json=lambda: payload)

    monkeypatch.setattr(
        "control_plane.clients.jenkins.request_with_retry", fake_request
    )
    client = JenkinsClient("http://jenkins:8080", "admin", "admin", RetryPolicy(1, 0))
    snapshot = client.queue_snapshot()
    assert snapshot.queued_by_label == {"linux-kvm": 1}


def test_queue_snapshot_prefers_assigned_label_name(monkeypatch):
    payload = {
        "items": [
            {
                "assignedLabel": {"name": "linux-nvmm"},
                "task": {"labelExpression": "linux-kvm"},
            }
        ]
    }

    def fake_request(_client, _method, _url, _retry, **_kwargs):
        return SimpleNamespace(json=lambda: payload)

    monkeypatch.setattr(
        "control_plane.clients.jenkins.request_with_retry", fake_request
    )
    client = JenkinsClient("http://jenkins:8080", "admin", "admin", RetryPolicy(1, 0))
    snapshot = client.queue_snapshot()
    assert snapshot.queued_by_label == {"linux-nvmm": 1}


def test_create_ephemeral_node_retries_with_crumb(monkeypatch):
    request = httpx.Request("POST", "http://jenkins:8080/computer/doCreateItem")
    response = httpx.Response(403, request=request, text="No valid crumb was included")
    cause = httpx.HTTPStatusError("forbidden", request=request, response=response)
    crumb_error = RuntimeError("request failed")
    crumb_error.__cause__ = cause

    calls: list[tuple[str, str, dict]] = []

    def fake_request(_client, method, url, _retry, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/computer/doCreateItem"):
            headers = kwargs.get("headers") or {}
            if headers.get("Jenkins-Crumb") != "crumb-token":
                raise crumb_error
            return SimpleNamespace()
        if method == "GET" and url.endswith("/crumbIssuer/api/json"):
            return SimpleNamespace(
                json=lambda: {
                    "crumbRequestField": "Jenkins-Crumb",
                    "crumb": "crumb-token",
                }
            )
        return SimpleNamespace()

    client = JenkinsClient("http://jenkins:8080", "admin", "admin", RetryPolicy(1, 0))

    def fake_client_request(method, url, **_kwargs):
        assert method == "POST"
        req = httpx.Request(method, url)
        return httpx.Response(403, request=req, text="No valid crumb was included")

    monkeypatch.setattr(client.client, "request", fake_client_request)
    monkeypatch.setattr(
        "control_plane.clients.jenkins.request_with_retry", fake_request
    )

    client.create_ephemeral_node("ephemeral-1", "linux-kvm")

    assert len(calls) == 2
    assert calls[0][1].endswith("/crumbIssuer/api/json")
    assert calls[1][2]["headers"]["Jenkins-Crumb"] == "crumb-token"
