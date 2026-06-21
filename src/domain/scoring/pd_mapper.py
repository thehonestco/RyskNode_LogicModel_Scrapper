"""
part2/scoring/pd_mapper.py
===========================
PD Band Mapper — derives the credit risk band from Part 1 domain scores.

This module is the bridge between Part 1 (scorecard engine) and
Part 2 (limit advisor). It:

  1. Runs a confidence gate on identity and documentation scores.
  2. Computes a composite governance score from the four domain scores.
  3. Maps the governance score to a master-scale PD band (AAA → D).
  4. Applies hard overrides that can only WORSEN the band, never improve it.

The band output from this module is the sole band input to limit_advisor.
The band is NEVER entered manually by the Seller or analyst.

Governance score formula
------------------------
  legal_health_score = 100 − legal_risk_score     (invert bad→good direction)

  governance_score = (
      0.15 × identity_score
    + 0.40 × financial_score
    + 0.30 × legal_health_score
    + 0.15 × documentation_score
  )

Weights rationale
-----------------
  financial_score     40 % — primary operating strength signal
  legal_health_score  30 % — risk severity; legal failure is a hard stopper
  identity_score      15 % — data confidence gate
  documentation_score 15 % — source quality and freshness penalty

Band mapping
------------
  governance_score   band
  ─────────────────  ────
  90 – 100           AAA
  80 –  90           AA
  70 –  80           A
  55 –  70           BBB
  40 –  55           BB
  25 –  40           B
  10 –  25           CCC
   0 –  10           D

Hard overrides (post-mapping, worsen only)
------------------------------------------
  criminal_case_count  ≥ 1       → down 2 notches
  legal_risk_score     > 70      → down 1 notch
  debt_to_equity       > 3       → down 1 notch
  current_ratio        < 1       → down 1 notch
  business_vintage_years < 1     → floor = B (cannot be better than B)

Confidence gate
---------------
  identity_score      < 60  → UNSCOREABLE  (limit = 0)
  documentation_score < 50  → data_penalty = 0.80 applied to final limit

All values 0–100 except raw financial ratios.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import List, Optional


# ---------------------------------------------------------------------------
# Band ordering (worst → best)
# ---------------------------------------------------------------------------
BAND_ORDER: List[str] = ["D", "CCC", "B", "BB", "BBB", "A", "AA", "AAA"]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------


@dataclass
class PDMapResult:
    pd_band: str  # final band after all overrides
    band_before_override: str  # band from score mapping only
    governance_score: float  # 0–100 composite score
    legal_health_score: float  # 100 − legal_risk_score
    data_penalty: float  # 1.0 or 0.80
    unscoreable: bool  # True if identity gate failed
    override_flags: List[str] = field(default_factory=list)
    reason_codes: List[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _score_to_band(score: float) -> str:
    """Map governance score to master-scale PD band."""
    if score >= 90:
        return "AAA"
    if score >= 80:
        return "AA"
    if score >= 70:
        return "A"
    if score >= 55:
        return "BBB"
    if score >= 40:
        return "BB"
    if score >= 25:
        return "B"
    if score >= 10:
        return "CCC"
    return "D"


def _worsen_band(band: str, notches: int) -> str:
    """Move band down by `notches` positions. Cannot go below D."""
    idx = BAND_ORDER.index(band)
    new_idx = max(0, idx - notches)
    return BAND_ORDER[new_idx]


def _apply_floor(band: str, floor: str) -> str:
    """Ensure band is no better than `floor`."""
    band_idx = BAND_ORDER.index(band)
    floor_idx = BAND_ORDER.index(floor)
    return BAND_ORDER[min(band_idx, floor_idx)]


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def derive_pd_band(
    # ─ Part 1 domain scores (0–100) ───────────────────────────────────────────
    identity_score: float,
    financial_score: float,
    legal_risk_score: float,  # higher = more risk (bad score)
    documentation_score: float,
    # ─ Hard-override inputs (raw values from NormalizedRecord / computed) ─
    criminal_case_count: Optional[int] = None,
    debt_to_equity: Optional[float] = None,
    current_ratio: Optional[float] = None,
    business_vintage_years: Optional[float] = None,
) -> PDMapResult:
    """
    Derive PD band from Part 1 domain scores.

    Parameters
    ----------
    identity_score         : 0–100, higher = better identity confidence
    financial_score        : 0–100, higher = better financial health
    legal_risk_score       : 0–100, higher = MORE legal risk (bad direction)
    documentation_score    : 0–100, higher = better documentation quality
    criminal_case_count    : raw count from NormalizedRecord
    debt_to_equity         : computed ratio from financial layer
    current_ratio          : computed ratio from financial layer
    business_vintage_years : years since incorporation

    Returns
    -------
    PDMapResult with pd_band, governance_score, data_penalty,
    override_flags, and reason_codes.
    """
    override_flags: List[str] = []
    reason_codes: List[str] = []

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 1 — Confidence gate
    # ─────────────────────────────────────────────────────────────────────────
    if identity_score < 60:
        reason_codes.append("IDENTITY_GATE_FAILED_UNSCOREABLE")
        return PDMapResult(
            pd_band="UNSCOREABLE",
            band_before_override="UNSCOREABLE",
            governance_score=0.0,
            legal_health_score=0.0,
            data_penalty=1.0,
            unscoreable=True,
            override_flags=[],
            reason_codes=reason_codes,
        )

    data_penalty = 1.0
    if documentation_score < 50:
        data_penalty = 0.80
        reason_codes.append("DOCUMENTATION_PENALTY_APPLIED_0.80")

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 2 — Composite governance score
    # legal_risk_score is a BAD score (high = risky).
    # Invert it so all four inputs point in the same good direction.
    # ─────────────────────────────────────────────────────────────────────────
    legal_health_score = round(100.0 - float(legal_risk_score), 4)

    governance_score = round(
        0.15 * float(identity_score)
        + 0.40 * float(financial_score)
        + 0.30 * legal_health_score
        + 0.15 * float(documentation_score),
        4,
    )

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 3 — Score → band
    # ─────────────────────────────────────────────────────────────────────────
    band = _score_to_band(governance_score)
    band_before_override = band

    # ─────────────────────────────────────────────────────────────────────────
    # Layer 4 — Hard overrides (worsen only, never improve)
    # ─────────────────────────────────────────────────────────────────────────

    # Override 1: criminal cases → down 2 notches
    if criminal_case_count is not None and int(criminal_case_count) >= 1:
        band = _worsen_band(band, 2)
        override_flags.append("CRIMINAL_CASE_DOWNGRADE_2_NOTCHES")
        reason_codes.append("CRIMINAL_CASE_DETECTED")

    # Override 2: high legal risk score → down 1 notch
    if float(legal_risk_score) > 70:
        band = _worsen_band(band, 1)
        override_flags.append("HIGH_LEGAL_RISK_DOWNGRADE_1_NOTCH")
        reason_codes.append("LEGAL_RISK_SCORE_ELEVATED")

    # Override 3: high leverage → down 1 notch
    if debt_to_equity is not None and float(debt_to_equity) > 3:
        band = _worsen_band(band, 1)
        override_flags.append("HIGH_LEVERAGE_DOWNGRADE_1_NOTCH")
        reason_codes.append("DEBT_TO_EQUITY_EXCEEDS_3x")

    # Override 4: negative liquidity → down 1 notch
    if current_ratio is not None and float(current_ratio) < 1:
        band = _worsen_band(band, 1)
        override_flags.append("LIQUIDITY_STRESS_DOWNGRADE_1_NOTCH")
        reason_codes.append("CURRENT_RATIO_BELOW_1")

    # Override 5: very new business → floor = B
    if business_vintage_years is not None and float(business_vintage_years) < 1:
        band = _apply_floor(band, "B")
        override_flags.append("NEW_BUSINESS_FLOOR_B")
        reason_codes.append("BUSINESS_VINTAGE_BELOW_1_YEAR")

    # Final reason codes from score
    if governance_score < 40:
        reason_codes.append("LOW_GOVERNANCE_SCORE")
    if legal_health_score < 50:
        reason_codes.append("LEGAL_HEALTH_SCORE_WEAK")
    if financial_score < 50:
        reason_codes.append("FINANCIAL_SCORE_WEAK")

    return PDMapResult(
        pd_band=band,
        band_before_override=band_before_override,
        governance_score=governance_score,
        legal_health_score=legal_health_score,
        data_penalty=data_penalty,
        unscoreable=False,
        override_flags=override_flags,
        reason_codes=reason_codes,
    )
