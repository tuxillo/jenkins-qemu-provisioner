import time
from typing import Any

import httpx


class RetryPolicy:
    def __init__(self, attempts: int, sleep_sec: int):
        self.attempts = attempts
        self.sleep_sec = sleep_sec


class RequestFailure(RuntimeError):
    def __init__(
        self,
        *,
        method: str,
        url: str,
        attempts: int,
        error_type: str,
        detail: str,
        status_code: int | None = None,
        response_text: str | None = None,
    ):
        self.method = method
        self.url = url
        self.attempts = attempts
        self.error_type = error_type
        self.detail = detail
        self.status_code = status_code
        self.response_text = response_text
        super().__init__(
            f"request failed after {attempts} attempts: {method} {url} ({error_type}: {detail})"
        )


def request_with_retry(
    client: httpx.Client, method: str, url: str, retry: RetryPolicy, **kwargs: Any
) -> httpx.Response:
    error: Exception | None = None
    status_code: int | None = None
    response_text: str | None = None
    detail = "unknown error"
    error_type = "RuntimeError"
    for attempt in range(1, retry.attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except httpx.HTTPStatusError as exc:
            error = exc
            status_code = exc.response.status_code
            response_text = exc.response.text
            body = (exc.response.text or "").strip()
            detail = (
                f"HTTP {status_code}: {body[:240]}" if body else f"HTTP {status_code}"
            )
            error_type = exc.__class__.__name__
        except httpx.RequestError as exc:
            error = exc
            detail = str(exc)
            error_type = exc.__class__.__name__
        except Exception as exc:  # noqa: BLE001
            error = exc
            detail = str(exc)
            error_type = exc.__class__.__name__
        if attempt < retry.attempts:
            time.sleep(retry.sleep_sec)
    raise RequestFailure(
        method=method,
        url=url,
        attempts=retry.attempts,
        error_type=error_type,
        detail=detail,
        status_code=status_code,
        response_text=response_text,
    ) from error
