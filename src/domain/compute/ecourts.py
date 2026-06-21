"""eCourts signal extraction.

Patch L — new module.

Extracts case count signals from eCourts raw API response
and produces a structured signal dict for conduct scoring.
"""

from __future__ import annotations

from typing import Any


def derive_ecourts_conduct_signals(ecourts_raw: dict[str, Any] | None) -> dict[str, Any]:
    """
    Derive conduct signals from eCourts raw data.

    Returns a dict with:
      case_count_total        int   — total cases (all types, all states)
      case_count_active       int   — active / pending cases
      case_count_drt          int   — Debt Recovery Tribunal cases
      case_count_nclt         int   — NCLT / insolvency cases
      case_count_hc           int   — High Court cases
      has_insolvency_petition bool  — True if any NCLT insolvency petition found
      source_notes            list  — reason codes for pipeline log
    """
    if not ecourts_raw:
        return {
            "case_count_total": 0,
            "case_count_active": 0,
            "case_count_drt": 0,
            "case_count_nclt": 0,
            "case_count_hc": 0,
            "has_insolvency_petition": False,
            "source_notes": ["ECOURTS_DATA_UNAVAILABLE"],
        }

    notes: list[str] = []

    case_count_total = int(ecourts_raw.get("case_count_total", 0) or 0)
    case_count_active = int(ecourts_raw.get("case_count_active", 0) or 0)
    case_count_drt = int(ecourts_raw.get("case_count_drt", 0) or 0)
    case_count_nclt = int(ecourts_raw.get("case_count_nclt", 0) or 0)
    case_count_hc = int(ecourts_raw.get("case_count_hc", 0) or 0)

    # Insolvency petition: explicit flag OR any NCLT case present
    has_insolvency = bool(ecourts_raw.get("has_insolvency_petition") or case_count_nclt > 0)

    if has_insolvency:
        notes.append("INSOLVENCY_PETITION_FOUND")
    if case_count_drt > 0:
        notes.append("DRT_CASE_FOUND")
    if case_count_active > 3:
        notes.append("HIGH_ACTIVE_CASE_COUNT")

    return {
        "case_count_total": case_count_total,
        "case_count_active": case_count_active,
        "case_count_drt": case_count_drt,
        "case_count_nclt": case_count_nclt,
        "case_count_hc": case_count_hc,
        "has_insolvency_petition": has_insolvency,
        "source_notes": notes,
    }
