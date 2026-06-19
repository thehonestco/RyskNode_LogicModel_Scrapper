from __future__ import annotations

from typing import Any


def apply_epfo_conduct_adjustments(
    base_conduct_score: int,
    epfo_signals: dict[str, Any],
) -> tuple[int, list[str]]:
    """
    Apply EPFO-derived conduct adjustments to the conduct score.
    Returns (adjusted_score, reason_codes).
    """
    score = int(base_conduct_score)
    reasons: list[str] = []

    if epfo_signals.get("revenue_per_employee_outlier"):
        score -= 4
        reasons.append("REVENUE_PER_EMPLOYEE_OUTLIER")

    if epfo_signals.get("pf_filing_regular") is False:
        score -= 5
        reasons.append("PF_FILING_IRREGULAR")

    score = max(0, min(100, score))
    return score, reasons
