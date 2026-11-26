import atexit
import logging
import os
import signal
import subprocess
import sys
import time
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


def start_celery_workers(settings: Settings) -> list[subprocess.Popen]:
    """Start one Celery worker process per enabled queue with its own concurrency."""
    queue_specs = [
        (settings.enable_queue_default, settings.celery_queue_default, settings.celery_concurrency_default),
        (settings.enable_queue_buyer_risk, settings.celery_queue_buyer_risk, settings.celery_concurrency_buyer_risk),
        (settings.enable_queue_credit_limit, settings.celery_queue_credit_limit, settings.celery_concurrency_credit_limit),
        (settings.enable_queue_vdd, settings.celery_queue_vdd, settings.celery_concurrency_vdd),
    ]
    enabled_workers = [
        (q.strip(), c)
        for flag, q, c in queue_specs
        if flag and q and q.strip()
    ]
    if not enabled_workers:
        logger.warning("No Celery queues enabled (all ENABLE_QUEUE_* are false); skipping Celery Workers.")
        return []

    procs: list[subprocess.Popen] = []
    seen: set[str] = set()
    for queue, concurrency in enabled_workers:
        if queue in seen:
            continue
        seen.add(queue)
        cmd = [
            sys.executable,
            "-m",
            "celery",
            "-A",
            "asyncworker",
            "worker",
            "--loglevel=info",
            "-Q",
            queue,
            "-c",
            str(concurrency),
            "-n",
            f"{queue}@%h",
        ]
        logger.info(f"Starting Celery Worker [{queue}@%h] queue={queue} concurrency={concurrency}...")
        proc = subprocess.Popen(cmd, cwd=src_dir)
        _subprocesses.append(proc)
        procs.append(proc)
    return procs


def start_celery_flower(settings: Settings) -> subprocess.Popen | None:
    """Start Flower UI monitoring dashboard background process."""
    port = str(settings.celery_flower_port)
    auth = f"{settings.celery_flower_user}:{settings.celery_flower_password}"
    prefix = (settings.celery_flower_url_prefix or "").strip("/")
    cmd = [
        sys.executable,
        "-m",
        "celery",
        "-A",
        "asyncworker",
        "flower",
        f"--port={port}",
        f"--basic_auth={auth}",
        "--inspect_timeout=5000",
        "--purge_offline_workers=60",
    ]
    if prefix:
        cmd.append(f"--url_prefix={prefix}")
    url = f"http://127.0.0.1:{port}/{prefix}" if prefix else f"http://127.0.0.1:{port}"
    logger.info(f"Starting Flower UI on {url} (user: {settings.celery_flower_user})...")
    proc = subprocess.Popen(cmd, cwd=src_dir)
    _subprocesses.append(proc)
    return proc


if __name__ == "__main__":
    logger.info("=" * 65)
    logger.info("  RyskNode LogicModel Scrapper - Service Orchestrator")
    logger.info("=" * 65)

    # 1. Start Celery Workers (one per enabled queue, if ENABLE_CELERY_WORKER=true)
    worker_procs: list[subprocess.Popen] = []
    if settings.enable_celery_worker:
        worker_procs = start_celery_workers(settings)
    else:
        logger.info("Celery Worker is DISABLED (ENABLE_CELERY_WORKER=false)")

    # 2. Start Flower UI (if enabled via ENABLE_CELERY_FLOWER)
    if settings.enable_celery_flower:
        if worker_procs:
            # Give workers a moment to finish booting so Flower's initial inspect() finds them
            time.sleep(3)
        start_celery_flower(settings)
    else:
        logger.info("Flower UI is DISABLED (ENABLE_CELERY_FLOWER=false)")

    # 3. Start FastAPI Application Server (or wait on worker subprocesses)
    try:
        if settings.enable_api_server:
            logger.info(f"Starting FastAPI Application on http://{settings.app_host}:{settings.app_port}...")
            uvicorn.run(
                "app.bootstrap:api",
                host=settings.app_host,
                port=settings.app_port,
                reload=settings.can_reload,
            )
        elif _subprocesses:
            logger.info("FastAPI Server is DISABLED (ENABLE_API_SERVER=false); running background workers...")
            for proc in _subprocesses:
                proc.wait()
        else:
            logger.warning("Both FastAPI Server and all Celery processes are disabled; exiting.")
    finally:
        _cleanup_subprocesses()



