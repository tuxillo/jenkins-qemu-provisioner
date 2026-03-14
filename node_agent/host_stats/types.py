from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class GenericHostStats:
    collected_at: datetime
    io_pressure: float
    disk_busy_frac: float | None = None
    disk_read_mb_s: float | None = None
    disk_write_mb_s: float | None = None


@dataclass(frozen=True)
class RawPlatformSample:
    collected_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)


class PlatformStatsBackend(Protocol):
    def sample(self) -> RawPlatformSample: ...

    def derive(
        self,
        previous: RawPlatformSample | None,
        current: RawPlatformSample,
    ) -> GenericHostStats: ...


def default_host_stats() -> GenericHostStats:
    return GenericHostStats(collected_at=datetime.now(UTC), io_pressure=0.0)
