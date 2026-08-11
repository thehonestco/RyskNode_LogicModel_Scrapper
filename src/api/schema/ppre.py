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


# Response Schemas
class BuyerAssessResponse(BaseModel):
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
    metadata: Dict[str, Any] = Field(default_factory=dict)
    input_parameters: Dict[str, Any] = Field(default_factory=dict, description="The 36 feature columns used as input to the ML scoring engine")
    final_feature_row: Optional[FinalFeatureRow] = None


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
