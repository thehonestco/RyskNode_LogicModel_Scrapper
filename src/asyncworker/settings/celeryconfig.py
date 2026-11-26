"""
Celery configuration for the RyskNode async worker.

All settings are loaded from environment variables with sensible defaults.
This module is referenced by the CELERY_CONFIG_MODULE environment variable
and loaded via ``app.config_from_envvar("CELERY_CONFIG_MODULE")``.
"""

from __future__ import annotations

import os
import socket
from pathlib import Path

from dotenv import load_dotenv
from kombu import Queue

_env_path = Path(__file__).resolve().parents[3] / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

# ═══════════════════════════════════════════════════════════════════════
# Broker & Result Backend
# ═══════════════════════════════════════════════════════════════════════
broker_url: str = os.getenv("CELERY_BROKER_URL", "redis://localhost:6379/0")
result_backend: str = broker_url

# Redis transport & connection health optimizations (adapted from laibel ai-services)
broker_connection_retry_on_startup: bool = True
broker_connection_retry: bool = True
broker_connection_max_retries: int = 20
broker_connection_retry_delay: int = 10
broker_pool_limit: int = 10

_tcp_keepalive_options = {
    socket.TCP_KEEPIDLE: 60,
    socket.TCP_KEEPINTVL: 10,
    socket.TCP_KEEPCNT: 6,
}

broker_transport_options: dict = {
    "visibility_timeout": int(os.getenv("CELERY_VISIBILITY_TIMEOUT", "3600")),
    "fanout_prefix": True,
    "fanout_patterns": True,
    "socket_keepalive": True,
    "socket_keepalive_options": _tcp_keepalive_options,
    "retry_on_timeout": True,
    "health_check_interval": 30,
}
result_backend_transport_options: dict = {
    "retry_on_timeout": True,
    "socket_keepalive": True,
    "socket_keepalive_options": _tcp_keepalive_options,
}
redis_backend_health_check_interval: int = 30

# ═══════════════════════════════════════════════════════════════════════
# Serialization — JSON only (prevents pickle RCE)
# ═══════════════════════════════════════════════════════════════════════
accept_content: list[str] = ["json"]
task_serializer: str = "json"
result_serializer: str = "json"

# ═══════════════════════════════════════════════════════════════════════
# Time Limits
# ═══════════════════════════════════════════════════════════════════════
task_time_limit: int = int(os.getenv("CELERY_TASK_TIME_LIMIT", "600"))
task_soft_time_limit: int = int(os.getenv("CELERY_TASK_SOFT_TIME_LIMIT", "540"))

# ═══════════════════════════════════════════════════════════════════════
# Result Backend Resilience
# ═══════════════════════════════════════════════════════════════════════
result_extended: bool = True
result_backend_always_retry: bool = True
result_backend_max_retries: int = 10
result_expires: int = int(os.getenv("CELERY_RESULT_EXPIRES", "86400"))  # 24h

# ═══════════════════════════════════════════════════════════════════════
# State Tracking & Events (required for Flower monitoring)
# ═══════════════════════════════════════════════════════════════════════
task_track_started: bool = True
worker_send_task_events: bool = True
task_send_sent_event: bool = True

# ═══════════════════════════════════════════════════════════════════════
# Queue Definitions (configurable via .env)
# ═══════════════════════════════════════════════════════════════════════
def _env_bool(name: str, default: bool = True) -> bool:
    return os.getenv(name, str(default)).lower() in ("true", "1", "yes")


_queue_defs: list[tuple[bool, str]] = [
    (_env_bool("ENABLE_QUEUE_DEFAULT", True), os.getenv("CELERY_QUEUE_DEFAULT", "default")),
    (_env_bool("ENABLE_QUEUE_BUYER_RISK", True), os.getenv("CELERY_QUEUE_BUYER_RISK", "buyer_risk")),
    (_env_bool("ENABLE_QUEUE_CREDIT_LIMIT", True), os.getenv("CELERY_QUEUE_CREDIT_LIMIT", "credit_limit")),
    (_env_bool("ENABLE_QUEUE_VDD", True), os.getenv("CELERY_QUEUE_VDD", "vendor_due_diligence")),
]

_queue_default: str = _queue_defs[0][1]
task_default_queue: str = _queue_default
task_default_exchange: str = _queue_default
task_default_routing_key: str = _queue_default

_configured_queues: list[str] = [name for enabled, name in _queue_defs if enabled and name]

task_queues: tuple[Queue, ...] = tuple(
    Queue(name, routing_key=name) for name in dict.fromkeys(_configured_queues)
)

task_routes: dict = {
    "asyncworker.tasks.assess_buyer_task": {"queue": _queue_defs[1][1]},
    "asyncworker.tasks.assess_credit_limit_task": {"queue": _queue_defs[2][1]},
    "asyncworker.tasks.vendor_due_diligence_task": {"queue": _queue_defs[3][1]},
}

# ═══════════════════════════════════════════════════════════════════════
# Worker Settings
# ═══════════════════════════════════════════════════════════════════════
task_acks_late: bool = True
worker_prefetch_multiplier: int = 1
task_reject_on_worker_lost: bool = True
worker_cancel_long_running_tasks_on_connection_loss: bool = True
worker_pool_restarts: bool = True
worker_max_tasks_per_child: int = int(os.getenv("CELERY_WORKER_MAX_TASKS_PER_CHILD", "100"))
worker_disable_rate_limits: bool = True
worker_concurrency: int = 2

timezone: str = os.getenv("CELERY_TIMEZONE", "Asia/Kolkata")
enable_utc: bool = True
