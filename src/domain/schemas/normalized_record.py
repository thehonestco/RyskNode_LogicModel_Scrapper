"""Canonical normalized entity record after source extraction and standardization."""
from pydantic import BaseModel, Field
from typing import Optional
from datetime import date


class NormalizedRecord(BaseModel):
    # --- Identity ---
    entity_id: str
    legal_name: Optional[str] = None
    gstin: Optional[str] = None
    cin: Optional[str] = None
    udyam_no: Optional[str] = None
    pan: Optional[str] = None
    entity_type: Optional[str] = None          # company | llp | proprietor | partnership
    incorporation_date: Optional[date] = None
    registered_address: Optional[str] = None
    state: Optional[str] = None
    msme_category: Optional[str] = None        # micro | small | medium

    # --- Sector ---
    nic_code: Optional[str] = None             # NIC 2008 activity code (from MCA/GST)

    # --- GST ---
    gst_status: Optional[str] = None           # active | cancelled | suspended
    gst_registration_date: Optional[date] = None
    gst_filing_periods_total: Optional[int] = None
    gst_filing_periods_filed: Optional[int] = None

    # --- Legal ---
    legal_case_count: Optional[int] = None
    pending_case_count: Optional[int] = None
    criminal_case_count: Optional[int] = None
    civil_case_count: Optional[int] = None

    # --- Financial (5-year series) ---
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

    # --- Balance sheet (latest) ---
    current_assets_latest: Optional[float] = None
    current_liabilities_latest: Optional[float] = None
    total_assets_latest: Optional[float] = None
    total_debt_latest: Optional[float] = None
    equity_latest: Optional[float] = None
    inventory_latest: Optional[float] = None
    accounts_receivable_latest: Optional[float] = None
    accounts_payable_latest: Optional[float] = None
    net_revenue_latest: Optional[float] = None
    cogs_latest: Optional[float] = None

    # --- Source metadata ---
    source_priority_used: Optional[str] = None
    sources_available: list[str] = Field(default_factory=list)
    conflict_flags: list[str] = Field(default_factory=list)
    snapshot_date: Optional[date] = None
    pipeline_version: str = "1.0.0"
