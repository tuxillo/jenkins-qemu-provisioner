import httpx
import pytest

from control_plane.clients.http import RetryPolicy, request_with_retry


def test_request_with_retry_raises_after_attempts():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(status_code=500, request=request)
    )
    client = httpx.Client(transport=transport)
    with pytest.raises(RuntimeError):
        request_with_retry(
            client, "GET", "http://example.test", RetryPolicy(attempts=2, sleep_sec=0)
        )


def test_request_with_retry_succeeds():
    transport = httpx.MockTransport(
        lambda request: httpx.Response(
            status_code=200, json={"ok": True}, request=request
        )
    )
    client = httpx.Client(transport=transport)
    response = request_with_retry(
        client, "GET", "http://example.test", RetryPolicy(attempts=2, sleep_sec=0)
    )
    assert response.json() == {"ok": True}
