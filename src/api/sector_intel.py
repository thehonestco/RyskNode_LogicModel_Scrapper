from common.base.router import APIRouter
from fastapi import Depends, HTTPException
from fastapi.responses import Response
from typing import Dict, Any, List

from domain.sector_intel.sector_engine import SectorEngine
from service.report_service import ReportService
import inject

router = APIRouter(prefix="/api/v1/sector", tags=["Sector Intelligence"])

@router.get("/nics")
async def get_supported_nics():
    """List all supported NIC codes for sector intelligence."""
    engine = SectorEngine()
    return {"supported_nics": engine.store.list_nic_codes()}

@router.post("/{nic_code}")
async def get_sector_intelligence(nic_code: int, buyer_data: Dict[str, Any] = None):
    """
    Get sector intelligence report comparing a buyer to NIC sector benchmarks.
    If buyer_data is empty, it returns the sector median data directly.
    """
    if buyer_data is None:
        buyer_data = {}
        
    engine = SectorEngine()
    report = engine.run(nic_code, buyer_data)
    
    if "error" in report:
        raise HTTPException(status_code=404, detail=report["error"])
        
    return report

@router.post("/{nic_code}/report")
async def get_sector_intelligence_report(nic_code: int, buyer_data: Dict[str, Any] = None, as_pdf: bool = False):
    """
    Generate dynamic HTML or PDF report for S5 Sector Intelligence.
    """
    if buyer_data is None:
        buyer_data = {}
        
    engine = SectorEngine()
    report = engine.run(nic_code, buyer_data)
    
    if "error" in report:
        raise HTTPException(status_code=404, detail=report["error"])
        
    report_service = inject.instance(ReportService)
    
    if as_pdf:
        pdf_bytes = await report_service.render_s5_report_pdf(report)
        return Response(content=pdf_bytes, media_type="application/pdf")
        
    html_content = report_service.render_s5_report(report)
    return Response(content=html_content, media_type="text/html")
