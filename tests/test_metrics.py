import os

from fastapi.testclient import TestClient

from control_plane.config import get_settings

os.environ["DISABLE_BACKGROUND_LOOPS"] = "true"
get_settings.cache_clear()

from control_plane.metrics import metrics
from control_plane.main import app


def test_metrics_endpoint_exposes_counters():
    metrics.inc("launch_attempts_total", 2)
    client = TestClient(app)
    response = client.get("/metrics")
    assert response.status_code == 200
    body = response.json()
    assert body["launch_attempts_total"] >= 2
