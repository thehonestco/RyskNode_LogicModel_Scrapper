"""
Celery task definitions for the RyskNode async worker.

Each task is a thin wrapper around an existing service method.  The pattern:

    1. Create an isolated database connection (UOW) for this worker process.
    2. Acquire the service instance via ``inject``.
    3. Bridge from sync Celery → async service method via ``async_to_sync``.
    4. Return a JSON-serializable result.
    5. Dispose the isolated UOW in a ``finally`` block.

The event publishing (to ``rysknode-events:*``) happens automatically via
Celery signals wired up in ``asyncworker/__init__.py`` — tasks don't need
to publish events explicitly.

Task → Queue Routing:
    scrape_single_company     →  default   (quick, single CIN)
    batch_scrape_companies    →  scrape    (heavy, many CINs)
    sync_data_gov_state       →  sync      (long-running, paginated API sync)
"""

from __future__ import annotations

import logging
from typing import Any

from asgiref.sync import async_to_sync
from celery.utils.log import get_task_logger

from asyncworker import app
from asyncworker.celery_task_context import celery_task_context

logger = get_task_logger(__name__)


# ═══════════════════════════════════════════════════════════════════════
# Internal helpers — UOW lifecycle management
# ═══════════════════════════════════════════════════════════════════════


def _get_settings():
    """Retrieve the application ``Settings`` instance from the DI container."""
    import inject

    from settings import Settings

    return inject.instance(Settings)


def _create_isolated_uow():
    """
    Create a database UOW with its own engine, isolated from the main
    application's connection pool.

    This is critical for Celery workers because:
    - Workers may run as forked processes (sharing the parent's pool causes
      socket corruption — the ``NullPool`` pattern from the gc reference).
    - Each task should have its own transaction boundary.

    Uses the existing ``create_isolated_uow`` from ``app.dependency``.
    """
    from app.dependency import create_isolated_uow

    return create_isolated_uow(_get_settings())


async def _dispose_isolated_uow(uow) -> None:
    """Dispose the dedicated engine associated with an isolated UOW."""
    from app.dependency import dispose_isolated_uow

    await dispose_isolated_uow(uow)


def _sanitize_result(result: Any) -> Any:
    """
    Ensure the task return value is JSON-serializable.

    Handles:
    - Pydantic models (``model_dump()``)
    - SQLAlchemy models (column dict extraction)
    - Domain objects with ``__dict__``
    - datetime, date (→ isoformat string)
    """
    import datetime

    if result is None:
        return None
    if isinstance(result, (str, int, float, bool)):
        return result
    if isinstance(result, (datetime.datetime, datetime.date)):
        return result.isoformat()
    if isinstance(result, dict):
        return {k: _sanitize_result(v) for k, v in result.items()}
    if isinstance(result, (list, tuple)):
        return [_sanitize_result(item) for item in result]
    if hasattr(result, "model_dump"):
        return result.model_dump(mode="json")
    if hasattr(result, "__table__"):
        # SQLAlchemy model
        return {c.name: _sanitize_result(getattr(result, c.name)) for c in result.__table__.columns}
    if hasattr(result, "__dict__"):
        return {k: _sanitize_result(v) for k, v in result.__dict__.items() if not k.startswith("_")}
    return str(result)


# ═══════════════════════════════════════════════════════════════════════
# Task 1: S1 Buyer Risk Assessment (PPRE) -> Queue: buyer_risk
# ═══════════════════════════════════════════════════════════════════════


async def _async_assess_buyer(
    entity_id: str,
    seller_id: str,
    trade_name: str | None = None,
    state_code: str | None = None,
    include_xai: bool = True,
) -> dict[str, Any]:
    import inject
    from service.artifact_service import ArtifactService
    from service.ppre_service import PPREService

    uow = _create_isolated_uow()
    try:
        artifact_service = inject.instance(ArtifactService)
        ppre_service = PPREService(uow=uow, artifact_service=artifact_service)
        result = await ppre_service.assess_buyer(
            entity_id=entity_id,
            seller_id=seller_id,
            trade_name=trade_name,
            state_code=state_code,
            include_xai=include_xai,
        )
        return _sanitize_result(result)
    finally:
        await _dispose_isolated_uow(uow)


@app.task(
    bind=True,
    name="asyncworker.tasks.assess_buyer_task",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    queue="buyer_risk",
    acks_late=True,
)
def assess_buyer_task(
    self,
    entity_id: str,
    seller_id: str,
    trade_name: str | None = None,
    state_code: str | None = None,
    include_xai: bool = True,
) -> dict[str, Any]:
    """
    Run S1 Buyer Risk Assessment as a background Celery task.

    Wraps ``PPREService.assess_buyer()`` — computes Tri-Core scores,
    Pralyon score, PD, risk band, and XAI explanations.
    Result is published to ``<app_name>-events:assess_buyer_task.completed``.
    """
    logger.info(
        "assess_buyer_task attempt %s/%s entity_id=%s seller_id=%s",
        self.request.retries + 1,
        (self.max_retries or 0) + 1,
        entity_id,
        seller_id,
    )

    with celery_task_context(self):
        return async_to_sync(_async_assess_buyer)(
            entity_id=entity_id,
            seller_id=seller_id,
            trade_name=trade_name,
            state_code=state_code,
            include_xai=include_xai,
        )


