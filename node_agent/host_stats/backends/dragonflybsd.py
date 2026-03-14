from __future__ import annotations

import ctypes
from datetime import UTC, datetime

from node_agent.host_stats.backends.common import (
    derive_disk_stats,
    df_mount_source,
    empty_stats,
    normalize_df_device_name,
    resolved_storage_paths,
    unique_preserving_order,
)
from node_agent.host_stats.types import RawPlatformSample


class _STailQEntry(ctypes.Structure):
    _fields_ = [("stqe_next", ctypes.c_void_p)]


class _Timeval(ctypes.Structure):
    _fields_ = [("tv_sec", ctypes.c_long), ("tv_usec", ctypes.c_long)]


class _Devstat(ctypes.Structure):
    _fields_ = [
        ("dev_links", _STailQEntry),
        ("device_number", ctypes.c_uint32),
        ("device_name", ctypes.c_char * 16),
        ("unit_number", ctypes.c_int32),
        ("bytes_read", ctypes.c_uint64),
        ("bytes_written", ctypes.c_uint64),
        ("bytes_freed", ctypes.c_uint64),
        ("num_reads", ctypes.c_uint64),
        ("num_writes", ctypes.c_uint64),
        ("num_frees", ctypes.c_uint64),
        ("num_other", ctypes.c_uint64),
        ("busy_count", ctypes.c_int32),
        ("block_size", ctypes.c_uint32),
        ("tag_types", ctypes.c_uint64 * 3),
        ("dev_creation_time", _Timeval),
        ("busy_time", _Timeval),
        ("start_time", _Timeval),
        ("last_comp_time", _Timeval),
        ("flags", ctypes.c_int),
        ("device_type", ctypes.c_int),
        ("priority", ctypes.c_int),
    ]


class DragonFlyBSDStatsBackend:
    def __init__(self, settings) -> None:
        self._settings = settings

    def sample(self) -> RawPlatformSample:
        collected_at = datetime.now(UTC)
        devices = _read_dragonfly_devices()
        selected_devices = _resolve_dragonfly_devices(self._settings, devices)
        if not selected_devices:
            selected_devices = list(devices.keys())
        return RawPlatformSample(
            collected_at=collected_at,
            payload={
                "selected_devices": selected_devices,
                "devices": {
                    name: devices[name] for name in selected_devices if name in devices
                },
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


def _read_dragonfly_devices() -> dict[str, dict[str, float | int]]:
    raw = _sysctl_bytes("kern.devstat.all")
    generation_size = ctypes.sizeof(ctypes.c_long)
    if len(raw) <= generation_size:
        return {}
    entries_blob = raw[generation_size:]
    entry_size = ctypes.sizeof(_Devstat)
    devices: dict[str, dict[str, float | int]] = {}
    for offset in range(
        0, len(entries_blob) - (len(entries_blob) % entry_size), entry_size
    ):
        entry = _Devstat.from_buffer_copy(entries_blob[offset : offset + entry_size])
        base_name = (
            bytes(entry.device_name)
            .split(b"\x00", 1)[0]
            .decode("ascii", errors="ignore")
        )
        if not base_name:
            continue
        device_name = f"{base_name}{entry.unit_number}"
        devices[device_name] = {
            "bytes_read": int(entry.bytes_read),
            "bytes_written": int(entry.bytes_written),
            "busy_seconds": _timeval_to_seconds(entry.busy_time),
        }
    return devices


def _resolve_dragonfly_devices(
    settings, devices: dict[str, dict[str, float | int]]
) -> list[str]:
    selected: list[str] = []
    for path in resolved_storage_paths(settings):
        source = df_mount_source(path)
        if not source:
            continue
        device_name = normalize_df_device_name(source)
        if device_name and device_name in devices:
            selected.append(device_name)
    return unique_preserving_order(selected)


def _timeval_to_seconds(value: _Timeval) -> float:
    return float(value.tv_sec) + (float(value.tv_usec) / 1_000_000.0)


def _sysctl_bytes(name: str) -> bytes:
    libc = ctypes.CDLL(None, use_errno=True)
    sysctlbyname = libc.sysctlbyname
    sysctlbyname.argtypes = [
        ctypes.c_char_p,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
        ctypes.c_void_p,
        ctypes.c_size_t,
    ]
    sysctlbyname.restype = ctypes.c_int

    size = ctypes.c_size_t(0)
    if sysctlbyname(name.encode("ascii"), None, ctypes.byref(size), None, 0) != 0:
        err = ctypes.get_errno()
        raise OSError(err, f"sysctlbyname({name}) size failed")

    buffer = ctypes.create_string_buffer(size.value)
    if (
        sysctlbyname(
            name.encode("ascii"),
            buffer,
            ctypes.byref(size),
            None,
            0,
        )
        != 0
    ):
        err = ctypes.get_errno()
        raise OSError(err, f"sysctlbyname({name}) data failed")

    return bytes(buffer.raw[: size.value])
