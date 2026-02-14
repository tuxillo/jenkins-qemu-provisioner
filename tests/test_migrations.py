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
