import logging
import threading

from fastapi import FastAPI

from node_agent.api import router
from node_agent.config import get_agent_settings
from node_agent.heartbeat import start_heartbeat_thread
from node_agent.safety import start_safety_thread, startup_reconcile
from node_agent.state import initialize_state


logger = logging.getLogger(__name__)

app = FastAPI(title="Jenkins QEMU Node Agent")
app.include_router(router)
stop_event = threading.Event()
heartbeat_thread: threading.Thread | None = None
safety_thread: threading.Thread | None = None


@app.on_event("startup")
def startup() -> None:
    settings = get_agent_settings()
    initialize_state()
    startup_reconcile()
    logger.info(
        "node-agent preflight control_plane_url=%s base_dir=%s overlay_dir=%s cloud_init_dir=%s network_backend=%s network_interface=%s",
        settings.control_plane_url,
        settings.base_image_dir,
        settings.overlay_dir,
        settings.cloud_init_dir,
        settings.network_backend,
        settings.network_interface,
    )
    logger.info(
        "node-agent startup complete host_id=%s os_family=%s accel=%s",
        settings.host_id,
        settings.os_family,
        settings.qemu_accel,
    )
    if not settings.disable_workers:
        global heartbeat_thread
        heartbeat_thread = start_heartbeat_thread(stop_event)
        global safety_thread
        safety_thread = start_safety_thread(stop_event)


@app.on_event("shutdown")
def shutdown() -> None:
    stop_event.set()
    if heartbeat_thread:
        heartbeat_thread.join(timeout=1)
    if safety_thread:
        safety_thread.join(timeout=1)
