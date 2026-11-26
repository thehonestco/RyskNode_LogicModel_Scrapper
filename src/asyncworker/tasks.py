"""
Celery task definitions for the RyskNode async worker.

Each task is a thin Celery entrypoint wrapping the shared ``assessment_runner``
or ``DataGovSyncService`` with an isolated Unit of Work (UOW) per worker task.
Event publishing to ``<app_name>-events:*`` happens automatically via Celery
signals in ``asyncworker/__init__.py``.
"""

from __future__ import annotations

import json
import logging
import os
from contextlib import asynccontextmanager
from typing import Any, AsyncIterator

import inject
from asgiref.sync import async_to_sync
from celery.utils.log import get_task_logger

from asyncworker import app
from asyncworker.celery_task_context import celery_task_context
from service.assessment_runner import (
    run_assess_buyer,
    run_assess_credit_limit,
    run_vendor_due_diligence,
)

logger = get_task_logger(__name__)

# Env-configurable queue names (matching asyncworker.settings.celeryconfig)
QUEUE_BUYER_RISK = os.getenv("CELERY_QUEUE_BUYER_RISK", "buyer_risk")
QUEUE_CREDIT_LIMIT = os.getenv("CELERY_QUEUE_CREDIT_LIMIT", "credit_limit")
QUEUE_VDD = os.getenv("CELERY_QUEUE_VDD", "vendor_due_diligence")


@asynccontextmanager
async def _isolated_uow() -> AsyncIterator[Any]:
    """Create and cleanly dispose an isolated DB UOW for a worker task."""
    from app.dependency import create_isolated_uow, dispose_isolated_uow
    from settings import Settings

    uow = create_isolated_uow(inject.instance(Settings))
    try:
        yield uow
    finally:
        await dispose_isolated_uow(uow)


# ═══════════════════════════════════════════════════════════════════════
# Task 1: S1 Buyer Risk Assessment (PPRE)
# ═══════════════════════════════════════════════════════════════════════


async def _async_assess_buyer(
    entity_id: str,
    seller_id: str,
    include_xai: bool = True,
) -> dict[str, Any]:
    from service.artifact_service import ArtifactService
    from service.ppre_service import PPREService

    async with _isolated_uow() as uow:
        ppre_service = PPREService(uow=uow, artifact_service=inject.instance(ArtifactService))
        return await run_assess_buyer(
            ppre_service=ppre_service,
            entity_id=entity_id,
            seller_id=seller_id,
            include_xai=include_xai,
        )


@app.task(
    bind=True,
    name="asyncworker.tasks.assess_buyer_task",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    queue=QUEUE_BUYER_RISK,
    acks_late=True,
)
def assess_buyer_task(
    self,
    entity_id: str,
    seller_id: str,
    include_xai: bool = True,
) -> dict[str, Any]:
    """Run S1 Buyer Risk Assessment as a background Celery task."""
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
            include_xai=include_xai,
        )


# ═══════════════════════════════════════════════════════════════════════
# Task 2: S2 Credit Limit Assessment (PPRE)
# ═══════════════════════════════════════════════════════════════════════


async def _async_assess_credit_limit(
    entity_id: str,
    seller_id: str,
    requested_amount: float | None = None,
    avg_monthly_purchase_volume: float | None = None,
    credit_period_days: int = 30,
    ead: float | None = None,
) -> dict[str, Any]:
    from service.artifact_service import ArtifactService
    from service.ppre_service import PPREService

    async with _isolated_uow() as uow:
        ppre_service = PPREService(uow=uow, artifact_service=inject.instance(ArtifactService))
        return await run_assess_credit_limit(
            ppre_service=ppre_service,
            entity_id=entity_id,
            seller_id=seller_id,
            requested_amount=requested_amount,
            avg_monthly_purchase_volume=avg_monthly_purchase_volume,
            credit_period_days=credit_period_days,
            ead=ead,
        )


@app.task(
    bind=True,
    name="asyncworker.tasks.assess_credit_limit_task",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    queue=QUEUE_CREDIT_LIMIT,
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
    """Run S2 Credit Limit Assessment as a background Celery task."""
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
# Task 3: S4 Vendor Due Diligence (VDD)
# ═══════════════════════════════════════════════════════════════════════


async def _async_vendor_due_diligence(
    entity_id: str,
    seller_id: str,
) -> dict[str, Any]:
    from service.artifact_service import ArtifactService
    from service.vdd_service import VDDService

    async with _isolated_uow() as uow:
        vdd_service = VDDService(uow=uow, artifact_service=inject.instance(ArtifactService))
        return await run_vendor_due_diligence(
            vdd_service=vdd_service,
            entity_id=entity_id,
            seller_id=seller_id,
        )


@app.task(
    bind=True,
    name="asyncworker.tasks.vendor_due_diligence_task",
    autoretry_for=(Exception,),
    retry_backoff=30,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    queue=QUEUE_VDD,
    acks_late=True,
)
def vendor_due_diligence_task(
    self,
    entity_id: str,
    seller_id: str,
) -> dict[str, Any]:
    """Run S4 Vendor Due Diligence as a background Celery task."""
    logger.info(
        "vendor_due_diligence_task attempt %s/%s entity_id=%s seller_id=%s",
        self.request.retries + 1,
        (self.max_retries or 0) + 1,
        entity_id,
        seller_id,
    )
    with celery_task_context(self):
        return async_to_sync(_async_vendor_due_diligence)(
            entity_id=entity_id,
            seller_id=seller_id,
        )


# ═══════════════════════════════════════════════════════════════════════
# Task 4: Data.gov.in State Synchronization
# ═══════════════════════════════════════════════════════════════════════


async def _async_sync_data_gov_state(
    statecode: str | None = None,
    offset: int | None = None,
    resume_only_on_interruption: bool = False,
) -> dict[str, Any]:
    from service.data_gov_sync_service import DataGovSyncService

    async with _isolated_uow() as uow:
        sync_service = inject.instance(DataGovSyncService)
        result = await sync_service.sync_state(
            state=statecode,
            uow=uow,
            offset=offset,
            resume_only_on_interruption=resume_only_on_interruption,
        )
        return json.loads(json.dumps(result, default=str))


@app.task(
    bind=True,
    name="asyncworker.tasks.sync_data_gov_state",
    autoretry_for=(Exception,),
    retry_backoff=60,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
    acks_late=True,
)
def sync_data_gov_state(
    self,
    statecode: str | None = None,
    offset: int | None = None,
    resume_only_on_interruption: bool = False,
) -> dict[str, Any]:
    """Synchronize company data from data.gov.in for a state (or all states)."""
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
