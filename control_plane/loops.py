import logging
import threading
import time

from control_plane.clients.http import RetryPolicy
from control_plane.clients.jenkins import JenkinsClient
from control_plane.clients.node_agent import NodeAgentClient
from control_plane.config import get_settings
from control_plane.services.gc import gc_hosts_once
from control_plane.services.reconciler import reconcile_once
from control_plane.services.scaler import scale_once


logger = logging.getLogger(__name__)


def _node_agent_factory(_host_id: str) -> NodeAgentClient:
    # Simple dev default: one local node-agent endpoint.
    settings = get_settings()
    retry = RetryPolicy(settings.retry_attempts, settings.retry_sleep_sec)
    return NodeAgentClient(base_url="http://localhost:9000", retry=retry)


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
