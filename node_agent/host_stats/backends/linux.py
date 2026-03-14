from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from node_agent.host_stats.backends.common import (
    derive_disk_stats,
    empty_stats,
    path_device_id,
    resolved_storage_paths,
    same_or_child_path,
    unique_preserving_order,
)
from node_agent.host_stats.types import RawPlatformSample


class LinuxStatsBackend:
    def __init__(self, settings) -> None:
        self._settings = settings

    def sample(self) -> RawPlatformSample:
        collected_at = datetime.now(UTC)
        diskstats = _read_diskstats()
        selected_devices = _resolve_linux_devices(
            resolved_storage_paths(self._settings), diskstats
        )
        if not selected_devices:
            selected_devices = list(diskstats.keys())
        devices = {
            name: diskstats[name] for name in selected_devices if name in diskstats
        }
        return RawPlatformSample(
            collected_at=collected_at,
            payload={
                "selected_devices": selected_devices,
                "devices": devices,
            },
        )

    def derive(
        self,
        previous: RawPlatformSample | None,
        current: RawPlatformSample,
    ):
        if previous is None:
            return empty_stats(current.collected_at)

        previous_devices = _device_payload(previous)
        current_devices = _device_payload(current)
        selected_devices = list(current.payload.get("selected_devices", []))
        elapsed_seconds = (current.collected_at - previous.collected_at).total_seconds()
        return derive_disk_stats(
            previous_devices=previous_devices,
            current_devices=current_devices,
            selected_devices=selected_devices,
            collected_at=current.collected_at,
            elapsed_seconds=elapsed_seconds,
        )


def _device_payload(sample: RawPlatformSample) -> dict[str, dict[str, float | int]]:
    payload = sample.payload.get("devices", {})
    return payload if isinstance(payload, dict) else {}


def _read_diskstats() -> dict[str, dict[str, int | float | str]]:
    diskstats: dict[str, dict[str, int | float | str]] = {}
    path = Path("/proc/diskstats")
    if not path.exists():
        return diskstats

    for line in path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if len(parts) < 14:
            continue
        name = parts[2]
        values = [int(value) for value in parts[3:]]
        if len(values) < 10:
            continue
        diskstats[name] = {
            "major": int(parts[0]),
            "minor": int(parts[1]),
            "bytes_read": values[2] * 512,
            "bytes_written": values[6] * 512,
            "busy_seconds": values[9] / 1000.0,
        }
    return diskstats


def _read_mountinfo() -> list[dict[str, str]]:
    mountinfo_path = Path("/proc/self/mountinfo")
    if not mountinfo_path.exists():
        return []

    mounts: list[dict[str, str]] = []
    for line in mountinfo_path.read_text(encoding="utf-8").splitlines():
        parts = line.split()
        if "-" not in parts or len(parts) < 7:
            continue
        sep = parts.index("-")
        if sep + 2 >= len(parts):
            continue
        mount_point = parts[4].replace("\\040", " ")
        mounts.append(
            {
                "major_minor": parts[2],
                "mount_point": mount_point,
                "source": parts[sep + 2],
            }
        )
    mounts.sort(key=lambda item: len(item["mount_point"]), reverse=True)
    return mounts


def _resolve_linux_devices(
    paths: list[Path],
    diskstats: dict[str, dict[str, int | float | str]],
) -> list[str]:
    mounts = _read_mountinfo()
    major_minor_to_name = {
        f"{int(values['major'])}:{int(values['minor'])}": name
        for name, values in diskstats.items()
    }

    selected: list[str] = []
    for path in paths:
        device_id = path_device_id(path)
        matched_mount = _find_mount_for_path(path, mounts, device_id)
        if matched_mount is None:
            continue
        device_name = major_minor_to_name.get(matched_mount["major_minor"])
        if device_name:
            selected.append(device_name)
            continue
        source = matched_mount.get("source", "")
        source_name = Path(source).name if source.startswith("/dev/") else source
        if source_name in diskstats:
            selected.append(source_name)
    return unique_preserving_order(selected)


def _find_mount_for_path(
    path: Path,
    mounts: list[dict[str, str]],
    device_id: int | None,
) -> dict[str, str] | None:
    path_text = str(path)
    candidate = None
    for mount in mounts:
        mount_point = mount["mount_point"]
        if not same_or_child_path(path_text, mount_point):
            continue
        if device_id is None:
            return mount
        try:
            if path_device_id(Path(mount_point)) == device_id:
                return mount
        except OSError:
            pass
        if candidate is None:
            candidate = mount
    return candidate
