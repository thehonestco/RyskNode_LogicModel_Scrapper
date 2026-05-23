from typing import Optional

from pydantic import BaseModel, Field, field_validator


class SyncRequest(BaseModel):
    statecode: Optional[str] = Field(
        None,
        description="Optional state code to synchronize company master data for. If omitted, all states will be synchronized.",
        examples=["orissa"],
    )
    cooldown: Optional[int] = Field(
        None,
        description="Optional cooldown time in seconds to sleep after facing a rate limit (HTTP 429).",
        examples=[120],
    )
    offset: Optional[int] = Field(
        None,
        description="Optional offset to resume synchronization from. If omitted, resumes from the latest matching markdown report.",
        examples=[282540],
    )

    @field_validator("statecode")
    @classmethod
    def validate_statecode(cls, v: Optional[str]) -> Optional[str]:
        if v is not None:
            cleaned = v.strip()
            if not cleaned:
                return None
            return cleaned
        return None
