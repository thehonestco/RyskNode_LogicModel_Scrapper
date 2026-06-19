from __future__ import annotations

from typing import Literal, Optional
from pydantic import BaseModel, Field


DataSufficiencyBand = Literal["full", "partial", "insufficient"]
LenderQualityFlag = Literal["PSU_BANK", "PRIVATE_BANK", "MIXED", "NBFC_ONLY", "NO_LENDER"]


class FinalFeatureRowV2(BaseModel):
    """
    Patch I schema extension.

    Entity-agnostic feature row for Part 1 → Part 2 handoff.
    This patch standardises around 3-year MCA financial history only.
    """

    entity_key: str = Field(..., description="GSTIN or CIN of assessed entity")
    entity_name: Optional[str] = None
    sector_bucket: Optional[str] = None
    incorporation_date: Optional[str] = None

    revenue: Optional[float] = None
    revenue_prev1: Optional[float] = None
    revenue_prev2: Optional[float] = None

    ebit: Optional[float] = None
    ebit_prev1: Optional[float] = None
    ebit_prev2: Optional[float] = None

    pat: Optional[float] = None
    pat_prev1: Optional[float] = None
    pat_prev2: Optional[float] = None

    total_debt: Optional[float] = None
    total_debt_prev1: Optional[float] = None
    total_debt_prev2: Optional[float] = None

    networth: Optional[float] = None
    networth_prev1: Optional[float] = None
    networth_prev2: Optional[float] = None

    receivables: Optional[float] = None
    receivables_prev1: Optional[float] = None
    receivables_prev2: Optional[float] = None

    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    debt_to_equity: Optional[float] = None
    debt_to_assets: Optional[float] = None
    payment_cycle_days: Optional[float] = Field(default=None, description="Entity payment cycle / DPO-style metric")
    finance_cost: Optional[float] = None

    revenue_cagr_3y: Optional[float] = None
    pat_cagr_3y: Optional[float] = None
    networth_cagr_3y: Optional[float] = None

    charge_count_active: int = 0
    has_any_active_charge: bool = False
    has_recent_charge_90d: bool = False
    old_unsatisfied_charge_count: int = 0
    lender_quality_flag: LenderQualityFlag = "NO_LENDER"

    data_sufficiency_band: DataSufficiencyBand = "insufficient"
    mca_pdf_required: bool = False
    revenue_source: Literal["mca", "gst_proxy"] = "mca"
    source_notes: list[str] = Field(default_factory=list)
