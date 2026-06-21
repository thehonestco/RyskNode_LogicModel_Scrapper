"""
part1/scorecards/legal_score.py
================================
Legal Risk Score — measures litigation burden and adverse legal activity.

DIRECTION: Higher score = HIGHER legal risk (bad score).

Formula (per spec)
------------------
  Component                   Weight   Raw signal
  ─────────────────────────── ──────   ──────────────────────────────────────────────
  1. Pending case intensity     25 %   pending_case_count / max(business_vintage_years, 1)
  2. Total case intensity       15 %   legal_case_count   / max(business_vintage_years, 1)
  3. Severe case mix            30 %   (criminal + high-value) / max(legal_case_count, 1)
  4. Recent adverse activity    20 %   recent_cases_24m   / max(legal_case_count, 1)
  5. Case concentration spike   10 %   burst of cases in last 12 months

  legal_risk_score = Σ (weight_i × component_score_i) / 100

Reason codes
------------
  HIGH_PENDING_INTENSITY        — pending intensity > 2 per year of vintage
  ELEVATED_SEVERE_CASE_MIX      — severe cases > 25% of total
  RECENT_ADVERSE_ACTIVITY       — recent_cases_24m / total > 0.5
  LITIGATION_CONCENTRATION_SPIKE — strong burst in last 12 months
  NO_LEGAL_RISK                 — all components zero

New fields used from NormalizedRecord (added as Optional — no breaking change)
  recent_cases_24m  : int   — cases filed in last 24 months
  recent_cases_12m  : int   — cases filed in last 12 months
  high_value_case_count : int — already present in previous version
"""

from __future__ import annotations

from typing import Optional

from domain.schemas.score_components import ComponentScore, DomainScore
from domain.scorecards.weighted_average import build_domain_score


# ---------------------------------------------------------------------------
# Component scorers  (all return 0–100; higher = more risk)
# ---------------------------------------------------------------------------


def _score_pending_intensity(pending: Optional[int], vintage: Optional[float]) -> tuple[float, str]:
    """Pending case intensity = pending_count / max(vintage_years, 1)."""
    if pending is None:
        return 50.0, "PENDING_INTENSITY_UNKNOWN"
    v = max(float(vintage or 1), 1.0)
    intensity = pending / v
    if intensity == 0:
        return 0.0, "PENDING_INTENSITY_ZERO"
    if intensity <= 0.5:
        return 25.0, "PENDING_INTENSITY_LOW"
    if intensity <= 1.0:
        return 50.0, "PENDING_INTENSITY_MODERATE"
    if intensity <= 2.0:
        return 75.0, "HIGH_PENDING_INTENSITY"
    return 100.0, "HIGH_PENDING_INTENSITY"


def _score_total_intensity(total: Optional[int], vintage: Optional[float]) -> tuple[float, str]:
    """Total case intensity = total_case_count / max(vintage_years, 1)."""
    if total is None:
        return 50.0, "TOTAL_INTENSITY_UNKNOWN"
    v = max(float(vintage or 1), 1.0)
    intensity = total / v
    if intensity == 0:
        return 0.0, "TOTAL_INTENSITY_ZERO"
    if intensity <= 1.0:
        return 25.0, "TOTAL_INTENSITY_LOW"
    if intensity <= 2.0:
        return 50.0, "TOTAL_INTENSITY_MODERATE"
    if intensity <= 3.0:
        return 75.0, "TOTAL_INTENSITY_HIGH"
    return 100.0, "TOTAL_INTENSITY_VERY_HIGH"


def _score_severe_case_mix(
    criminal: Optional[int],
    high_value: Optional[int],
    total: Optional[int],
) -> tuple[float, str]:
    """
    Severe case mix = (criminal_cases + high_value_cases) / max(total_cases, 1).
    Binned as percentage of total.
    """
    if total is None or total == 0:
        if criminal and criminal > 0:
            return 100.0, "ELEVATED_SEVERE_CASE_MIX"
        return 0.0, "SEVERE_MIX_ZERO"
    severe = (criminal or 0) + (high_value or 0)
    pct = severe / max(total, 1)
    if pct == 0:
        return 0.0, "SEVERE_MIX_ZERO"
    if pct <= 0.10:
        return 25.0, "SEVERE_MIX_LOW"
    if pct <= 0.25:
        return 50.0, "ELEVATED_SEVERE_CASE_MIX"
    if pct <= 0.50:
        return 75.0, "ELEVATED_SEVERE_CASE_MIX"
    return 100.0, "ELEVATED_SEVERE_CASE_MIX"


