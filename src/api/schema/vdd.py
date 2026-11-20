from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, model_validator

# ─────────────────────────────────────────────────────────────────────────────
# S4 Request
# ─────────────────────────────────────────────────────────────────────────────


class VendorDueDiligenceRequest(BaseModel):
    model_config = {"extra": "allow"}

    entity_id: str = Field(
        ..., description="[Required] CIN (21 characters) or GSTIN (15 characters) of the vendor"
    )
    seller_id: str = Field(
        ..., description="[Required - Audit Log] Unique identifier of the seller / requesting tenant"
    )

    @model_validator(mode="before")
    @classmethod
    def resolve_seller_identity(cls, values: Any) -> Any:
        if isinstance(values, dict):
            # Backward compatibility: map legacy requester_id to seller_id if passed
            if "seller_id" not in values and "requester_id" in values:
                values["seller_id"] = values["requester_id"]
        return values


# ─────────────────────────────────────────────────────────────────────────────
# S4 Sub-models (mirror PRD JSON skeleton)
# ─────────────────────────────────────────────────────────────────────────────


class VDDOverview(BaseModel):
    vendor_name: Optional[str] = None
    report_id: Optional[str] = None
    report_date: Optional[str] = None
    suite_label: str = "Service 4 of 5 · Pralyon Intelligence Suite"
    vendor_profile: str = "Vendor"
    escalation_level: str = "Risk Committee | Procurement Manager | Auto-approve"
    distribution: str = "Internal Use Only"


class DecisionEntry(BaseModel):
    parameter: str
    recommendation: str
    reason: str


class OnboardingDecision(BaseModel):
    status: str = "HOLD"  # APPROVED | CONDITIONAL | HOLD | REJECTED
    verdict_headline: str = ""
    verdict_detail: str = ""
    critical_flags_count: int = 0
    high_flags_count: int = 0
    docs_missing_count: int = 0
    legal_cases_count: int = 0
    api_cost_inr: float = 0.0
    decisions: List[DecisionEntry] = Field(default_factory=list)


class EntityIdentityVDD(BaseModel):
    vendor_name: Optional[str] = None
    business_type: Optional[str] = None
    gstin_status: str = "Unknown"
    udyam_number: Optional[str] = None
    udyam_status: Optional[str] = None
    authorised_signatory: Optional[str] = None
    site_address_consistency: str = "Unverified"
    cin: Optional[str] = None
    pan: Optional[str] = None
    incorporation_date: Optional[str] = None
    registered_state: Optional[str] = None


class RedFlagEntry(BaseModel):
    issue: str
    observed_result: str
    severity: str  # Critical | High | Medium | Low
    required_action: str


class AMLCheck(BaseModel):
    result: str = "Not screened"
    status: str = "Incomplete"
    risk: str = ""
    action: str = ""


class AMLIntegrity(BaseModel):
    entity_aml: AMLCheck = Field(default_factory=AMLCheck)
    sanctions_watchlist: AMLCheck = Field(default_factory=AMLCheck)
    pep_match_directors: AMLCheck = Field(default_factory=AMLCheck)
    adverse_media: AMLCheck = Field(default_factory=AMLCheck)
    fraud_corruption: AMLCheck = Field(default_factory=AMLCheck)
    fatf_jurisdiction: AMLCheck = Field(default_factory=AMLCheck)


class UBOEntry(BaseModel):
    observed: str = ""
    status: str = ""
    why_matters: str = ""
    follow_up: str = ""


class DirectorVDD(BaseModel):
    name: str = "Unknown"
    din: str = "N/A"
    disqualification_status: str = "Unknown"
    linked_entities_count: int = 0
    struck_off_count: int = 0
    status: str = ""
    follow_up: str = ""


class RelatedPartyEntry(BaseModel):
    observed: str = ""
    status: str = ""
    follow_up: str = ""


class OwnershipGovernance(BaseModel):
    ubo: UBOEntry = Field(default_factory=UBOEntry)
    shareholding_pattern: UBOEntry = Field(default_factory=UBOEntry)
    directors: List[DirectorVDD] = Field(default_factory=list)
    related_party_exposure: RelatedPartyEntry = Field(default_factory=RelatedPartyEntry)


class LitigationEntry(BaseModel):
    check: str
    observed_result: str
    status: str  # High | Critical | Monitor | Review | Clear
    why_matters: str
    follow_up: str


class ComplianceEntry(BaseModel):
    parameter: str
    result: str
    status: str
    risk: str


class FinancialQualityItem(BaseModel):
    headline: str = ""
    detail: str = ""


class FinancialQuality(BaseModel):
    turnover_mismatch: FinancialQualityItem = Field(default_factory=FinancialQualityItem)
    borrowing_stack: FinancialQualityItem = Field(default_factory=FinancialQualityItem)
    working_capital_stress: FinancialQualityItem = Field(
        default_factory=FinancialQualityItem
    )


class ChargeVDD(BaseModel):
    charge_id: str
    lender: str = "Unknown"
    amount_inr: float = 0.0
    created: str = "N/A"
    status: str = "Active"
    risk_note: str = ""


class MCACharges(BaseModel):
    summary_note: str = ""
    total_active_borrowing_inr: float = 0.0
    charges: List[ChargeVDD] = Field(default_factory=list)


class OperationalViabilityEntry(BaseModel):
    parameter: str
    current_status: str
    required_action: str


class DocumentEntry(BaseModel):
    document: str
    status: str  # Received | Not provided | Partial | Auto-fetched
    adequacy: str  # Adequate | Critical gap | Incomplete | Required | Complete | ...
    comment: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# S4 Top-level Response
# ─────────────────────────────────────────────────────────────────────────────


class VendorDueDiligenceResponse(BaseModel):
    model_config = {"extra": "ignore"}

    overview: VDDOverview = Field(default_factory=VDDOverview)
    onboarding_decision: OnboardingDecision = Field(default_factory=OnboardingDecision)
    entity_identity: EntityIdentityVDD = Field(default_factory=EntityIdentityVDD)
    red_flag_register: List[RedFlagEntry] = Field(default_factory=list)
    aml_integrity: AMLIntegrity = Field(default_factory=AMLIntegrity)
    ownership_governance: OwnershipGovernance = Field(default_factory=OwnershipGovernance)
    litigation_legal: List[LitigationEntry] = Field(default_factory=list)
    compliance_intelligence: List[ComplianceEntry] = Field(default_factory=list)
    financial_quality: FinancialQuality = Field(default_factory=FinancialQuality)
    mca_charges: MCACharges = Field(default_factory=MCACharges)
    operational_viability: List[OperationalViabilityEntry] = Field(default_factory=list)
    document_register: List[DocumentEntry] = Field(default_factory=list)
    data_sources_used: List[str] = Field(default_factory=list)
    assessed_at: Optional[datetime] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
