from __future__ import annotations

from datetime import UTC, datetime

from node_agent.host_stats.types import GenericHostStats, RawPlatformSample


class NullStatsBackend:
    def sample(self) -> RawPlatformSample:
        return RawPlatformSample(collected_at=datetime.now(UTC))

    def derive(
        self,
        previous: RawPlatformSample | None,
        current: RawPlatformSample,
    ) -> GenericHostStats:
        _ = previous
        return GenericHostStats(collected_at=current.collected_at, io_pressure=0.0)
