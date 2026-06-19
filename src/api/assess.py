import inject
from fastapi import Depends
from fastapi.responses import HTMLResponse

from api.schema.ppre import BuyerAssessRequest, BuyerAssessResponse
from common.base import constants
from common.base.router import APIRouter
from common.base.utils import respond
from common.schema.base import ResponseSchema
from service.ppre_service import PPREService
from service.report_service import ReportService

router = APIRouter(prefix="/api/v1", tags=["Buyer Risk Assessment"])

@router.post("/assess", response_model=ResponseSchema)
async def assess_buyer_json(
    request: BuyerAssessRequest,
    ppre_service: PPREService = Depends(lambda: inject.instance(PPREService)),
):
    """S1 Buyer Risk Assessment JSON API."""
    result = await ppre_service.assess_buyer(
        entity_id=request.entity_id,
        seller_id=request.seller_id,
        trade_name=request.trade_name,
        state_code=request.state_code,
        include_xai=request.include_xai,
    )
    # The output from assess_buyer contains internal fields like _ppre_output,
    # but we can filter it using Pydantic or respond directly.
    # We validate via BuyerAssessResponse to ensure correct schema.
    response_obj = BuyerAssessResponse.model_validate(result)
    return respond(code=constants.HTTP_200_OK, data=response_obj.model_dump())

@router.post("/assess/report", response_class=HTMLResponse)
async def assess_buyer_report(
    request: BuyerAssessRequest,
    ppre_service: PPREService = Depends(lambda: inject.instance(PPREService)),
    report_service: ReportService = Depends(lambda: inject.instance(ReportService)),
):
    """S1 Buyer Risk Assessment dynamic HTML Report API."""
    result = await ppre_service.assess_buyer(
        entity_id=request.entity_id,
        seller_id=request.seller_id,
        trade_name=request.trade_name,
        state_code=request.state_code,
        include_xai=request.include_xai,
    )
    html_content = report_service.render_s1_report(result)
    return HTMLResponse(content=html_content, status_code=200)



