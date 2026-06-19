from __future__ import annotations

from typing import Any


HIGH_COMPANY_COUNT_THRESHOLD = 20


def derive_director_conduct_signals(directors_raw: dict[str, Any] | None) -> dict[str, Any]:
    """
    Derive conduct signals from MCA ROC director data.
    Comes from the same MCA aggregator call — separate endpoint.

    Hard-gate: if any director is on wilful defaulter list
    -> this flag must be checked BEFORE scoring in the pipeline runner.
    """
    if not directors_raw:
        return {
            "director_wilful_defaulter": False,
            "high_director_company_count": False,
            "max_director_company_count": None,
            "source_notes": ["MCA_DIRECTORS_DATA_UNAVAILABLE"],
        }

    directors: list[dict] = directors_raw.get("directors") or []
    notes: list[str] = []
    any_wilful_defaulter = False
    max_company_count = 0

    for d in directors:
        if d.get("is_wilful_defaulter") or d.get("disqualified"):
            any_wilful_defaulter = True
            notes.append("DIRECTOR_WILFUL_DEFAULTER")
        company_count = d.get("associated_company_count") or 0
        if company_count > max_company_count:
            max_company_count = company_count

    high_count = max_company_count > HIGH_COMPANY_COUNT_THRESHOLD
    if high_count:
        notes.append("HIGH_DIRECTOR_COMPANY_COUNT")

    return {
        "director_wilful_defaulter": any_wilful_defaulter,
        "high_director_company_count": high_count,
        "max_director_company_count": max_company_count if max_company_count > 0 else None,
        "source_notes": notes,
    }
