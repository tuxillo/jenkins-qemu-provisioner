from datetime import UTC, datetime

from node_agent.host_stats.service import HostStatsService
from node_agent.host_stats.types import GenericHostStats, RawPlatformSample


class _FakeBackend:
    def __init__(self) -> None:
        self.calls = 0

    def sample(self) -> RawPlatformSample:
        self.calls += 1
        return RawPlatformSample(
            collected_at=datetime.now(UTC), payload={"calls": self.calls}
        )

    def derive(
        self,
        previous: RawPlatformSample | None,
        current: RawPlatformSample,
    ) -> GenericHostStats:
        _ = previous
        return GenericHostStats(
            collected_at=current.collected_at,
            io_pressure=1.5,
            disk_busy_frac=-1.0,
            disk_read_mb_s=12.5,
            disk_write_mb_s=-3.0,
        )


def test_host_stats_service_sanitizes_generic_metrics() -> None:
    service = HostStatsService(backend=_FakeBackend())

    stats = service.refresh_now()

    assert stats.io_pressure == 1.0
    assert stats.disk_busy_frac == 0.0
    assert stats.disk_read_mb_s == 12.5
    assert stats.disk_write_mb_s == 0.0


def test_host_stats_service_latest_returns_cached_snapshot() -> None:
    backend = _FakeBackend()
    service = HostStatsService(backend=backend)

    first = service.latest()
    second = service.latest()

    assert backend.calls == 1
    assert first == second
