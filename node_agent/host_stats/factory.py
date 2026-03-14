from __future__ import annotations

from node_agent.host_stats.backends.null import NullStatsBackend
from node_agent.host_stats.types import PlatformStatsBackend


def create_platform_stats_backend(_settings) -> PlatformStatsBackend:
    return NullStatsBackend()
