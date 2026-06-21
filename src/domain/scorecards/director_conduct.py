"""Director conduct scorecard adjustments.

Patch L — new module.

Applies conduct score penalties derived from MCA director signals.
Penalty schedule:

  high_director_company_count → −3
    A director sitting on 20+ companies simultaneously is a flag for:
    shell-company structures, nominee directors, or stretched attention.
    Not a hard decline — legitimate group structures can trigger this.

Design note:
  The hard-decline gate for wilful defaulter directors is handled upstream
  in run_part1.py BEFORE scoring. This module only handles non-hard-gate
  conduct adjustments.
"""

from __future__ import annotations

from typing import Any


def apply_director_conduct_adjustments(
    base_conduct_score: int,
    director_signals: dict[str, Any],
) -> tuple[int, list[str]]:
    """
    Apply director-derived conduct adjustments.
    Returns (adjusted_score, reason_codes).
    """
    score = int(base_conduct_score)
    reasons: list[str] = []

    if director_signals.get("high_director_company_count"):
        score -= 3
        reasons.append("HIGH_DIRECTOR_COMPANY_COUNT")

    score = max(0, min(100, score))
    return score, reasons
