from __future__ import annotations

from node_agent.host_stats.backends.dragonflybsd import DragonFlyBSDStatsBackend
from node_agent.host_stats.backends.linux import LinuxStatsBackend
from node_agent.host_stats.backends.null import NullStatsBackend
from node_agent.host_stats.types import PlatformStatsBackend


def create_platform_stats_backend(settings) -> PlatformStatsBackend:
    if settings.os_flavor == "dragonflybsd":
        return DragonFlyBSDStatsBackend(settings)
    if settings.os_family == "linux" or settings.os_flavor == "linux":
        return LinuxStatsBackend(settings)
    return NullStatsBackend()
