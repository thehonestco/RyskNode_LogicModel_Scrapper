import re
from typing import List, Union

from pydantic import BaseModel, Field, field_validator

CIN_REGEX = re.compile(r"^[UL][0-9]{5}[A-Z]{2}[0-9]{4}[A-Z]{3}[0-9]{6}$", re.I)


class ScrapeRequest(BaseModel):
    queries: Union[List[str], str] = Field(
        ...,
        description="A single CIN/Company Name or a list of CINs/Company Names",
        examples=["U62010UP2023PTC191535", ["U62010UP2023PTC191535", "Apurva Natvar Parikh"]],
    )

    @field_validator("queries")
    @classmethod
    def validate_queries(cls, v: Union[List[str], str]) -> Union[List[str], str]:
        if isinstance(v, list):
            # Clean and deduplicate list while preserving order
            seen = set()
            unique_v = []
            for item in v:
                if isinstance(item, str):
                    item = item.strip()
                    if item and item not in seen:
                        unique_v.append(item)
                        seen.add(item)
            if not unique_v:
                raise ValueError("List of queries cannot be empty or contain only whitespace")
            return unique_v
        if isinstance(v, str):
            v = v.strip()
            if not v:
                raise ValueError("Query string cannot be empty")
            return v
        return v


class ScrapeAcknowledgment(BaseModel):
    status: str
    total_queries: int
