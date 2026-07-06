from __future__ import annotations

from typing import Any


def check_rbi_wilful_defaulter(rbi_raw: dict[str, Any] | None) -> dict[str, Any]:
    """
    Hard-gate check against RBI wilful defaulter / CRILC data.

    Input: raw response from aggregator RBI defaulter API call.
    Returns a result dict consumed by the pipeline runner before any scoring.

    If entity is on the wilful defaulter list:
      -> band = D, score = 0, limit = 0, tenor = None
      -> pipeline must return this result immediately without scoring.
    """
    if not rbi_raw:
        return {"is_wilful_defaulter": False, "source_notes": ["RBI_DEFAULTER_DATA_UNAVAILABLE"]}

    is_defaulter = bool(rbi_raw.get("wilful_defaulter") or rbi_raw.get("is_wilful_defaulter"))
    notes = []
    if is_defaulter:
        notes.append("RBI_WILFUL_DEFAULTER")

    return {
        "is_wilful_defaulter": is_defaulter,
        "source_notes": notes,
    }


def build_hard_decline_result(entity_key: str, reason: str) -> dict[str, Any]:
    """Build a terminal hard-decline result dict (no scoring required)."""
    return {
        "entity_key": entity_key,
        "band": "D",
        "score": 0,
        "limit": 0,
        "tenor": None,
        "decision": "DECLINE",
        "hard_decline_reason": reason,
        "source_notes": [reason],
        "hard_decline": True,
    }