def _score_recent_adverse(
    recent_24m: Optional[int],
    total: Optional[int],
) -> tuple[float, str]:
    """Recent adverse activity = recent_cases_24m / max(total_cases, 1)."""
    if recent_24m is None:
        return 50.0, "RECENT_ADVERSE_UNKNOWN"
    if recent_24m == 0:
        return 0.0, "NO_RECENT_ADVERSE"
    ratio = recent_24m / max(total or 1, 1)
    if ratio <= 0.25:
        return 25.0, "RECENT_ADVERSE_LOW"
    if ratio <= 0.50:
        return 50.0, "RECENT_ADVERSE_ACTIVITY"
    if ratio <= 0.75:
        return 75.0, "RECENT_ADVERSE_ACTIVITY"
    return 100.0, "RECENT_ADVERSE_ACTIVITY"


def _score_concentration_spike(
    recent_12m: Optional[int],
    total: Optional[int],
    vintage: Optional[float],
) -> tuple[float, str]:
    """
    Concentration spike: burst of cases in last 12 months relative to
    the historical annual run-rate = total / max(vintage, 1).
    """
    if recent_12m is None or recent_12m == 0:
        return 0.0, "NO_CONCENTRATION_SPIKE"
    v = max(float(vintage or 1), 1.0)
    annual_run_rate = (total or 0) / v
    if annual_run_rate == 0:
        # Any case in 12m when historical rate is zero = strong spike
        return 100.0 if recent_12m >= 2 else 50.0, "LITIGATION_CONCENTRATION_SPIKE"
    ratio = recent_12m / annual_run_rate
    if ratio <= 1.0:
        return 0.0, "NO_CONCENTRATION_SPIKE"
    if ratio <= 2.0:
        return 50.0, "LITIGATION_CONCENTRATION_SPIKE"
    return 100.0, "LITIGATION_CONCENTRATION_SPIKE"


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def compute_legal_score(
    legal_case_count: Optional[int] = None,
    pending_case_count: Optional[int] = None,
    criminal_case_count: Optional[int] = None,
    high_value_case_count: Optional[int] = None,
    business_vintage_years: Optional[float] = None,
    recent_cases_24m: Optional[int] = None,
    recent_cases_12m: Optional[int] = None,
) -> DomainScore:
    """
    Compute Legal Risk Score (0–100; higher = more risk).

    Parameters
    ----------
    legal_case_count       : Total historical case count (eCourts)
    pending_case_count     : Currently pending cases (eCourts)
    criminal_case_count    : Criminal cases (eCourts)
    high_value_case_count  : High-value cases (eCourts)
    business_vintage_years : Years since incorporation (MCA)
    recent_cases_24m       : Cases filed in last 24 months (eCourts)
    recent_cases_12m       : Cases filed in last 12 months (eCourts)

    Returns
    -------
    DomainScore with score, components, and reason codes.
    """
    components = []

    # 1. Pending case intensity (weight 25)
    s, r = _score_pending_intensity(pending_case_count, business_vintage_years)
    components.append(
        ComponentScore(
            component_name="pending_case_intensity",
            raw_value=pending_case_count,
            normalized_score=s,
            weight=25.0,
            reason_code=r,
        )
    )

    # 2. Total case intensity (weight 15)
    s, r = _score_total_intensity(legal_case_count, business_vintage_years)
    components.append(
        ComponentScore(
            component_name="total_case_intensity",
            raw_value=legal_case_count,
            normalized_score=s,
            weight=15.0,
            reason_code=r,
        )
    )

    # 3. Severe case mix (weight 30)
    s, r = _score_severe_case_mix(criminal_case_count, high_value_case_count, legal_case_count)
    components.append(
        ComponentScore(
            component_name="severe_case_mix",
            raw_value=float((criminal_case_count or 0) + (high_value_case_count or 0)),
            normalized_score=s,
            weight=30.0,
            reason_code=r,
        )
    )

    # 4. Recent adverse activity (weight 20)
    s, r = _score_recent_adverse(recent_cases_24m, legal_case_count)
    components.append(
        ComponentScore(
            component_name="recent_adverse_activity",
            raw_value=float(recent_cases_24m) if recent_cases_24m is not None else None,
            normalized_score=s,
            weight=20.0,
            reason_code=r,
        )
    )

    # 5. Case concentration spike (weight 10)
    s, r = _score_concentration_spike(recent_cases_12m, legal_case_count, business_vintage_years)
    components.append(
        ComponentScore(
            component_name="concentration_spike",
            raw_value=float(recent_cases_12m) if recent_cases_12m is not None else None,
            normalized_score=s,
            weight=10.0,
            reason_code=r,
        )
    )

    return build_domain_score("legal", components)
