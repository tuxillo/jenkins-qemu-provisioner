from types import SimpleNamespace

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


def test_create_ephemeral_node_fetches_crumb_before_post(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    def fake_request(_client, method, url, _retry, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/computer/doCreateItem"):
            headers = kwargs.get("headers") or {}
            assert headers.get("Jenkins-Crumb") == "crumb-token"
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
    monkeypatch.setattr(
        "control_plane.clients.jenkins.request_with_retry", fake_request
    )

    client.create_ephemeral_node("ephemeral-1", "linux-kvm")

    assert len(calls) == 2
    assert calls[0][1].endswith("/crumbIssuer/api/json")
    assert calls[1][2]["headers"]["Jenkins-Crumb"] == "crumb-token"


def test_get_inbound_secret_prefers_json_api(monkeypatch):
    calls: list[str] = []

    def fake_request(_client, method, url, _retry, **_kwargs):
        calls.append(url)
        assert method == "GET"
        if "api/json?tree=jnlpMac" in url:
            return SimpleNamespace(json=lambda: {"jnlpMac": "secret-from-json"})
        raise AssertionError("JNLP fallback should not be used")

    monkeypatch.setattr(
        "control_plane.clients.jenkins.request_with_retry", fake_request
    )
    client = JenkinsClient("http://jenkins:8080", "admin", "admin", RetryPolicy(1, 0))
    secret = client.get_inbound_secret("ephemeral-1")
    assert secret == "secret-from-json"
    assert any("api/json?tree=jnlpMac" in url for url in calls)
