"""
part1/scorecards/documentation_score.py
========================================
Documentation Reliability Score — measures source quality, freshness,
filing regularity, cross-source consistency, and historical depth.

DIRECTION: Higher score = STRONGER, fresher, more consistent documentation (good score).

Formula (per spec)
------------------
  Component                    Weight   Signal source
  ──────────────────────────── ──────   ──────────────────────────────────────────────
  1. Official-source quality     20 %   source quality tier (official/verified/mixed/weak)
  2. Filing regularity           20 %   gst_filing_periods_filed / gst_filing_periods_total
  3. Freshness                   20 %   source_freshness_days
  4. Cross-source consistency    25 %   conflict_flags count (raised from 10% to 25%)
  5. Historical depth            15 %   years of usable financial history from turnover series

  documentation_score = Σ (weight_i × component_score_i) / 100

Reason codes
------------
  DOCUMENTATION_NOT_FRESH        — freshness > 90 days
  FILING_REGULARITY_WEAK         — filing ratio < 0.50
  CROSS_SOURCE_MISMATCH          — multiple conflict flags detected
  HISTORICAL_DEPTH_LIMITED       — fewer than 3 years of financial data
  OFFICIAL_SOURCE_WEAK           — fewer than 2 official sources present
"""

from __future__ import annotations

from typing import Optional

from domain.schemas.normalized_record import NormalizedRecord
from domain.schemas.score_components import ComponentScore, DomainScore
from domain.scorecards.weighted_average import build_domain_score

# Official/verified source tiers for quality scoring
OFFICIAL_SOURCES = {"mca", "gst", "ecourts", "cbdt"}
VERIFIED_SOURCES = {"udyam", "epfo", "gstn", "roc"}


# ---------------------------------------------------------------------------
# Component scorers
# ---------------------------------------------------------------------------


def _score_official_source_availability(sources: list[str]) -> tuple[float, str]:
    """
    100 if all key sources are official/verified.
    70  if mostly official (≥2 official sources).
    40  if mixed (1 official + others).
    0   if no official sources.
    """
    sources_lower = {s.lower() for s in sources}
    n_official = len(sources_lower & OFFICIAL_SOURCES)
    n_verified = len(sources_lower & VERIFIED_SOURCES)
    n_quality = n_official + n_verified

    if n_official >= 3:
        return 100.0, "ALL_KEY_SOURCES_OFFICIAL"
    if n_official >= 2 or n_quality >= 3:
        return 70.0, "MOSTLY_OFFICIAL_SOURCES"
    if n_official >= 1:
        return 40.0, "MIXED_SOURCE_QUALITY"
    return 0.0, "OFFICIAL_SOURCE_WEAK"


def _score_filing_regularity(
    filed: Optional[int],
    total: Optional[int],
) -> tuple[float, str]:
    """
    filing_ratio = gst_filing_periods_filed / gst_filing_periods_total.
    100 ≥ 0.90 (regular).
    70  ≥ 0.70 (minor gaps).
    40  ≥ 0.50 (repeated delays).
    0   < 0.50 (major failure).
    """
    if filed is None or total is None or total == 0:
        return 50.0, "FILING_REGULARITY_UNKNOWN"
    ratio = filed / total
    if ratio >= 0.90:
        return 100.0, "FILING_REGULAR"
    if ratio >= 0.70:
        return 70.0, "FILING_MINOR_GAPS"
    if ratio >= 0.50:
        return 40.0, "FILING_REGULARITY_WEAK"
    return 0.0, "FILING_REGULARITY_WEAK"


def _score_freshness(freshness_days: Optional[float]) -> tuple[float, str]:
    """Freshness bands per spec: ≤7d=100, ≤30d=75, ≤90d=50, ≤180d=25, older=0."""
    if freshness_days is None:
        return 50.0, "FRESHNESS_UNKNOWN"
    if freshness_days <= 7:
        return 100.0, "DATA_FRESH"
    if freshness_days <= 30:
        return 75.0, "DATA_RECENT"
    if freshness_days <= 90:
        return 50.0, "DOCUMENTATION_NOT_FRESH"
    if freshness_days <= 180:
        return 25.0, "DOCUMENTATION_NOT_FRESH"
    return 0.0, "DOCUMENTATION_NOT_FRESH"


