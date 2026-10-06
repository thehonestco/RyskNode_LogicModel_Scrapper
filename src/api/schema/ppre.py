from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional
from datetime import datetime


# Request Schemas
class BuyerAssessRequest(BaseModel):
    entity_id: str = Field(..., description="CIN (21 characters) or GSTIN (15 characters) of the buyer")
    seller_id: str = Field(..., description="Unique identifier of the seller requesting assessment")
    trade_name: Optional[str] = Field(None, description="Trade name or DBA of the buyer")
    state_code: Optional[str] = Field(None, description="State code override")
    include_xai: bool = Field(True, description="Whether to compute explainable AI attributions")


class CreditLimitRequest(BaseModel):
    entity_id: str = Field(..., description="CIN or GSTIN of the buyer")
    seller_id: str = Field(..., description="Unique identifier of the seller")
    requested_amount: float = Field(..., description="Seller's credit request in INR")
    avg_monthly_purchase_volume: Optional[float] = Field(
        None, description="Seller's monthly trade volume with buyer (INR)"
    )
    credit_period_days: int = Field(30, description="Preferred payment term/tenor in days")
    ead: Optional[float] = Field(None, description="Exposure at default override")


from domain.schemas.final_feature_row import FinalFeatureRow


# ─────────────────────────────────────────────────────────────────────────
# S1 Granular Response Sub-Models (matching the approved payload skeleton)
# ─────────────────────────────────────────────────────────────────────────

class FeaturePill(BaseModel):
    title: str
    subtitle: str

class MetaBar(BaseModel):
    cin: Optional[str] = None
    gstin: Optional[str] = None
    pan: Optional[str] = None
    registered_state: Optional[str] = None
    incorporation_date: Optional[str] = None
    vintage_years: Optional[int] = None
    report_date: Optional[str] = None
    report_id: Optional[str] = None

class VerdictInfo(BaseModel):
    declined: bool = False
    badge_text: str = ""
    headline: str = ""
    summary_html: str = ""
    summary_text: str = ""

class MetricTile(BaseModel):
    value: Any = None
    display: Optional[str] = None
    label: str = ""
    sub_text: str = ""

class KeyMetrics(BaseModel):
    risk_band: MetricTile = Field(default_factory=MetricTile)
    blended_pd: MetricTile = Field(default_factory=MetricTile)
    zeropass_status: MetricTile = Field(default_factory=MetricTile)
    signals_count: MetricTile = Field(default_factory=MetricTile)

class Overview(BaseModel):
    company_name: Optional[str] = None
    trade_name: Optional[str] = None
    report_id: Optional[str] = None
    report_date: Optional[str] = None
    suite_label: str = "Service 1 of 5 · Pralyon Intelligence Suite"
    meta_bar: MetaBar = Field(default_factory=MetaBar)
    feature_pills: List[FeaturePill] = Field(default_factory=list)
    verdict: VerdictInfo = Field(default_factory=VerdictInfo)
    key_metrics: KeyMetrics = Field(default_factory=KeyMetrics)
    pralyon_score: int = 300

class VerifiedProfileRow(BaseModel):
    field: str
    value: Optional[str] = None
    source: str = ""
    status: str = ""
    status_class: str = "neutral"

class EntityIdentity(BaseModel):
    verified_profiles: List[VerifiedProfileRow] = Field(default_factory=list)

class DirectorEntry(BaseModel):
    name: str = "Unknown"
    din: str = "N/A"
    designation: str = "Director"
    sec_164_disqualified: bool = False
    other_entities_count: int = 0
    struck_off_links: str = "None"
    shareholding_pct: Optional[float] = None
    remuneration: Optional[float] = None
    status: str = "Clear"
    status_class: str = "pass"

class DirectorProfile(BaseModel):
    directors: List[DirectorEntry] = Field(default_factory=list)
    summary_text: str = ""

