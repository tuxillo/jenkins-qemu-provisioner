import logging
import threading
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from control_plane.api import router
from control_plane.config import get_settings
from control_plane.db import configure_sqlite_runtime, engine
from control_plane.logging_config import configure_logging
from control_plane.loops import start_loops
from control_plane.models import Base


logger = logging.getLogger(__name__)
stop_event = threading.Event()
loop_threads: list[threading.Thread] = []


app = FastAPI(title="Jenkins QEMU Control Plane")
app.include_router(router)
_static_dir = Path(__file__).resolve().parent / "static"
app.mount("/static", StaticFiles(directory=str(_static_dir)), name="static")


@app.on_event("startup")
def startup() -> None:
    configure_logging()
    settings = get_settings()
    if not settings.jenkins_url:
        raise RuntimeError("JENKINS_URL is required")

    configure_sqlite_runtime()
    Base.metadata.create_all(bind=engine)

    if not settings.disable_background_loops:
        global loop_threads
        loop_threads = start_loops(stop_event)
    logger.info("control-plane startup complete")


@app.on_event("shutdown")
def shutdown() -> None:
    stop_event.set()
    for thread in loop_threads:
        thread.join(timeout=1)
