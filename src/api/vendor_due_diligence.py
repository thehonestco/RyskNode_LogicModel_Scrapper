import inject
from fastapi import Depends
from fastapi.responses import HTMLResponse

from api.schema.vdd import VendorDueDiligenceRequest, VendorDueDiligenceResponse
from common.base import constants
from common.base.router import APIRouter
from common.base.utils import respond
from common.schema.base import ResponseSchema
from service.report_service import ReportService
from service.vdd_service import VDDService


def get_vdd_service() -> VDDService:
    return inject.instance(VDDService)


def get_report_service() -> ReportService:
    return inject.instance(ReportService)


router = APIRouter(prefix="/api/v1", tags=["Vendor Due Diligence"])


@router.post("/vendor-due-diligence", response_model=ResponseSchema)
async def vendor_due_diligence_json(
    request: VendorDueDiligenceRequest,
    vdd_service: VDDService = Depends(get_vdd_service),
):
    """
    S4 Vendor Due Diligence JSON API (Synchronous).

    Runs integrity, compliance, and onboarding-readiness assessment for a vendor
    entity and returns the full structured VDD payload.
    """
    result = await vdd_service.vendor_due_diligence(
        entity_id=request.entity_id,
        seller_id=request.seller_id,
        trade_name=getattr(request, "trade_name", None),
        state_code=getattr(request, "state_code", None),
        vendor_profile=getattr(request, "vendor_profile", None),
    )
    response_obj = VendorDueDiligenceResponse.model_validate(result)
    return respond(code=constants.HTTP_200_OK, data=response_obj.model_dump())


@router.post("/vendor-due-diligence/report", response_class=HTMLResponse)
async def vendor_due_diligence_report(
    request: VendorDueDiligenceRequest,
    vdd_service: VDDService = Depends(get_vdd_service),
    report_service: ReportService = Depends(get_report_service),
):
    """S4 Vendor Due Diligence dynamic HTML Report API."""
    result = await vdd_service.vendor_due_diligence(
        entity_id=request.entity_id,
        seller_id=request.seller_id,
        trade_name=getattr(request, "trade_name", None),
        state_code=getattr(request, "state_code", None),
        vendor_profile=getattr(request, "vendor_profile", None),
    )
    html_content = report_service.render_s4_report(result)
    return HTMLResponse(content=html_content, status_code=200)
