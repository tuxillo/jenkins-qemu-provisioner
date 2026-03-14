from __future__ import annotations

import os
import re
import subprocess
from datetime import datetime
from pathlib import Path

from node_agent.host_stats.types import GenericHostStats


def storage_paths(settings) -> list[Path]:
    return [
        Path(settings.base_image_dir),
        Path(settings.overlay_dir),
        Path(settings.cloud_init_dir),
    ]


def normalize_df_device_name(source: str) -> str | None:
    basename = Path(source).name
    if not basename:
        return None
    match = re.match(r"([A-Za-z]+[0-9]+)", basename)
    if match:
        return match.group(1)
    return basename if basename else None


def df_mount_source(path: Path) -> str | None:
    try:
        completed = subprocess.run(
            ["df", "-P", str(path)],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return None

    lines = [line.strip() for line in completed.stdout.splitlines() if line.strip()]
    if len(lines) < 2:
        return None
    parts = lines[-1].split()
    if not parts:
        return None
    return parts[0]


def empty_stats(collected_at: datetime) -> GenericHostStats:
    return GenericHostStats(collected_at=collected_at, io_pressure=0.0)


def derive_disk_stats(
    *,
    previous_devices: dict[str, dict[str, float | int]],
    current_devices: dict[str, dict[str, float | int]],
    selected_devices: list[str],
    collected_at: datetime,
    elapsed_seconds: float,
) -> GenericHostStats:
    if elapsed_seconds <= 0:
        return empty_stats(collected_at)

    busy_fracs: list[float] = []
    total_read_bytes = 0.0
    total_write_bytes = 0.0

    for device_name in selected_devices:
        previous = previous_devices.get(device_name)
        current = current_devices.get(device_name)
        if previous is None or current is None:
            continue

        busy_delta = float(current["busy_seconds"]) - float(previous["busy_seconds"])
        read_delta = float(current["bytes_read"]) - float(previous["bytes_read"])
        write_delta = float(current["bytes_written"]) - float(previous["bytes_written"])
        busy_fracs.append(max(min(busy_delta / elapsed_seconds, 1.0), 0.0))
        total_read_bytes += max(read_delta, 0.0)
        total_write_bytes += max(write_delta, 0.0)

    if not busy_fracs:
        return empty_stats(collected_at)

    mib = 1024.0 * 1024.0
    disk_busy_frac = max(busy_fracs)
    return GenericHostStats(
        collected_at=collected_at,
        io_pressure=disk_busy_frac,
        disk_busy_frac=disk_busy_frac,
        disk_read_mb_s=total_read_bytes / elapsed_seconds / mib,
        disk_write_mb_s=total_write_bytes / elapsed_seconds / mib,
    )


def unique_preserving_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    ordered: list[str] = []
    for item in items:
        if not item or item in seen:
            continue
        seen.add(item)
        ordered.append(item)
    return ordered


def resolved_storage_paths(settings) -> list[Path]:
    resolved: list[Path] = []
    for path in storage_paths(settings):
        try:
            resolved.append(path.resolve())
        except OSError:
            resolved.append(path)
    return resolved


def same_or_child_path(path: str, mount_point: str) -> bool:
    if mount_point == "/":
        return path.startswith("/")
    return path == mount_point or path.startswith(f"{mount_point}/")


def path_device_id(path: Path) -> int | None:
    try:
        return os.stat(path).st_dev
    except OSError:
        return None
