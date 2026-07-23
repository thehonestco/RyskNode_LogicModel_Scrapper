"""Final Part 1 feature row — one record per entity per scoring event.

This is the canonical output of Part 1 and the future PD/LGD training feature table.
"""

from pydantic import BaseModel, Field
from typing import Optional
from datetime import date


class FinalFeatureRow(BaseModel):
    # --- IDs ---
    snapshot_id: str
    entity_id: str
    application_id: Optional[str] = None

    # --- Identity ---
    legal_name: Optional[str] = None
    gstin: Optional[str] = None
    cin: Optional[str] = None
    udyam_no: Optional[str] = None
    entity_type: Optional[str] = None
    state: Optional[str] = None
    msme_category: Optional[str] = None

    # --- Sector ---
    nic_code: Optional[str] = None  # NIC 2008 activity code — drives sector benchmarks

    # --- Stability ---
    business_vintage_years: Optional[float] = None
    gst_active_flag: Optional[bool] = None
    gst_filing_consistency_ratio: Optional[float] = None

    # --- Financial ratios ---
    current_ratio: Optional[float] = None
    quick_ratio: Optional[float] = None
    working_capital: Optional[float] = None
    debt_to_equity: Optional[float] = None
    debt_to_assets: Optional[float] = None
    tangible_net_worth: Optional[float] = None
    dso: Optional[float] = None
    dpo: Optional[float] = None

    # --- Legal ---
    legal_case_count: Optional[int] = None
    pending_case_count: Optional[int] = None
    criminal_case_count: Optional[int] = None

    # --- Financial history ---
    turnover_y1: Optional[float] = None
    turnover_y2: Optional[float] = None
    turnover_y3: Optional[float] = None
    turnover_y4: Optional[float] = None
    turnover_y5: Optional[float] = None

    revenue_y1: Optional[float] = None
    revenue_y2: Optional[float] = None
    revenue_y3: Optional[float] = None
    revenue_y4: Optional[float] = None
    revenue_y5: Optional[float] = None

    net_revenue_y1: Optional[float] = None
    net_revenue_y2: Optional[float] = None
    net_revenue_y3: Optional[float] = None
    net_revenue_y4: Optional[float] = None
    net_revenue_y5: Optional[float] = None

    # --- Financial statements array ---
    financials: list[dict] = Field(default_factory=list)

    # --- Trend metrics ---
    turnover_cagr_5y: Optional[float] = None
    revenue_cagr_5y: Optional[float] = None
    net_revenue_cagr_5y: Optional[float] = None

    # --- Latest anchors ---
    current_liabilities_latest: Optional[float] = None
    total_assets_latest: Optional[float] = None
    net_revenue_latest: Optional[float] = None

    # --- Domain scores ---
    identity_score: Optional[float] = None
    financial_score: Optional[float] = None
    legal_score: Optional[float] = None
    documentation_score: Optional[float] = None
    data_completeness_score: Optional[float] = None

    # --- Reason codes ---
    reason_codes_identity: list[str] = Field(default_factory=list)
    reason_codes_financial: list[str] = Field(default_factory=list)
    reason_codes_legal: list[str] = Field(default_factory=list)
    reason_codes_documentation: list[str] = Field(default_factory=list)

    # --- Audit ---
    snapshot_date: Optional[date] = None
    source_fetch_date: Optional[date] = None
    pipeline_version: str = "1.0.0"
    sources_used: list[str] = Field(default_factory=list)
    data_sufficiency_band: Optional[str] = None  # full | partial | insufficient