def _score_cross_source_consistency(conflict_flags: list[str]) -> tuple[float, str]:
    """
    100  no conflicts.
    70   1 minor conflict.
    40   2 conflicts.
    0    3+ conflicts.
    """
    n = len(conflict_flags)
    if n == 0:
        return 100.0, "NO_SOURCE_CONFLICTS"
    if n == 1:
        return 70.0, "MINOR_SOURCE_CONFLICT"
    if n == 2:
        return 40.0, "CROSS_SOURCE_MISMATCH"
    return 0.0, "CROSS_SOURCE_MISMATCH"


def _score_historical_depth(record: NormalizedRecord) -> tuple[float, str]:
    """
    Count years of usable financial data from the turnover series.
    5yr=100, 4yr=80, 3yr=60, 2yr=30, <2yr=0.
    """
    years = sum(
        [
            1 if record.turnover_y1 is not None else 0,
            1 if record.turnover_y2 is not None else 0,
            1 if record.turnover_y3 is not None else 0,
            1 if record.turnover_y4 is not None else 0,
            1 if record.turnover_y5 is not None else 0,
        ]
    )
    if years >= 5:
        return 100.0, "HISTORICAL_DEPTH_STRONG"
    if years == 4:
        return 80.0, "HISTORICAL_DEPTH_GOOD"
    if years == 3:
        return 60.0, "HISTORICAL_DEPTH_ADEQUATE"
    if years == 2:
        return 30.0, "HISTORICAL_DEPTH_LIMITED"
    return 0.0, "HISTORICAL_DEPTH_LIMITED"


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------


def compute_documentation_score(
    record: NormalizedRecord,
    source_freshness_days: Optional[float] = None,
) -> DomainScore:
    """
    Compute Documentation Reliability Score (0–100; higher = better).

    Parameters
    ----------
    record               : NormalizedRecord — canonical entity record
    source_freshness_days: float — age of latest reliable filing in days

    Returns
    -------
    DomainScore with 5 components, weights summing to 100.
    """
    components = []

    # 1. Official-source availability (weight 20)
    s, r = _score_official_source_availability(record.sources_available)
    components.append(
        ComponentScore(
            component_name="official_source_availability",
            raw_value=float(len(record.sources_available)),
            normalized_score=s,
            weight=20.0,
            reason_code=r,
        )
    )

    # 2. Filing regularity (weight 20)
    s, r = _score_filing_regularity(
        record.gst_filing_periods_filed,
        record.gst_filing_periods_total,
    )
    components.append(
        ComponentScore(
            component_name="filing_regularity",
            raw_value=(
                round(record.gst_filing_periods_filed / record.gst_filing_periods_total, 3)
                if record.gst_filing_periods_filed is not None and record.gst_filing_periods_total
                else None
            ),
            normalized_score=s,
            weight=20.0,
            reason_code=r,
        )
    )

    # 3. Freshness (weight 20)
    s, r = _score_freshness(source_freshness_days)
    components.append(
        ComponentScore(
            component_name="freshness",
            raw_value=source_freshness_days,
            normalized_score=s,
            weight=20.0,
            reason_code=r,
        )
    )

    # 4. Cross-source consistency (weight 25  — raised from 10%)
    s, r = _score_cross_source_consistency(record.conflict_flags)
    components.append(
        ComponentScore(
            component_name="cross_source_consistency",
            raw_value=float(len(record.conflict_flags)),
            normalized_score=s,
            weight=25.0,
            reason_code=r,
        )
    )

    # 5. Historical depth (weight 15)
    s, r = _score_historical_depth(record)
    components.append(
        ComponentScore(
            component_name="historical_depth",
            raw_value=float(
                sum(
                    [
                        1 if record.turnover_y1 is not None else 0,
                        1 if record.turnover_y2 is not None else 0,
                        1 if record.turnover_y3 is not None else 0,
                        1 if record.turnover_y4 is not None else 0,
                        1 if record.turnover_y5 is not None else 0,
                    ]
                )
            ),
            normalized_score=s,
            weight=15.0,
            reason_code=r,
        )
    )

    return build_domain_score("documentation", components)
