import logging
import threading
import time
from urllib.parse import urlparse

from control_plane.clients.http import RetryPolicy
from control_plane.clients.jenkins import JenkinsClient
from control_plane.clients.node_agent import NodeAgentClient
from control_plane.config import get_settings
from control_plane.db import session_scope
from control_plane.models import Host
from control_plane.services.gc import gc_hosts_once
from control_plane.services.reconciler import reconcile_once
from control_plane.services.scaler import scale_once


logger = logging.getLogger(__name__)


def _normalize_node_agent_url(raw_addr: str | None, fallback_url: str) -> str:
    if not raw_addr:
        return fallback_url
    if raw_addr.startswith("http://") or raw_addr.startswith("https://"):
        return raw_addr
    return f"http://{raw_addr}"


def _node_agent_factory(host_id: str) -> NodeAgentClient:
    settings = get_settings()
    retry = RetryPolicy(settings.retry_attempts, settings.retry_sleep_sec)

    base_url = settings.node_agent_url
    if host_id:
        with session_scope() as session:
            host = session.get(Host, host_id)
            if host and host.addr:
                base_url = _normalize_node_agent_url(host.addr, settings.node_agent_url)

    parsed = urlparse(base_url)
    if not parsed.hostname:
        logger.warning(
            "node-agent url missing hostname host_id=%s url=%s", host_id, base_url
        )

    return NodeAgentClient(
        base_url=base_url,
        retry=retry,
        auth_token=settings.node_agent_auth_token,
    )


def _build_jenkins_client() -> JenkinsClient:
    settings = get_settings()
    retry = RetryPolicy(settings.retry_attempts, settings.retry_sleep_sec)
    return JenkinsClient(
        base_url=settings.jenkins_url,
        user=settings.jenkins_user,
        api_token=settings.jenkins_api_token,
        retry=retry,
    )


def start_loops(stop_event: threading.Event) -> list[threading.Thread]:
    settings = get_settings()
    jenkins = _build_jenkins_client()

    def scaling_worker() -> None:
        while not stop_event.is_set():
            try:
                scale_once(jenkins, _node_agent_factory)
                reconcile_once(jenkins, _node_agent_factory)
            except Exception as exc:  # noqa: BLE001
                logger.exception("scaling/reconcile tick failed: %s", exc)
            stop_event.wait(settings.loop_interval_sec)

    def gc_worker() -> None:
        while not stop_event.is_set():
            try:
                gc_hosts_once()
            except Exception as exc:  # noqa: BLE001
                logger.exception("gc tick failed: %s", exc)
            stop_event.wait(settings.gc_interval_sec)

    t1 = threading.Thread(target=scaling_worker, name="scaling-worker", daemon=True)
    t2 = threading.Thread(target=gc_worker, name="gc-worker", daemon=True)
    t1.start()
    t2.start()
    time.sleep(0.01)
    return [t1, t2]
