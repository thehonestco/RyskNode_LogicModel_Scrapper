"""GST filing conduct scorecard adjustments.

Patch L — new module.

Applies conduct score penalties derived from GST filing consistency.
Penalty schedule:

  non-filer   → −8  (entity is not filing GST returns at all — serious compliance gap)
  irregular   → −3  (intermittent filing — moderate conduct concern)

Design note:
  A non-filer penalty of −8 is calibrated to push an entity from a marginal
  Band B into Band C without being a hard decline. A valid reason (e.g. below
  GST threshold turnover) should be captured in underwriter notes.
"""
from __future__ import annotations

from typing import Any


def apply_gst_conduct_adjustments(
    base_conduct_score: int,
    gst_signals: dict[str, Any],
) -> tuple[int, list[str]]:
    """
    Apply GST filing conduct adjustments.
    Returns (adjusted_score, reason_codes).
    """
    score = int(base_conduct_score)
    reasons: list[str] = []

    filing = (gst_signals.get("gst_filing_consistency") or "").lower()

    if filing == "non-filer":
        score -= 8
        reasons.append("GST_NON_FILER")
    elif filing == "irregular":
        score -= 3
        reasons.append("GST_FILING_IRREGULAR")

    score = max(0, min(100, score))
    return score, reasons
