from __future__ import annotations

import logging
import threading
import time

from node_agent.config import get_agent_settings
from node_agent.host_stats.factory import create_platform_stats_backend
from node_agent.host_stats.types import GenericHostStats, default_host_stats


logger = logging.getLogger(__name__)


def _clamp_fraction(value: float | None) -> float | None:
    if value is None:
        return None
    return max(0.0, min(float(value), 1.0))


class HostStatsService:
    def __init__(self, backend=None) -> None:
        settings = get_agent_settings()
        self._backend = backend or create_platform_stats_backend(settings)
        self._lock = threading.Lock()
        self._raw_sample = None
        self._stats = default_host_stats()
        self._initialized = False

    def latest(self) -> GenericHostStats:
        with self._lock:
            if self._initialized:
                return self._stats
        return self.refresh_now()

    def refresh_now(self) -> GenericHostStats:
        with self._lock:
            previous = self._raw_sample

        current = self._backend.sample()
        derived = self._backend.derive(previous, current)
        stats = GenericHostStats(
            collected_at=derived.collected_at,
            io_pressure=_clamp_fraction(derived.io_pressure) or 0.0,
            disk_busy_frac=_clamp_fraction(derived.disk_busy_frac),
            disk_read_mb_s=(
                None
                if derived.disk_read_mb_s is None
                else max(float(derived.disk_read_mb_s), 0.0)
            ),
            disk_write_mb_s=(
                None
                if derived.disk_write_mb_s is None
                else max(float(derived.disk_write_mb_s), 0.0)
            ),
        )

        with self._lock:
            self._raw_sample = current
            self._stats = stats
            self._initialized = True
            return self._stats

    def worker(self, stop_event: threading.Event) -> None:
        settings = get_agent_settings()
        while not stop_event.is_set():
            try:
                self.refresh_now()
            except Exception as exc:  # noqa: BLE001
                logger.warning("host-stats refresh failed: %s", exc)
            stop_event.wait(settings.host_stats_interval_sec)


_service: HostStatsService | None = None


def get_host_stats_service() -> HostStatsService:
    global _service
    if _service is None:
        _service = HostStatsService()
    return _service


def reset_host_stats_service() -> None:
    global _service
    _service = None


def start_host_stats_thread(stop_event: threading.Event) -> threading.Thread:
    thread = threading.Thread(
        target=get_host_stats_service().worker,
        args=(stop_event,),
        name="host-stats-worker",
        daemon=True,
    )
    thread.start()
    time.sleep(0.01)
    return thread
