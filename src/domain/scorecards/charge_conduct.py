from __future__ import annotations

from typing import Any


def apply_charge_conduct_adjustments(base_conduct_score: int, charge_signals: dict[str, Any]) -> tuple[int, list[str]]:
    score = int(base_conduct_score)
    reasons: list[str] = []

    lender_quality = charge_signals.get("lender_quality_flag", "NO_LENDER")
    if lender_quality == "PSU_BANK":
        score += 3
        reasons.append("CHARGE_LENDER_QUALITY_PSU")
    elif lender_quality == "PRIVATE_BANK":
        score += 1
        reasons.append("CHARGE_LENDER_QUALITY_PRIVATE")
    elif lender_quality == "NBFC_ONLY":
        score -= 3
        reasons.append("CHARGE_LENDER_QUALITY_NBFC_ONLY")

    if charge_signals.get("old_unsatisfied_charge_count", 0) > 0:
        score -= 4
        reasons.append("OLD_UNSATISFIED_CHARGE")

    if charge_signals.get("has_recent_charge_90d", False):
        reasons.append("RECENT_CHARGE_90D")

    score = max(0, min(100, score))
    return score, reasons