# ═══════════════════════════════════════════════════════════════════════
# Task 2: S2 Credit Limit Assessment (PPRE) -> Queue: credit_limit
# ═══════════════════════════════════════════════════════════════════════


async def _async_assess_credit_limit(
    entity_id: str,
    seller_id: str,
    requested_amount: float | None = None,
    avg_monthly_purchase_volume: float | None = None,
    credit_period_days: int = 30,
    ead: float | None = None,
) -> dict[str, Any]:
    import inject
    from service.artifact_service import ArtifactService
    from service.ppre_service import PPREService

    uow = _create_isolated_uow()
    try:
        artifact_service = inject.instance(ArtifactService)
        ppre_service = PPREService(uow=uow, artifact_service=artifact_service)
        result = await ppre_service.assess_buyer(
            entity_id=entity_id,
            seller_id=seller_id,
            requested_amount=requested_amount,
            avg_monthly_purchase_volume=avg_monthly_purchase_volume,
            credit_period_days=credit_period_days,
            ead=ead,
        )

        # Map S2 specific fields from _ppre_output to root level
        ppre_out = result.get("_ppre_output") or {}
        result["evaluated_limit"] = ppre_out.get("evaluated_limit") or 0.0
        result["recommended_tenor"] = ppre_out.get("recommended_tenor") or 30
        result["advance_required"] = ppre_out.get("advance_required") or 0.0
        result["tenor_schedule"] = ppre_out.get("tenor_schedule") or []
        result["stress_table"] = ppre_out.get("stress_table") or []

        return _sanitize_result(result)
    finally:
        await _dispose_isolated_uow(uow)


@app.task(
    bind=True,
    name="asyncworker.tasks.assess_credit_limit_task",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    queue="credit_limit",
    acks_late=True,
)
def assess_credit_limit_task(
    self,
    entity_id: str,
    seller_id: str,
    requested_amount: float | None = None,
    avg_monthly_purchase_volume: float | None = None,
    credit_period_days: int = 30,
    ead: float | None = None,
) -> dict[str, Any]:
    """
    Run S2 Credit Limit Assessment as a background Celery task.

    Wraps ``PPREService.assess_buyer()`` with S2 credit parameters —
    calculates evaluated limit, tenor, stress table, and risk metrics.
    Result is published to ``<app_name>-events:assess_credit_limit_task.completed``.
    """
    logger.info(
        "assess_credit_limit_task attempt %s/%s entity_id=%s amount=%s",
        self.request.retries + 1,
        (self.max_retries or 0) + 1,
        entity_id,
        requested_amount,
    )

    with celery_task_context(self):
        return async_to_sync(_async_assess_credit_limit)(
            entity_id=entity_id,
            seller_id=seller_id,
            requested_amount=requested_amount,
            avg_monthly_purchase_volume=avg_monthly_purchase_volume,
            credit_period_days=credit_period_days,
            ead=ead,
        )


# ═══════════════════════════════════════════════════════════════════════
# Task 3: Data.gov.in State Synchronization -> Queue: sync
# ═══════════════════════════════════════════════════════════════════════


async def _async_sync_data_gov_state(
    statecode: str | None = None,
    offset: int | None = None,
    resume_only_on_interruption: bool = False,
) -> dict[str, Any]:
    import inject
    from service.data_gov_sync_service import DataGovSyncService

    uow = _create_isolated_uow()
    try:
        sync_service = inject.instance(DataGovSyncService)
        result = await sync_service.sync_state(
            state=statecode,
            uow=uow,
            offset=offset,
            resume_only_on_interruption=resume_only_on_interruption,
        )
        return _sanitize_result(result)
    finally:
        await _dispose_isolated_uow(uow)


@app.task(
    bind=True,
    name="asyncworker.tasks.sync_data_gov_state",
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    queue="sync",
    acks_late=True,
)
def sync_data_gov_state(
    self,
    statecode: str | None = None,
    offset: int | None = None,
    resume_only_on_interruption: bool = False,
) -> dict[str, Any]:
    """
    Synchronize company data from data.gov.in for a state (or all states).

    Wraps ``DataGovSyncService.sync_state()``.
    Can be triggered on-demand via Flower UI or scheduled via Celery Beat.
    """
    logger.info(
        "sync_data_gov_state attempt %s/%s state=%s offset=%s",
        self.request.retries + 1,
        (self.max_retries or 0) + 1,
        statecode or "All States",
        offset,
    )

    with celery_task_context(self):
        return async_to_sync(_async_sync_data_gov_state)(
            statecode=statecode,
            offset=offset,
            resume_only_on_interruption=resume_only_on_interruption,
        )



