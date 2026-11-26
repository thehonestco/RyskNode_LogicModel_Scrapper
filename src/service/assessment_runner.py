"""
Shared execution runner for S1, S2, and S4 assessments.

Both the FastAPI HTTP endpoints and the Celery Redis queue tasks call these
functions so that request execution, Pydantic schema validation, and JSON
payload shaping are identical regardless of entrypoint.
"""

from __future__ import annotations

from typing import Any

from service.ppre_service import PPREService
from service.vdd_service import VDDService


async def run_assess_buyer(
    ppre_service: PPREService,
    entity_id: str,
    seller_id: str,
    include_xai: bool = True,
) -> dict[str, Any]:
    """Run S1 Buyer Risk Assessment and return the validated JSON-ready payload."""
    from api.schema.ppre import BuyerAssessResponse

    result = await ppre_service.assess_buyer(
        entity_id=entity_id,
        seller_id=seller_id,
        include_xai=include_xai,
    )
    return BuyerAssessResponse.model_validate(result).model_dump(mode="json")


async def run_assess_credit_limit(
    ppre_service: PPREService,
    entity_id: str,
    seller_id: str,
    requested_amount: float | None = None,
    avg_monthly_purchase_volume: float | None = None,
    credit_period_days: int = 30,
    ead: float | None = None,
) -> dict[str, Any]:
    """Run S2 Credit Limit Assessment and return the validated JSON-ready payload."""
    from api.schema.ppre import CreditLimitResponse

    result = await ppre_service.assess_buyer(
        entity_id=entity_id,
        seller_id=seller_id,
        requested_amount=requested_amount,
        avg_monthly_purchase_volume=avg_monthly_purchase_volume,
        credit_period_days=credit_period_days,
        ead=ead,
    )
    return CreditLimitResponse.model_validate(result).model_dump(mode="json")


async def run_vendor_due_diligence(
    vdd_service: VDDService,
    entity_id: str,
    seller_id: str,
) -> dict[str, Any]:
    """Run S4 Vendor Due Diligence and return the validated JSON-ready payload."""
    from api.schema.vdd import VendorDueDiligenceResponse

    result = await vdd_service.vendor_due_diligence(
        entity_id=entity_id,
        seller_id=seller_id,
    )
    return VendorDueDiligenceResponse.model_validate(result).model_dump(mode="json")
