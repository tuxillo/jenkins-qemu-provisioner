import ctypes
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from node_agent.host_stats.backends.dragonflybsd import (
    DragonFlyBSDStatsBackend,
    _Devstat,
    _Timeval,
    _read_dragonfly_devices,
)
from node_agent.host_stats.backends.linux import (
    LinuxStatsBackend,
    _resolve_linux_devices,
)
from node_agent.host_stats.types import RawPlatformSample


def test_linux_resolves_storage_devices_from_mountinfo(monkeypatch) -> None:
    monkeypatch.setattr(
        "node_agent.host_stats.backends.linux._read_mountinfo",
        lambda: [
            {
                "major_minor": "8:1",
                "mount_point": "/var/lib/jenkins-qemu",
                "source": "/dev/sda1",
            }
        ],
    )
    monkeypatch.setattr(
        "node_agent.host_stats.backends.linux.path_device_id",
        lambda _path: 100,
    )

    devices = _resolve_linux_devices(
        [Path("/var/lib/jenkins-qemu/overlays")],
        {
            "sda1": {
                "major": 8,
                "minor": 1,
                "bytes_read": 0,
                "bytes_written": 0,
                "busy_seconds": 0.0,
            }
        },
    )

    assert devices == ["sda1"]


def test_linux_backend_derive_computes_busy_and_throughput() -> None:
    backend = LinuxStatsBackend(SimpleNamespace())
    previous = RawPlatformSample(
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        payload={
            "selected_devices": ["nvme0n1"],
            "devices": {
                "nvme0n1": {
                    "bytes_read": 1_000_000,
                    "bytes_written": 2_000_000,
                    "busy_seconds": 10.0,
                }
            },
        },
    )
    current = RawPlatformSample(
        collected_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=2),
        payload={
            "selected_devices": ["nvme0n1"],
            "devices": {
                "nvme0n1": {
                    "bytes_read": 5_194_304,
                    "bytes_written": 4_097_152,
                    "busy_seconds": 11.0,
                }
            },
        },
    )

    stats = backend.derive(previous, current)

    assert stats.io_pressure == 0.5
    assert stats.disk_busy_frac == 0.5
    assert stats.disk_read_mb_s == 2.0
    assert stats.disk_write_mb_s == 1.0


def test_dragonfly_reads_devstat_entries(monkeypatch) -> None:
    first = _Devstat()
    first.device_name = b"da"
    first.unit_number = 0
    first.bytes_read = 1024
    first.bytes_written = 2048
    first.busy_time = _Timeval(3, 500000)

    second = _Devstat()
    second.device_name = b"nvme"
    second.unit_number = 1
    second.bytes_read = 4096
    second.bytes_written = 8192
    second.busy_time = _Timeval(5, 0)

    generation_size = ctypes.sizeof(ctypes.c_long)
    blob = (
        (1).to_bytes(generation_size, byteorder="little", signed=True)
        + bytes(first)
        + bytes(second)
    )
    monkeypatch.setattr(
        "node_agent.host_stats.backends.dragonflybsd._sysctl_bytes",
        lambda _name: blob,
    )

    devices = _read_dragonfly_devices()

    assert devices["da0"]["bytes_read"] == 1024
    assert devices["da0"]["bytes_written"] == 2048
    assert devices["da0"]["busy_seconds"] == 3.5
    assert devices["nvme1"]["busy_seconds"] == 5.0


def test_dragonfly_backend_derive_computes_busy_and_throughput() -> None:
    backend = DragonFlyBSDStatsBackend(SimpleNamespace())
    previous = RawPlatformSample(
        collected_at=datetime(2026, 1, 1, tzinfo=UTC),
        payload={
            "selected_devices": ["da0"],
            "devices": {
                "da0": {
                    "bytes_read": 0,
                    "bytes_written": 0,
                    "busy_seconds": 1.0,
                }
            },
        },
    )
    current = RawPlatformSample(
        collected_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=4),
        payload={
            "selected_devices": ["da0"],
            "devices": {
                "da0": {
                    "bytes_read": 8_388_608,
                    "bytes_written": 4_194_304,
                    "busy_seconds": 3.0,
                }
            },
        },
    )

    stats = backend.derive(previous, current)

    assert stats.io_pressure == 0.5
    assert stats.disk_busy_frac == 0.5
    assert stats.disk_read_mb_s == 2.0
    assert stats.disk_write_mb_s == 1.0
