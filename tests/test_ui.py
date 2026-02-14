import os

from fastapi.testclient import TestClient

from control_plane.config import get_settings

os.environ["DISABLE_BACKGROUND_LOOPS"] = "true"
get_settings.cache_clear()

from control_plane.db import Base, engine  # noqa: E402
from control_plane.main import app  # noqa: E402


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
