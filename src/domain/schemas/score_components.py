"""Score component schemas for each domain scorecard."""

from pydantic import BaseModel, Field
from typing import Optional, Any


class ComponentScore(BaseModel):
    component_name: str
    raw_value: Optional[Any] = None
    normalized_score: float = Field(..., ge=0, le=100)
    weight: float = Field(..., gt=0)
    reason_code: Optional[str] = None


class DomainScore(BaseModel):
    domain: str  # identity | financial | legal | documentation
    components: list[ComponentScore]
    weighted_score: float = Field(..., ge=0, le=100)
    top_reason_codes: list[str] = Field(default_factory=list)
    data_sufficiency: str = "full"  # full | partial | insufficient


class IdentityScoreResult(DomainScore):
    domain: str = "identity"


class FinancialScoreResult(DomainScore):
    domain: str = "financial"


class LegalScoreResult(DomainScore):
    domain: str = "legal"


class DocumentationScoreResult(DomainScore):
    domain: str = "documentation"
