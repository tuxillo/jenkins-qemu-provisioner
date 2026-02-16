import json
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
    assert snapshot.queued_by_node == {}


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
    assert snapshot.queued_by_node == {}


def test_queue_snapshot_extracts_label_from_pipeline_why_message(monkeypatch):
    payload = {
        "items": [
            {
                "task": {
                    "name": "part of build #4",
                    "labelExpression": None,
                },
                "assignedLabel": None,
                "why": "\u2018Jenkins\u2019 doesn\u2019t have label \u2018dragonflybsd-nvmm\u2019",
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
    assert snapshot.queued_by_label == {"dragonflybsd-nvmm": 1}
    assert snapshot.queued_by_node == {}


def test_queue_snapshot_extracts_waiting_node_from_pipeline_why_message(monkeypatch):
    payload = {
        "items": [
            {
                "task": {
                    "name": "part of build #10",
                    "labelExpression": None,
                },
                "assignedLabel": None,
                "why": "Waiting for next available executor on \u2018ephemeral-842a2f6d7516\u2019",
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
    assert snapshot.queued_by_label == {}
    assert snapshot.queued_by_node == {"ephemeral-842a2f6d7516": 1}


def test_create_ephemeral_node_fetches_crumb_before_post(monkeypatch):
    calls: list[tuple[str, str, dict]] = []

    def fake_request(_client, method, url, _retry, **kwargs):
        calls.append((method, url, kwargs))
        if method == "POST" and url.endswith("/computer/doCreateItem"):
            headers = kwargs.get("headers") or {}
            assert headers.get("Jenkins-Crumb") == "crumb-token"
            payload = kwargs.get("data") or {}
            node_json = json.loads(payload.get("json", "{}"))
            assert node_json.get("launcher", {}).get("webSocket") is True
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


def test_create_ephemeral_node_allows_tcp_transport(monkeypatch):
    captured_payload: dict | None = None

    def fake_request(_client, method, url, _retry, **kwargs):
        nonlocal captured_payload
        if method == "GET" and url.endswith("/crumbIssuer/api/json"):
            return SimpleNamespace(
                json=lambda: {
                    "crumbRequestField": "Jenkins-Crumb",
                    "crumb": "crumb-token",
                }
            )
        if method == "POST" and url.endswith("/computer/doCreateItem"):
            payload = kwargs.get("data") or {}
            captured_payload = json.loads(payload.get("json", "{}"))
            return SimpleNamespace()
        return SimpleNamespace()

    monkeypatch.setattr(
        "control_plane.clients.jenkins.request_with_retry", fake_request
    )
    client = JenkinsClient("http://jenkins:8080", "admin", "admin", RetryPolicy(1, 0))
    client.create_ephemeral_node("ephemeral-1", "linux-kvm", use_websocket=False)

    assert captured_payload is not None
    assert captured_payload.get("launcher", {}).get("webSocket") is False


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


def test_node_runtime_status_maps_connected_and_busy(monkeypatch):
    def fake_request(_client, method, _url, _retry, **_kwargs):
        assert method == "GET"
        return SimpleNamespace(json=lambda: {"offline": False, "idle": False})

    monkeypatch.setattr(
        "control_plane.clients.jenkins.request_with_retry", fake_request
    )
    client = JenkinsClient("http://jenkins:8080", "admin", "admin", RetryPolicy(1, 0))
    status = client.node_runtime_status("ephemeral-1")
    assert status.connected is True
    assert status.busy is True


def test_node_current_build_url_reads_executor_payload(monkeypatch):
    def fake_request(_client, method, _url, _retry, **_kwargs):
        assert method == "GET"
        return SimpleNamespace(
            json=lambda: {
                "executors": [
                    {
                        "currentExecutable": {
                            "url": "http://jenkins:8080/job/fake/15/",
                        }
                    }
                ],
                "oneOffExecutors": [],
            }
        )

    monkeypatch.setattr(
        "control_plane.clients.jenkins.request_with_retry", fake_request
    )
    client = JenkinsClient("http://jenkins:8080", "admin", "admin", RetryPolicy(1, 0))
    assert (
        client.node_current_build_url("ephemeral-1")
        == "http://jenkins:8080/job/fake/15/"
    )


def test_is_build_running_checks_building_flag(monkeypatch):
    calls: list[str] = []

    def fake_request(_client, method, url, _retry, **_kwargs):
        calls.append(url)
        assert method == "GET"
        return SimpleNamespace(json=lambda: {"building": False, "result": "SUCCESS"})

    monkeypatch.setattr(
        "control_plane.clients.jenkins.request_with_retry", fake_request
    )
    client = JenkinsClient("http://jenkins:8080", "admin", "admin", RetryPolicy(1, 0))
    assert client.is_build_running("http://jenkins:8080/job/fake/15") is False
    assert calls
    assert calls[0].endswith("/api/json?tree=building,result")
