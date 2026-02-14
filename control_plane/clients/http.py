import time
from typing import Any

import httpx


class RetryPolicy:
    def __init__(self, attempts: int, sleep_sec: int):
        self.attempts = attempts
        self.sleep_sec = sleep_sec


def request_with_retry(
    client: httpx.Client, method: str, url: str, retry: RetryPolicy, **kwargs: Any
) -> httpx.Response:
    error: Exception | None = None
    for attempt in range(1, retry.attempts + 1):
        try:
            response = client.request(method, url, **kwargs)
            response.raise_for_status()
            return response
        except Exception as exc:  # noqa: BLE001
            error = exc
            if attempt < retry.attempts:
                time.sleep(retry.sleep_sec)
    raise RuntimeError(
        f"request failed after {retry.attempts} attempts: {method} {url}"
    ) from error