class GateEntry(BaseModel):
    gate_id: str
    check: str
    result: str = "N/A"
    disposition: str = "Clear"
    status_class: str = "pass"

class ZeroPass(BaseModel):
    headline: str = ""
    description: str = ""
    gates: List[GateEntry] = Field(default_factory=list)

class DimensionScore(BaseModel):
    name: str
    weight: str
    score: int = 0
    max_score: int = 100
    reading: str = ""

class BandLadderEntry(BaseModel):
    band: str
    pd_range: str
    reading: str
    is_current: bool = False
    blended_pd_display: Optional[str] = None

class TriCore(BaseModel):
    headline: str = ""
    description: str = ""
    dimensions: List[DimensionScore] = Field(default_factory=list)
    band_ladder: List[BandLadderEntry] = Field(default_factory=list)

class StatementRow(BaseModel):
    metric: str
    values: Dict[str, Optional[float]] = Field(default_factory=dict)
    format: str = "currency_cr"

class Statements(BaseModel):
    years: List[str] = Field(default_factory=list)
    rows: List[StatementRow] = Field(default_factory=list)

class RatioRow(BaseModel):
    name: str
    value: Optional[float] = None
    display: str = "N/A"
    benchmark: str = ""
    status: str = ""
    status_class: str = "neutral"
    implication: str = ""

class FinancialPerformance(BaseModel):
    statements: Statements = Field(default_factory=Statements)
    consolidated_statements: Optional[Statements] = None
    ratios: List[RatioRow] = Field(default_factory=list)

class BehaviourSignal(BaseModel):
    signal: str
    type: str = "Strength"
    type_class: str = "pass"
    observation: str = ""
    implication: str = ""

class BehaviourPrint(BaseModel):
    signals: List[BehaviourSignal] = Field(default_factory=list)

class ComplianceCheck(BaseModel):
    check: str
    result: str = ""
    status: str = ""
    status_class: str = "neutral"
    implication: str = ""

class ComplianceIntelligence(BaseModel):
    checks: List[ComplianceCheck] = Field(default_factory=list)

class LegalCase(BaseModel):
    forum: str
    matter_type: str
    count: int = 0
    status: str = "Clear"
    status_class: str = "pass"
    implication: str = ""

class DetailedCourtCase(BaseModel):
    cnr: Optional[str] = None
    court: str = ""
    case_type: str = ""
    matter_type: str = ""
    parties: str = ""
    status: str = "Pending"
    filing_date: Optional[str] = None
    risk_tag: Optional[str] = None
    status_class: str = "warn"

class LegalLitigation(BaseModel):
    cases: List[LegalCase] = Field(default_factory=list)
    detailed_cases: List[DetailedCourtCase] = Field(default_factory=list)
    summary_text: str = ""

class ChargeEntry(BaseModel):
    charge_id: str
    lender: str = "Unknown"
    amount: Optional[float] = None
    display_amount: str = "-"
    created: str = "N/A"
    status: str = "Active"
    status_class: str = "warn"
    risk_note: str = "Standard charge registry entry"

class ChargeSummaryInfo(BaseModel):
    open_charge_count: int = 0
    satisfied_charge_count: int = 0
    total_charge_count: int = 0
    total_open_registered_amount: Optional[float] = None
    display_total_open_amount: str = "-"

class ChargeRegister(BaseModel):
    summary: Optional[ChargeSummaryInfo] = None
    charges: List[ChargeEntry] = Field(default_factory=list)
    empty_text: Optional[str] = None
    summary_text: str = ""

class TraceSignal(BaseModel):
    signal_id: int
    track: str = ""
    type: str = "Positive"
    title: str = ""
    description: str = ""
    watchpoint: Optional[str] = None

class TraceLayer(BaseModel):
    headline: str = ""
    signals: List[TraceSignal] = Field(default_factory=list)
    methodology_note: Optional[str] = None

class MonitoringTrigger(BaseModel):
    trigger: str
    event: str = ""
    auto_response: str = ""
    required_action: str = ""

