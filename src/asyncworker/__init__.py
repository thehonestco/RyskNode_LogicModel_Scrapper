"""
RyskNode Celery Async Worker Application.

This module initializes the Celery application instance used by **both**:
  1. The FastAPI API process — for dispatching tasks via ``task.delay()``.
  2. The Celery worker process — for executing tasks.

Both processes import this module, but only the worker process executes tasks
(and therefore fires the task_success / task_failure signals that publish
events to Redis).

Startup flow:
    1. Configure ``inject`` dependency injection (if not already done by FastAPI).
    2. Create the ``Celery("rysknode_worker")`` app instance.
    3. Load configuration from ``asyncworker.settings.celeryconfig`` via envvar.
    4. Auto-discover task functions from ``asyncworker.tasks``.
    5. Wire up signal handlers for logging, event publishing, and shutdown.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

_env_path = Path(__file__).resolve().parents[2] / ".env"
if _env_path.exists():
    load_dotenv(_env_path)
else:
    load_dotenv()

from celery import Celery
from celery.signals import (
    after_setup_logger,
    after_setup_task_logger,
    task_failure,
    task_retry,
    task_success,
    worker_shutting_down,
)

from asyncworker.celery_task_context import (
    CeleryContextVarLogFilter,
    ContextAwareTaskFormatter,
)

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════
# Ensure src/ is on sys.path (required when Celery starts as a standalone
# process outside of the FastAPI uvicorn context)
# ═══════════════════════════════════════════════════════════════════════
_src_dir = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if _src_dir not in sys.path:
    sys.path.insert(0, _src_dir)

# ═══════════════════════════════════════════════════════════════════════
# Dependency Injection
# ═══════════════════════════════════════════════════════════════════════
# When the FastAPI app imports this module, inject is already configured
# by bootstrap.py — so the guard prevents double-configuration.
# When the Celery worker starts directly, inject is NOT configured yet,
# so this block runs to set up database connections, services, etc.
# ═══════════════════════════════════════════════════════════════════════
import inject  # noqa: E402

if not inject.is_configured():
    from app.dependency import configure_dependency

    inject.configure(configure_dependency)

# ═══════════════════════════════════════════════════════════════════════
# Celery App Instance
# ═══════════════════════════════════════════════════════════════════════
app = Celery("rysknode_worker")

# Load configuration from the module pointed to by CELERY_CONFIG_MODULE.
# Default: asyncworker.settings.celeryconfig
os.environ.setdefault(
    "CELERY_CONFIG_MODULE",
    os.getenv("CELERY_APP_CONF", "asyncworker.settings.celeryconfig"),
)
app.config_from_envvar("CELERY_CONFIG_MODULE")

# Auto-discover task definitions in asyncworker/tasks.py
app.autodiscover_tasks(["asyncworker.tasks"])

# ═══════════════════════════════════════════════════════════════════════
# Kombu Resilience: Publish Failure Event to Pub/Sub on Bad Message
# ═══════════════════════════════════════════════════════════════════════
try:
    from queue import Empty
    from datetime import datetime, timezone as tz
    from kombu.transport.redis import Channel as RedisChannel
    from kombu.utils.encoding import bytes_to_str
    from kombu.utils.json import loads
    from asyncworker.events import publish_event

    def _safe_brpop_read(self, **options):
        try:
            try:
                dest__item = self.client.parse_response(self.client.connection, "BRPOP", **options)
            except self.connection_errors:
                self.client.connection.disconnect()
                raise
            if dest__item:
                dest, item = dest__item
                dest = bytes_to_str(dest).rsplit(self.sep, 1)[0]
                self._queue_cycle.rotate(dest)
                try:
                    payload = loads(bytes_to_str(item))
                    if isinstance(payload, dict):
                        import base64 as b64_lib
                        import json as json_lib
                        import uuid as uuid_lib

                        headers = payload.setdefault("headers", {})
                        task_id = headers.setdefault("id", str(uuid_lib.uuid4()))
                        headers.setdefault("root_id", task_id)
                        headers.setdefault("lang", "py")
                        headers.setdefault("retries", 0)

                        props = payload.setdefault("properties", {})
                        props.setdefault("delivery_tag", task_id)
                        props.setdefault("correlation_id", task_id)
                        d_info = props.setdefault("delivery_info", {})
                        d_info.setdefault("exchange", "")
                        d_info.setdefault("routing_key", dest)

                        body = payload.get("body")
                        if isinstance(body, dict):
                            headers.setdefault("argsrepr", "()")
                            headers.setdefault("kwargsrepr", repr(body))
                            tuple_body = [[], body, {"callbacks": None, "errbacks": None, "chain": None, "chord": None}]
                            payload["body"] = b64_lib.b64encode(json_lib.dumps(tuple_body).encode("utf-8")).decode("utf-8")
                            props["body_encoding"] = "base64"
                            payload["content-type"] = "application/json"
                            payload["content-encoding"] = "utf-8"
                        elif isinstance(body, list):
                            headers.setdefault("argsrepr", "()")
                            headers.setdefault("kwargsrepr", "{}")
                            payload["body"] = b64_lib.b64encode(json_lib.dumps(body).encode("utf-8")).decode("utf-8")
                            props["body_encoding"] = "base64"
                            payload["content-type"] = "application/json"
                            payload["content-encoding"] = "utf-8"
                        elif isinstance(body, str) and not props.get("body_encoding"):
                            stripped = body.strip()
                            if (stripped.startswith("[") and stripped.endswith("]")) or (stripped.startswith("{") and stripped.endswith("}")):
                                try:
                                    parsed_body = json_lib.loads(stripped)
                                    if isinstance(parsed_body, dict):
                                        parsed_body = [[], parsed_body, {"callbacks": None, "errbacks": None, "chain": None, "chord": None}]
                                    payload["body"] = b64_lib.b64encode(json_lib.dumps(parsed_body).encode("utf-8")).decode("utf-8")
                                    props["body_encoding"] = "base64"
                                    payload["content-type"] = "application/json"
                                    payload["content-encoding"] = "utf-8"
                                except Exception:
                                    pass

                    self.connection._deliver(payload, dest)
                except Exception as exc:
                    logger.error("Message error on queue '%s': %s", dest, exc)
                    try:
                        publish_event(
                            f"{dest}.failed",
                            {
                                "task_id": None,
                                "queue": dest,
                                "status": "FAILURE",
                                "error": f"Invalid message envelope/payload: {exc}",
                                "timestamp": datetime.now(tz.utc).isoformat(),
                            },
                        )
                    except Exception as pub_exc:
                        logger.warning("Could not publish error to pub/sub: %s", pub_exc)
                return True
            else:
                raise Empty()
        finally:
            self._in_poll = None

    RedisChannel._brpop_read = _safe_brpop_read
except Exception as e:
    logger.warning("Could not apply Kombu safe_brpop_read resilience guard: %s", e)




# ═══════════════════════════════════════════════════════════════════════
# Signal: Logging Context Propagation
# ═══════════════════════════════════════════════════════════════════════
# Install the ContextVar-aware log filter and formatter on Celery's
# loggers so that task identity (task_id, task_name) appears in all log
# output, even inside async_to_sync coroutines.
# ═══════════════════════════════════════════════════════════════════════


def _install_context_logging(logger: logging.Logger, **kwargs) -> None:  # type: ignore[override]
    """Attach ContextVar filter to handlers for task identity propagation."""
    if not any(isinstance(f, CeleryContextVarLogFilter) for f in logger.filters):
        logger.addFilter(CeleryContextVarLogFilter())

    for handler in logger.handlers:
        if not any(isinstance(f, CeleryContextVarLogFilter) for f in handler.filters):
            handler.addFilter(CeleryContextVarLogFilter())

        formatter = handler.formatter
        if formatter is None or isinstance(formatter, ContextAwareTaskFormatter):
            continue
        # Only replace Celery's built-in TaskFormatter
        if type(formatter).__name__ != "TaskFormatter":
            continue

        use_color = getattr(formatter, "use_color", True)
        handler.setFormatter(
            ContextAwareTaskFormatter(
                fmt=getattr(formatter, "_fmt", None),
                use_color=use_color,
                datefmt=formatter.datefmt,
            )
        )


after_setup_logger.connect(_install_context_logging)
after_setup_task_logger.connect(_install_context_logging)


# ═══════════════════════════════════════════════════════════════════════
# Signal: Redis Event Publishing on Task Completion
# ═══════════════════════════════════════════════════════════════════════
# These signal handlers fire ONLY in the worker process (the API process
# never executes tasks, so these signals never fire there).
#
# Event channel pattern:  <app_name>-events:<task_suffix>.<status>
# Example:                rysknode-events:sync_data_gov_state.completed
# ═══════════════════════════════════════════════════════════════════════


@task_success.connect
def _on_task_success(sender=None, result=None, **kwargs) -> None:
    """Publish task result to Redis event channel on success."""
    from datetime import datetime, timezone as tz

    from asyncworker.events import publish_event

    task_name = getattr(sender, "name", str(sender))
    task_id = None
    if hasattr(sender, "request"):
        task_id = getattr(sender.request, "id", None)

    event_suffix = task_name.rsplit(".", 1)[-1]

    publish_event(
        f"{event_suffix}.completed",
        {
            "task_id": task_id,
            "task_name": task_name,
            "status": "SUCCESS",
            "result": result,
            "timestamp": datetime.now(tz.utc).isoformat(),
        },
    )


@task_failure.connect
def _on_task_failure(sender=None, task_id=None, exception=None, traceback=None, **kwargs) -> None:
    """Publish task failure to Redis event channel."""
    from datetime import datetime, timezone as tz

    from asyncworker.events import publish_event

    task_name = getattr(sender, "name", str(sender))
    event_suffix = task_name.rsplit(".", 1)[-1]

    publish_event(
        f"{event_suffix}.failed",
        {
            "task_id": task_id,
            "task_name": task_name,
            "status": "FAILURE",
            "error": str(exception),
            "timestamp": datetime.now(tz.utc).isoformat(),
        },
    )


@task_retry.connect
def _on_task_retry(sender=None, request=None, reason=None, **kwargs) -> None:
    """Publish task retry to Redis event channel."""
    from datetime import datetime, timezone as tz

    from asyncworker.events import publish_event

    task_name = getattr(sender, "name", str(sender))
    event_suffix = task_name.rsplit(".", 1)[-1]

    publish_event(
        f"{event_suffix}.retrying",
        {
            "task_id": getattr(request, "id", None),
            "task_name": task_name,
            "status": "RETRY",
            "reason": str(reason),
            "timestamp": datetime.now(tz.utc).isoformat(),
        },
    )


# ═══════════════════════════════════════════════════════════════════════
# Signal: Graceful Shutdown
# ═══════════════════════════════════════════════════════════════════════


@worker_shutting_down.connect
def _on_worker_shutdown(sig=None, how=None, exitcode=None, **kwargs) -> None:
    """Clean up Redis event publisher on worker shutdown."""
    from asyncworker.events import _publisher

    if _publisher is not None:
        _publisher.close()
    logger.info("Worker shutting down (signal=%s, how=%s, exitcode=%s)", sig, how, exitcode)
