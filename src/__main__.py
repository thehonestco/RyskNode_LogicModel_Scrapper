import atexit
import logging
import os
import signal
import subprocess
import sys
from os.path import abspath, dirname, join

import inject
import uvicorn

# Adjust python paths
src_dir = abspath(join(__file__, "../"))
project_root = abspath(join(__file__, "../../"))
if src_dir not in sys.path:
    sys.path.insert(0, src_dir)
if project_root not in sys.path:
    sys.path.insert(0, project_root)

from app.dependency import configure_dependency
from settings import Settings

if not inject.is_configured():
    inject.configure(configure_dependency)

settings = inject.instance(Settings)
logger = logging.getLogger("orchestrator")
logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")

_subprocesses: list[subprocess.Popen] = []


def _cleanup_subprocesses() -> None:
    """Terminate all background worker and flower processes gracefully on exit."""
    for proc in _subprocesses:
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=3)
            except Exception:
                proc.kill()


atexit.register(_cleanup_subprocesses)


def _signal_handler(sig, frame):
    _cleanup_subprocesses()
    sys.exit(0)


signal.signal(signal.SIGINT, _signal_handler)
signal.signal(signal.SIGTERM, _signal_handler)


def start_celery_worker(settings: Settings) -> subprocess.Popen | None:
    """Start Celery worker background process with configured queues and concurrency."""
    queues = settings.celery_worker_queues
    concurrency = str(settings.celery_worker_concurrency)
    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "asyncworker",
        "worker",
        "--loglevel=info",
        "-Q",
        queues,
        "-c",
        concurrency,
    ]
    logger.info(f"Starting Celery Worker with queues=[{queues}] concurrency={concurrency}...")
    proc = subprocess.Popen(cmd, cwd=src_dir)
    _subprocesses.append(proc)
    return proc


def start_celery_flower(settings: Settings) -> subprocess.Popen | None:
    """Start Flower UI monitoring dashboard background process."""
    port = str(settings.celery_flower_port)
    auth = f"{settings.celery_flower_user}:{settings.celery_flower_password}"
    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "asyncworker",
        "flower",
        f"--port={port}",
        f"--basic_auth={auth}",
    ]
    logger.info(f"Starting Flower UI on http://127.0.0.1:{port} (user: {settings.celery_flower_user})...")
    proc = subprocess.Popen(cmd, cwd=src_dir)
    _subprocesses.append(proc)
    return proc


def start_celery_beat(settings: Settings) -> subprocess.Popen | None:
    """Start Celery Beat periodic scheduler background process."""
    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "asyncworker",
        "beat",
        "--loglevel=info",
    ]
    logger.info("Starting Celery Beat periodic task scheduler...")
    proc = subprocess.Popen(cmd, cwd=src_dir)
    _subprocesses.append(proc)
    return proc


if __name__ == "__main__":
    logger.info("=" * 65)
    logger.info("  RyskNode LogicModel Scrapper - Service Orchestrator")
    logger.info("=" * 65)

    # 1. Start Celery Worker (if enabled via ENABLE_CELERY_WORKER)
    if settings.enable_celery_worker:
        start_celery_worker(settings)
    else:
        logger.info("Celery Worker is DISABLED (ENABLE_CELERY_WORKER=false)")

    # 2. Start Flower UI (if enabled via ENABLE_CELERY_FLOWER)
    if settings.enable_celery_flower:
        start_celery_flower(settings)
    else:
        logger.info("Flower UI is DISABLED (ENABLE_CELERY_FLOWER=false)")

    # 3. Start Celery Beat (if enabled via ENABLE_CELERY_BEAT)
    if settings.enable_celery_beat:
        start_celery_beat(settings)
    else:
        logger.info("Celery Beat Scheduler is DISABLED (ENABLE_CELERY_BEAT=false)")

    # 4. Start FastAPI Application Server (main blocking process)
    logger.info(f"Starting FastAPI Application on http://{settings.app_host}:{settings.app_port}...")
    try:
        uvicorn.run(
            "app.bootstrap:api",
            host=settings.app_host,
            port=settings.app_port,
            reload=settings.can_reload,
        )
    finally:
        _cleanup_subprocesses()



