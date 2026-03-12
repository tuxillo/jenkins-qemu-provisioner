import json
import os
import re

from fastapi.testclient import TestClient

from control_plane.config import get_settings

os.environ["DISABLE_BACKGROUND_LOOPS"] = "true"
get_settings.cache_clear()

from control_plane.db import Base, SessionLocal, engine  # noqa: E402
from control_plane.main import app  # noqa: E402
from control_plane.models import Event, Host  # noqa: E402


def setup_function() -> None:
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)


def test_ui_route_embeds_snapshot_and_assets() -> None:
    client = TestClient(app)
    response = client.get("/ui")
    assert response.status_code == 200
    body = response.text
    assert 'id="cp-snapshot"' in body
    assert "/static/ui.css" in body
    assert "/static/ui.js" in body
    assert "generated_at" in body


def test_static_assets_are_served() -> None:
    client = TestClient(app)
    js = client.get("/static/ui.js")
    css = client.get("/static/ui.css")
    assert js.status_code == 200
    assert css.status_code == 200


def test_ui_snapshot_filters_noisy_heartbeat_events() -> None:
    with SessionLocal() as db:
        db.add(Event(event_type="host.heartbeat", payload_json="{}"))
        db.add(Event(event_type="lease.created", payload_json="{}"))
        db.commit()

    client = TestClient(app)
    response = client.get("/ui")
    assert response.status_code == 200

    match = re.search(
        r'<script id="cp-snapshot" type="application/json">(.*)</script>',
        response.text,
    )
    assert match is not None

    snapshot = json.loads(match.group(1))
    event_types = [event["event_type"] for event in snapshot["events"]]

    assert "lease.created" in event_types
    assert "host.heartbeat" not in event_types


def test_ui_snapshot_escapes_html_like_event_payload() -> None:
    payload = {
        "error": "oops </script><script>alert(1)</script>",
        "host_id": "h1",
    }
    with SessionLocal() as db:
        db.add(Event(event_type="lease.failed", payload_json=json.dumps(payload)))
        db.commit()

    client = TestClient(app)
    response = client.get("/ui")
    assert response.status_code == 200
    assert "</script><script>alert(1)</script>" not in response.text
    assert "\\u003c/script>" in response.text

    match = re.search(
        r'<script id="cp-snapshot" type="application/json">(.*)</script>',
        response.text,
    )
    assert match is not None
    snapshot = json.loads(match.group(1))
    assert snapshot["events"][0]["event_type"] == "lease.failed"


def test_ui_snapshot_includes_allocatable_host_capacity() -> None:
    with SessionLocal() as db:
        db.add(
            Host(
                host_id="host-a",
                enabled=True,
                cpu_total=16,
                cpu_allocatable=12,
                cpu_free=8,
                ram_total_mb=32768,
                ram_allocatable_mb=24576,
                ram_free_mb=16384,
            )
        )
        db.commit()

    client = TestClient(app)
    response = client.get("/ui")
    assert response.status_code == 200

    match = re.search(
        r'<script id="cp-snapshot" type="application/json">(.*)</script>',
        response.text,
    )
    assert match is not None

    snapshot = json.loads(match.group(1))
    host = snapshot["hosts"][0]
    assert host["cpu_total"] == 16
    assert host["cpu_allocatable"] == 12
    assert host["cpu_free"] == 8
    assert host["ram_total_mb"] == 32768
    assert host["ram_allocatable_mb"] == 24576
    assert host["ram_free_mb"] == 16384