class UpsellCTA(BaseModel):
    title: str = ""
    description: str = ""

class MonitoringConditions(BaseModel):
    validity_text: str = ""
    triggers: List[MonitoringTrigger] = Field(default_factory=list)
    upsell: UpsellCTA = Field(default_factory=UpsellCTA)

class Footer(BaseModel):
    brand: str = "RyskNode · Pralyon Intelligence Suite"
    report_line: str = ""
    confidentiality: str = "Confidential · Internal use only · Not for redistribution"


# ─────────────────────────────────────────────────────────────────────────
# S1 Top-Level Response (granular, UI-component structure)
# ─────────────────────────────────────────────────────────────────────────

class BuyerAssessResponse(BaseModel):
    model_config = {"extra": "ignore"}

    overview: Overview = Field(default_factory=Overview)
    entity_identity: EntityIdentity = Field(default_factory=EntityIdentity)
    director_profile: DirectorProfile = Field(default_factory=DirectorProfile)
    zeropass: ZeroPass = Field(default_factory=ZeroPass)
    tri_core: TriCore = Field(default_factory=TriCore)
    financial_performance: FinancialPerformance = Field(default_factory=FinancialPerformance)
    behaviour_print: BehaviourPrint = Field(default_factory=BehaviourPrint)
    compliance_intelligence: ComplianceIntelligence = Field(default_factory=ComplianceIntelligence)
    legal_litigation: LegalLitigation = Field(default_factory=LegalLitigation)
    charge_register: ChargeRegister = Field(default_factory=ChargeRegister)
    trace_layer: TraceLayer = Field(default_factory=TraceLayer)
    monitoring_conditions: MonitoringConditions = Field(default_factory=MonitoringConditions)
    footer: Footer = Field(default_factory=Footer)


# ─────────────────────────────────────────────────────────────────────────
# S2 Response (unchanged — kept for credit-limit endpoint)
# ─────────────────────────────────────────────────────────────────────────

class CreditLimitResponse(BaseModel):
    entity_id: str
    seller_id: str
    assessed_at: datetime
    pralyon_score: int
    risk_band: str
    blended_pd: float
    lgd_estimate: float
    conduct_score: float
    financial_score: float
    identity_score: float
    legal_score: float
    documentation_score: float
    xai_summary: Optional[str] = Field(None, description="Short TraceLayer™ executive summary for top verdict banner")
    xai_summary_text: Optional[str] = Field(None, description="Plain text version of TraceLayer™ executive summary")
    dimension_readings: Dict[str, str] = Field(default_factory=dict, description="TraceLayer™ readings per Tri-Core dimension (financial, identity, legal, conduct)")
    table_enrichments: Dict[str, Any] = Field(default_factory=dict, description="TraceLayer™ derived seller implications and required actions")
    lime_methodology_note: Optional[str] = Field(None, description="TraceLayer™ model-agnostic calculation methodology note for report footer")
    xai_narrative: str
    xai_narrative_text: Optional[str] = Field(None, description="Plain text version of XAI narrative")
    xai_narrative_lines: List[str] = Field(default_factory=list, description="List of plain text narrative lines/paragraphs")
    shap_top_features: List[Any]
    shap_ranked: List[Any] = Field(default_factory=list, description="Full feature attribution list")
    lime_explanation: Dict[str, Any] = Field(default_factory=dict, description="Local threshold rules dictionary")
    data_sources_used: List[str]
    pipeline_version: str

    # S2 limit and schedules
    evaluated_limit: float
    recommended_tenor: int
    advance_required: float
    tenor_schedule: List[Dict[str, Any]]
    stress_table: List[Dict[str, Any]]

    metadata: Dict[str, Any] = Field(default_factory=dict)
    input_parameters: Dict[str, Any] = Field(default_factory=dict, description="The 36 feature columns used as input to the ML scoring engine")
    final_feature_row: Optional[FinalFeatureRow] = None
