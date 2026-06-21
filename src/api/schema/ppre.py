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
    xai_narrative: str
    shap_top_features: List[Any]
    data_sources_used: List[str]
    pipeline_version: str
    metadata: Dict[str, Any] = Field(default_factory=dict)


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
    xai_narrative: str
    shap_top_features: List[Any]
    data_sources_used: List[str]
    pipeline_version: str

    # S2 limit and schedules
    evaluated_limit: float
    recommended_tenor: int
    advance_required: float
    tenor_schedule: List[Dict[str, Any]]
    stress_table: List[Dict[str, Any]]

    metadata: Dict[str, Any] = Field(default_factory=dict)
