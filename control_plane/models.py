from datetime import datetime
from enum import Enum

from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from control_plane.db import Base


class LeaseState(str, Enum):
    REQUESTED = "REQUESTED"
    PROVISIONING = "PROVISIONING"
    BOOTING = "BOOTING"
    CONNECTED = "CONNECTED"
    RUNNING = "RUNNING"
    TERMINATING = "TERMINATING"
    TERMINATED = "TERMINATED"
    FAILED = "FAILED"
    ORPHANED = "ORPHANED"


class Lease(Base):
    __tablename__ = "leases"
    __table_args__ = (
        UniqueConstraint("vm_id", name="uq_leases_vm_id"),
        UniqueConstraint("jenkins_node", name="uq_leases_jenkins_node"),
    )

    lease_id: Mapped[str] = mapped_column(String(64), primary_key=True)
    vm_id: Mapped[str] = mapped_column(String(64), nullable=False)
    label: Mapped[str] = mapped_column(String(128), nullable=False)
    jenkins_node: Mapped[str] = mapped_column(String(128), nullable=False)
    state: Mapped[str] = mapped_column(
        String(32), default=LeaseState.REQUESTED.value, nullable=False
    )
    host_id: Mapped[str | None] = mapped_column(String(128))

    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    connect_deadline: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    ttl_deadline: Mapped[datetime] = mapped_column(DateTime, nullable=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime)
    bound_build_url: Mapped[str | None] = mapped_column(Text)
    last_heartbeat: Mapped[datetime | None] = mapped_column(DateTime)
    last_error: Mapped[str | None] = mapped_column(Text)


class Host(Base):
    __tablename__ = "hosts"

    host_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)
    bootstrap_token_hash: Mapped[str | None] = mapped_column(String(256))
    session_token_hash: Mapped[str | None] = mapped_column(String(256))
    session_expires_at: Mapped[datetime | None] = mapped_column(DateTime)

    os_family: Mapped[str | None] = mapped_column(String(64))
    os_flavor: Mapped[str | None] = mapped_column(String(64))
    os_version: Mapped[str | None] = mapped_column(String(128))
    cpu_arch: Mapped[str | None] = mapped_column(String(64))
    addr: Mapped[str | None] = mapped_column(String(256))
    qemu_binary: Mapped[str | None] = mapped_column(String(256))
    supported_accels: Mapped[str | None] = mapped_column(Text)
    selected_accel: Mapped[str | None] = mapped_column(String(32))

    cpu_total: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    cpu_free: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ram_total_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    ram_free_mb: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    io_pressure: Mapped[float] = mapped_column(Float, default=0.0, nullable=False)
    last_seen: Mapped[datetime | None] = mapped_column(DateTime)


class Event(Base):
    __tablename__ = "events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    timestamp: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
    lease_id: Mapped[str | None] = mapped_column(
        String(64), ForeignKey("leases.lease_id")
    )
    event_type: Mapped[str] = mapped_column(String(128), nullable=False)
    payload_json: Mapped[str] = mapped_column(Text, nullable=False)


class SchemaMigration(Base):
    __tablename__ = "schema_migrations"

    version: Mapped[str] = mapped_column(String(32), primary_key=True)
    applied_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.utcnow, nullable=False
    )
