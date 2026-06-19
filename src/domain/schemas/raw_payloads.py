"""Raw API payload schema — stores exactly what each source returns."""
from pydantic import BaseModel, Field
from typing import Optional, Any
from datetime import datetime


class RawPayload(BaseModel):
    payload_id: str = Field(..., description="Unique payload key (UUID)")
    entity_lookup_key: str = Field(..., description="Key used to fetch: GSTIN / CIN / name")
    source_name: str = Field(..., description="mca | gst | udyam | ecourts")
    request_timestamp: datetime = Field(..., description="UTC timestamp of API call")
    raw_json: dict[str, Any] = Field(..., description="Original response payload")
    source_status: str = Field(..., description="success | error | partial")
    pipeline_version: str = Field(default="1.0.0")
    http_status_code: Optional[int] = None
    error_message: Optional[str] = None
