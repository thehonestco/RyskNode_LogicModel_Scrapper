"""
part1/compute/roc_directors.py
================================
Director conduct signals from MCA ROC directory data.

Per RA Model Doc Section 2.2 — HARD GATE:
  If ANY director of the BUYER is a wilful defaulter OR has been
  disqualified by MCA under Section 164 → INSTANT DECLINE.

Per RA Model Doc Section 5.5 — CONDUCT:
  high_director_company_count: True if any director sits on > 20 companies
  → conduct score adjustment: −3 (HIGH_DIRECTOR_COMPANY_COUNT)
"""
from __future__ import annotations

from typing import Any


HIGH_COMPANY_COUNT_THRESHOLD = 20


def derive_director_conduct_signals(directors_raw: dict[str, Any] | None) -> dict[str, Any]:
    """
    Derive conduct signals from MCA ROC director data.

    Hard-gate check:
      director_wilful_defaulter = True
      → INSTANT DECLINE before scoring (checked in pipeline runner)

    Note: "disqualified" under MCA Section 164 is treated as equivalent to
    wilful defaulter for hard-gate purposes per RA Model documentation.

    MCA disqualification fields we check (finanvo payload format):
      - d.get("disqualified")            → True/False or "Yes"/"No"
      - d.get("is_wilful_defaulter")     → True/False
      - d.get("din_disqualified")        → bool
      - d.get("disqualificationDetails") → non-empty list
    """
    if not directors_raw:
        return {
            "director_wilful_defaulter": False,
            "director_disqualified": False,
            "high_director_company_count": False,
            "max_director_company_count": None,
            "disqualified_director_names": [],
            "source_notes": ["MCA_DIRECTORS_DATA_UNAVAILABLE"],
        }

    directors: list[dict] = directors_raw.get("directors") or []
    notes: list[str] = []
    any_wilful_defaulter = False
    any_disqualified = False
    disqualified_names: list[str] = []
    max_company_count = 0

    for d in directors:
        director_name = (
            d.get("fullName") or d.get("name") or d.get("directorName") or "Unknown Director"
        )

        # Check 1: Explicit wilful defaulter flag
        if d.get("is_wilful_defaulter"):
            any_wilful_defaulter = True
            notes.append(f"DIRECTOR_WILFUL_DEFAULTER: {director_name}")

        # Check 2: MCA Section 164 Disqualification
        # Finanvo field can be bool True, string "Yes", or non-empty disqualification details
        disqualified_flag = d.get("disqualified")
        disq_details = d.get("disqualificationDetails") or []
        din_disq = d.get("din_disqualified")

        is_disq = (
            (disqualified_flag is True)
            or (str(disqualified_flag).lower() in ("yes", "true", "1"))
            or (din_disq is True)
            or (isinstance(disq_details, list) and len(disq_details) > 0)
        )

        if is_disq:
            any_disqualified = True
            disqualified_names.append(director_name)
            notes.append(f"DIRECTOR_MCA_DISQUALIFIED_SEC164: {director_name}")

        # Company count — check multiple field names
        company_count = (
            d.get("associated_company_count")
            or d.get("companyCount")
            or d.get("noOfCompanies")
            or 0
        )
        try:
            company_count = int(company_count)
        except (ValueError, TypeError):
            company_count = 0

        if company_count > max_company_count:
            max_company_count = company_count

    high_count = max_company_count > HIGH_COMPANY_COUNT_THRESHOLD
    if high_count:
        notes.append("HIGH_DIRECTOR_COMPANY_COUNT")

    # Per doc: wilful defaulter OR MCA disqualified → hard gate
    hard_gate_triggered = any_wilful_defaulter or any_disqualified

    return {
        "director_wilful_defaulter": hard_gate_triggered,
        "director_disqualified": any_disqualified,
        "high_director_company_count": high_count,
        "max_director_company_count": max_company_count if max_company_count > 0 else None,
        "disqualified_director_names": disqualified_names,
        "source_notes": notes,
    }
