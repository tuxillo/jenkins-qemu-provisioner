import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

from node_agent.config import get_agent_settings


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS vms (
  vm_id TEXT PRIMARY KEY,
  state TEXT NOT NULL,
  host_id TEXT,
  lease_id TEXT,
  qemu_pid INTEGER,
  overlay_path TEXT,
  cloud_init_iso TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  connect_deadline TEXT,
  lease_expires_at TEXT,
  reason TEXT
);
CREATE INDEX IF NOT EXISTS idx_vms_state ON vms(state);
"""


def _db_path() -> str:
    settings = get_agent_settings()
    path = Path(settings.state_db_path)
    if path.parent:
        path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)


@contextmanager
def connection():
    conn = sqlite3.connect(_db_path())
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL;")
        conn.execute("PRAGMA foreign_keys=ON;")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize_state() -> None:
    with connection() as conn:
        conn.executescript(SCHEMA_SQL)


def now_iso() -> str:
    return datetime.now(UTC).isoformat()


def upsert_vm(
    *,
    vm_id: str,
    state: str,
    host_id: str,
    lease_id: str | None,
    qemu_pid: int,
    overlay_path: str,
    cloud_init_iso: str,
    connect_deadline: str | None,
    lease_expires_at: str | None,
    reason: str | None,
) -> None:
    ts = now_iso()
    with connection() as conn:
        conn.execute(
            """
            INSERT INTO vms(vm_id,state,host_id,lease_id,qemu_pid,overlay_path,cloud_init_iso,created_at,updated_at,connect_deadline,lease_expires_at,reason)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(vm_id) DO UPDATE SET
              state=excluded.state,
              host_id=excluded.host_id,
              lease_id=excluded.lease_id,
              qemu_pid=excluded.qemu_pid,
              overlay_path=excluded.overlay_path,
              cloud_init_iso=excluded.cloud_init_iso,
              updated_at=excluded.updated_at,
              connect_deadline=excluded.connect_deadline,
              lease_expires_at=excluded.lease_expires_at,
              reason=excluded.reason
            """,
            (
                vm_id,
                state,
                host_id,
                lease_id,
                qemu_pid,
                overlay_path,
                cloud_init_iso,
                ts,
                ts,
                connect_deadline,
                lease_expires_at,
                reason,
            ),
        )


def update_vm_state(
    vm_id: str, state: str, reason: str | None = None, qemu_pid: int | None = None
) -> None:
    with connection() as conn:
        if qemu_pid is None:
            conn.execute(
                "UPDATE vms SET state=?, updated_at=?, reason=? WHERE vm_id=?",
                (state, now_iso(), reason, vm_id),
            )
        else:
            conn.execute(
                "UPDATE vms SET state=?, updated_at=?, reason=?, qemu_pid=? WHERE vm_id=?",
                (state, now_iso(), reason, qemu_pid, vm_id),
            )


def get_vm(vm_id: str) -> dict | None:
    with connection() as conn:
        row = conn.execute("SELECT * FROM vms WHERE vm_id=?", (vm_id,)).fetchone()
        return dict(row) if row else None


def list_vms() -> list[dict]:
    with connection() as conn:
        rows = conn.execute("SELECT * FROM vms ORDER BY updated_at DESC").fetchall()
        return [dict(r) for r in rows]


def delete_vm(vm_id: str) -> None:
    with connection() as conn:
        conn.execute("DELETE FROM vms WHERE vm_id=?", (vm_id,))
