"""eCourts conduct scorecard adjustments.

Patch L — new module.

Applies conduct score penalties derived from eCourts case signals.
Penalty schedule (all cumulative, capped 0-100):

  has_insolvency_petition  → −15  (near hard-decline; NCLT petition is severe)
  case_count_drt > 0       → −5   (DRT filing = lender has dragged entity to tribunal)
  case_count_active > 3    → −3   (multiple active cases = operational/legal stress)

Design note:
  Insolvency penalty is intentionally large (−15) to push borderline entities
  into Band C or D, forcing manual review. It is NOT a hard decline because
  a petition may have been filed by a creditor and subsequently stayed.
"""
from __future__ import annotations

from typing import Any


def apply_ecourts_conduct_adjustments(
    base_conduct_score: int,
    ecourts_signals: dict[str, Any],
) -> tuple[int, list[str]]:
    """
    Apply eCourts-derived conduct adjustments.
    Returns (adjusted_score, reason_codes).
    """
    score = int(base_conduct_score)
    reasons: list[str] = []

    if ecourts_signals.get("has_insolvency_petition"):
        score -= 15
        reasons.append("ECOURTS_INSOLVENCY_PETITION")

    if ecourts_signals.get("case_count_drt", 0) > 0:
        score -= 5
        reasons.append("ECOURTS_DRT_CASE")

    if ecourts_signals.get("case_count_active", 0) > 3:
        score -= 3
        reasons.append("ECOURTS_HIGH_ACTIVE_CASES")

    score = max(0, min(100, score))
    return score, reasons
