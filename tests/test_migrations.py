from sqlalchemy import inspect

from control_plane.db import engine
from control_plane.scripts.init_db import apply_migrations


def test_apply_migrations_creates_tables():
    apply_migrations()
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    assert "leases" in tables
    assert "hosts" in tables
    assert "events" in tables
    assert "schema_migrations" in tables

    host_columns = {column["name"] for column in inspector.get_columns("hosts")}
    assert "cpu_allocatable" in host_columns
    assert "ram_allocatable_mb" in host_columns
    assert "available_images_json" in host_columns

    lease_columns = {column["name"] for column in inspector.get_columns("leases")}
    assert "guest_image" in lease_columns
    assert "base_image_id" in lease_columns
