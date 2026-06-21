import inject
from fastapi import Depends
from fastapi.responses import HTMLResponse

from api.schema.ppre import CreditLimitRequest, CreditLimitResponse
from common.base import constants
from common.base.router import APIRouter
from common.base.utils import respond
from common.schema.base import ResponseSchema
from service.ppre_service import PPREService
from service.report_service import ReportService

router = APIRouter(prefix="/api/v1", tags=["Credit Limit Assessment"])


@router.post("/credit-limit", response_model=ResponseSchema)
async def credit_limit_json(
    request: CreditLimitRequest,
    ppre_service: PPREService = Depends(lambda: inject.instance(PPREService)),
):
    """S2 Credit Limit Assessment JSON API."""
    result = await ppre_service.assess_buyer(
        entity_id=request.entity_id,
        seller_id=request.seller_id,
        requested_amount=request.requested_amount,
        avg_monthly_purchase_volume=request.avg_monthly_purchase_volume,
        credit_period_days=request.credit_period_days,
        ead=request.ead,
    )

    # Map S2 specific fields from _ppre_output to root level for Pydantic validation
    ppre_out = result.get("_ppre_output") or {}
    result["evaluated_limit"] = ppre_out.get("evaluated_limit") or 0.0
    result["recommended_tenor"] = ppre_out.get("recommended_tenor") or 30
    result["advance_required"] = ppre_out.get("advance_required") or 0.0
    result["tenor_schedule"] = ppre_out.get("tenor_schedule") or []
    result["stress_table"] = ppre_out.get("stress_table") or []

    response_obj = CreditLimitResponse.model_validate(result)
    return respond(code=constants.HTTP_200_OK, data=response_obj.model_dump())


@router.post("/credit-limit/report", response_class=HTMLResponse)
async def credit_limit_report(
    request: CreditLimitRequest,
    ppre_service: PPREService = Depends(lambda: inject.instance(PPREService)),
    report_service: ReportService = Depends(lambda: inject.instance(ReportService)),
):
    """S2 Credit Limit Assessment dynamic HTML Report API."""
    result = await ppre_service.assess_buyer(
        entity_id=request.entity_id,
        seller_id=request.seller_id,
        requested_amount=request.requested_amount,
        avg_monthly_purchase_volume=request.avg_monthly_purchase_volume,
        credit_period_days=request.credit_period_days,
        ead=request.ead,
    )
    html_content = report_service.render_s2_report(result, requested_amount=request.requested_amount)
    return HTMLResponse(content=html_content, status_code=200)
