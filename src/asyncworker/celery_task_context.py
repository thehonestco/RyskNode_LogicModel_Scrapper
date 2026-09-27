"""
Propagate Celery task identity across ``async_to_sync`` via ``contextvars``.

Problem:
    Celery's ``current_task`` is thread-local.  ``asgiref.async_to_sync`` runs
    the coroutine on a different thread, so ``current_task`` is ``None`` inside
    async service methods.  This breaks task-aware logging and lineage tracking.

Solution:
    Capture task identity (id, name, root_id) into ``ContextVar`` tokens before
    entering ``async_to_sync``.  ContextVars are automatically copied to child
    threads by ``asgiref``, so the identity survives the thread hop.

Usage:
    @app.task(bind=True)
    def my_task(self, ...):
        with celery_task_context(self):
            return async_to_sync(my_async_function)(...)

Adapted from:
    gc project — ``src/asyncworker/celery_task_context.py``
"""

from __future__ import annotations

import logging
from contextlib import contextmanager
from contextvars import ContextVar
from typing import Any, Iterator

from celery import current_task

# ── ContextVars for task identity ────────────────────────────────────
_task_id: ContextVar[str | None] = ContextVar("celery_task_id", default=None)
_task_name: ContextVar[str | None] = ContextVar("celery_task_name", default=None)
_root_id: ContextVar[str | None] = ContextVar("celery_root_id", default=None)


def get_celery_task_id() -> str | None:
    """Return the current Celery task ID from ContextVar."""
    return _task_id.get()


def get_celery_task_name() -> str | None:
    """Return the current Celery task name from ContextVar."""
    return _task_name.get()


def get_celery_lineage() -> tuple[str | None, str | None]:
    """Return ``(parent_id, root_id)`` for child task enqueue."""
    parent_id = _task_id.get()
    root_id = _root_id.get() or parent_id

    if parent_id:
        return parent_id, root_id

    # Fallback to thread-local current_task if ContextVar is empty
    task = current_task
    if task is not None and getattr(task, "request", None) is not None:
        parent_id = getattr(task.request, "id", None)
        root_id = getattr(task.request, "root_id", None) or parent_id
        return parent_id, root_id

    return None, None


# ── Context Managers ─────────────────────────────────────────────────

@contextmanager
def celery_task_context(task: Any) -> Iterator[None]:
    """
    Capture bound Celery task identity into ContextVars for the duration
    of the enclosed block.  Use this around ``async_to_sync`` calls.

    Args:
        task: A bound Celery task instance (``self`` when ``bind=True``).
    """
    request = getattr(task, "request", None)
    task_id = getattr(request, "id", None) if request is not None else None
    task_name = getattr(task, "name", None)
    root_id = None
    if request is not None:
        root_id = getattr(request, "root_id", None) or task_id

    tokens = (
        _task_id.set(str(task_id) if task_id else None),
        _task_name.set(str(task_name) if task_name else None),
        _root_id.set(str(root_id) if root_id else None),
    )
    try:
        yield
    finally:
        _task_id.reset(tokens[0])
        _task_name.reset(tokens[1])
        _root_id.reset(tokens[2])


@contextmanager
def celery_task_context_from_current() -> Iterator[None]:
    """Capture identity from ``celery.current_task`` (for unbound tasks)."""
    task = current_task
    if task is None:
        yield
        return
    with celery_task_context(task):
        yield


# ── Logging Integration ──────────────────────────────────────────────

def apply_celery_context_to_record(record: logging.LogRecord) -> None:
    """Stamp ``task_name`` / ``task_id`` from ContextVars when missing."""
    task_id = getattr(record, "task_id", None)
    task_name = getattr(record, "task_name", None)
    ctx_id = _task_id.get()
    ctx_name = _task_name.get()

    if ctx_id and (not task_id or task_id == "???"):
        record.task_id = ctx_id  # type: ignore[attr-defined]
    if ctx_name and (not task_name or task_name == "???"):
        record.task_name = ctx_name  # type: ignore[attr-defined]


class CeleryContextVarLogFilter(logging.Filter):
    """
    Log filter that fills ``task_name`` and ``task_id`` on log records
    when Celery's thread-local ``current_task`` is empty.

    Must be attached to **handlers** (not just loggers) because child loggers
    propagate to parent handlers, and logger-level filters are skipped for
    propagated records.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        apply_celery_context_to_record(record)
        return True


class ContextAwareTaskFormatter(logging.Formatter):
    """
    Wraps Celery's ``TaskFormatter`` to seed task identity from ContextVars
    before delegating formatting.

    This ensures that log records inside ``async_to_sync`` coroutines show
    the correct ``[task_id]`` prefix instead of ``[???]``.
    """

    def __init__(self, fmt: str | None = None, use_color: bool = True, **kwargs: Any):
        from celery.app.log import TaskFormatter

        datefmt = kwargs.pop("datefmt", None)
        self._celery_formatter = TaskFormatter(fmt=fmt, use_color=use_color)
        super().__init__(fmt=fmt, datefmt=datefmt)

    def format(self, record: logging.LogRecord) -> str:
        apply_celery_context_to_record(record)
        return self._celery_formatter.format(record)
