"""
Celery configuration for the RyskNode async worker.

All settings are loaded from environment variables with sensible defaults.
This module is referenced by the CELERY_CONFIG_MODULE environment variable
and loaded via ``app.config_from_envvar("CELERY_CONFIG_MODULE")``.

Architecture note:
    This configuration file intentionally avoids importing Django settings or
    pydantic-settings classes.  It reads directly from ``os.environ`` so that
    the Celery worker process can start with minimal dependencies and no
    coupling to the FastAPI application layer.

References:
    - gc project:  ``src/asyncworker/settings/celeryconfig_docker.py``
    - laibel project: ``laibel/laibel/settings/base.py`` (CELERY_* namespace)
"""

from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv
from kombu import Queue

# Load .env file from project root or current directory
_env_path = Path(__file__).resolve().parents[3] / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

# ═══════════════════════════════════════════════════════════════════════
# Broker & Result Backend
# ═══════════════════════════════════════════════════════════════════════
broker_url: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend: str = os.getenv("CELERY_RESULT_BACKEND", broker_url)

# ═══════════════════════════════════════════════════════════════════════
# Serialization — JSON only (security best practice)
# Prevents arbitrary code execution via pickle deserialization.
# ═══════════════════════════════════════════════════════════════════════
accept_content: list[str] = ["json"]
task_serializer: str = "json"
result_serializer: str = "json"

# ═══════════════════════════════════════════════════════════════════════
# Time Limits
#   task_time_limit:      hard kill (SIGKILL) after this many seconds
#   task_soft_time_limit: raise SoftTimeLimitExceeded after this many seconds
#                         (gives the task a chance to clean up)
# ═══════════════════════════════════════════════════════════════════════
task_time_limit: int = int(os.getenv("CELERY_TASK_TIME_LIMIT", "600"))
task_soft_time_limit: int = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "540"))

# ═══════════════════════════════════════════════════════════════════════
# Result Backend Resilience
#   result_extended:              store task args, kwargs, worker name in result
#   result_backend_always_retry:  retry on connection errors to result backend
#   result_backend_max_retries:   how many times to retry
#   result_expires:               auto-delete results after N seconds
# ═══════════════════════════════════════════════════════════════════════
result_extended: bool = True
result_backend_always_retry: bool = True
result_backend_max_retries: int = 10
result_expires: int = int(os.getenv("CELERY_RESULT_EXPIRES", "86400"))  # 24h

# ═══════════════════════════════════════════════════════════════════════
# State Tracking & Events (required for Flower monitoring)
#   task_track_started:       report STARTED state when a task begins
#   worker_send_task_events:  send real-time events from workers to Flower
#   task_send_sent_event:     send event when a task is dispatched
# ═══════════════════════════════════════════════════════════════════════
task_track_started: bool = True
worker_send_task_events: bool = True
task_send_sent_event: bool = True

# ═══════════════════════════════════════════════════════════════════════
# Broker Connection Resilience
#   Retry connecting to Redis on startup instead of crashing immediately.
# ═══════════════════════════════════════════════════════════════════════
broker_connection_retry_on_startup: bool = True

# ═══════════════════════════════════════════════════════════════════════
# Queue Definitions
#   default:       lightweight fallback tasks
#   buyer_risk:    S1 Buyer Risk Assessment workloads
#   credit_limit:  S2 Credit Limit Assessment workloads
#   sync:          long-running data.gov.in synchronization
# ═══════════════════════════════════════════════════════════════════════
task_default_queue: str = "default"
task_default_exchange: str = "default"
task_default_routing_key: str = "default"

task_queues: tuple[Queue, ...] = (
    Queue("default", routing_key="default"),
    Queue("buyer_risk", routing_key="buyer_risk"),
    Queue("credit_limit", routing_key="credit_limit"),
    Queue("sync", routing_key="sync"),
)

# ═══════════════════════════════════════════════════════════════════════
# Worker Settings
# ═══════════════════════════════════════════════════════════════════════
worker_pool_restarts: bool = True
worker_concurrency: int = int(os.getenv("CELERY_WORKER_CONCURRENCY", "4"))

# ═══════════════════════════════════════════════════════════════════════
# Beat Scheduler (Periodic Tasks — Configurable via .env)
# ═══════════════════════════════════════════════════════════════════════
from celery.schedules import crontab

beat_schedule: dict = {}

# If ENABLE_CELERY_BEAT_SYNC is set to true in .env, register periodic sync
if os.getenv("ENABLE_CELERY_BEAT_SYNC", "false").lower() in ("true", "1", "yes"):
    sync_hour = os.getenv("CELERY_BEAT_SYNC_CRON_HOUR", "2")
    sync_minute = os.getenv("CELERY_BEAT_SYNC_CRON_MINUTE", "0")
    sync_state = os.getenv("CELERY_BEAT_SYNC_STATECODE", "") or None

    beat_schedule["data-gov-periodic-sync"] = {
        "task": "asyncworker.tasks.sync_data_gov_state",
        "schedule": crontab(hour=sync_hour, minute=sync_minute),
        "kwargs": {
            "statecode": sync_state,
            "resume_only_on_interruption": True,
        },
    }

timezone: str = os.getenv("CELERY_TIMEZONE", "Asia/Kolkata")
enable_utc: bool = True
