"""
part1/scorecards/financial_score.py
====================================
Financial scorecard — measures financial health, stability, and growth quality.

Sector-aware scoring
--------------------
All sub-scoring functions resolve thresholds from SECTOR_BENCHMARKS via
get_benchmarks(sector). The sector is derived from the buyer's NIC 2008
activity code using nic_mapper.resolve_sector(nic_code).

This means a CAPITAL_GOODS buyer (NIC 29xxx) with DSO=90 days scores
75/100 (DSO_GOOD) rather than 40/100 (DSO_HIGH) it would receive under
the old universal thresholds — because 90 days is within normal for that
sector. The scoring is now contextually fair.

The GENERAL benchmark group preserves the original universal thresholds
exactly, so any entity with a missing or unrecognised NIC code scores
identically to the pre-patch behaviour. Zero regression risk.

Weights (unchanged from v1)
---------------------------
  current_ratio        20%
  quick_ratio          15%
  debt_to_equity       20%
  net_revenue_cagr_5y  20%
  dso                  10%
  working_capital      10%
  business_vintage      5%
  Total               100%

Data flow
---------
  FinalFeatureRow.nic_code
      └─ nic_mapper.resolve_sector()   →  sector: str
          └─ sector_benchmarks.get_benchmarks()  →  thresholds: dict
              └─ _score_*() functions use thresholds
                  └─ build_domain_score()  →  DomainScore
"""

from __future__ import annotations

from typing import Optional

from domain.schemas.score_components import ComponentScore, DomainScore
from domain.scorecards.weighted_average import build_domain_score
from domain.scorecards.nic_mapper import resolve_sector, GENERAL
from domain.scorecards.sector_benchmarks import get_benchmarks
from domain.compute.safe_math import clamp


# ---------------------------------------------------------------------------
# Sub-scoring functions — all sector-aware via get_benchmarks(sector)
# ---------------------------------------------------------------------------


def _score_current_ratio(val: Optional[float], sector: str = GENERAL) -> tuple[float, str]:
    if val is None:
        return 0.0, "CURRENT_RATIO_MISSING"
    b = get_benchmarks(sector)["current_ratio"]
    if val >= b["strong"]:
        return 100.0, "CURRENT_RATIO_STRONG"
    if val >= b["adequate"]:
        return 80.0, "CURRENT_RATIO_ADEQUATE"
    if val >= b["marginal"]:
        return 50.0, "CURRENT_RATIO_MARGINAL"
    return 20.0, "CURRENT_RATIO_WEAK"


def _score_debt_to_equity(val: Optional[float], sector: str = GENERAL) -> tuple[float, str]:
    if val is None:
        return 0.0, "D_E_MISSING"
    b = get_benchmarks(sector)["debt_to_equity"]
    if val <= b["low"]:
        return 100.0, "D_E_LOW"
    if val <= b["moderate"]:
        return 70.0, "D_E_MODERATE"
    if val <= b["high"]:
        return 40.0, "D_E_HIGH"
    return 10.0, "D_E_VERY_HIGH"


def _score_cagr(val: Optional[float], sector: str = GENERAL) -> tuple[float, str]:
    if val is None:
        return 0.0, "CAGR_MISSING"
    b = get_benchmarks(sector)["cagr"]
    if val >= b["strong"]:
        return 100.0, "CAGR_STRONG"
    if val >= b["moderate"]:
        return 75.0, "CAGR_MODERATE"
    if val >= 0.0:
        return 50.0, "CAGR_FLAT"
    return 20.0, "CAGR_NEGATIVE"


def _score_quick_ratio(val: Optional[float], sector: str = GENERAL) -> tuple[float, str]:
    if val is None:
        return 0.0, "QUICK_RATIO_MISSING"
    b = get_benchmarks(sector)["quick_ratio"]
    if val >= b["strong"]:
        return 100.0, "QUICK_RATIO_STRONG"
    if val >= b["adequate"]:
        return 65.0, "QUICK_RATIO_ADEQUATE"
    return 30.0, "QUICK_RATIO_WEAK"


def _score_dso(val: Optional[float], sector: str = GENERAL) -> tuple[float, str]:
    if val is None:
        return 50.0, "DSO_MISSING"  # neutral score for missing data
    b = get_benchmarks(sector)["dso"]
    if val <= b["excellent"]:
        return 100.0, "DSO_EXCELLENT"
    if val <= b["good"]:
        return 75.0, "DSO_GOOD"
    if val <= b["high"]:
        return 40.0, "DSO_HIGH"
    return 10.0, "DSO_VERY_HIGH"


# ---------------------------------------------------------------------------
# Primary entry-point
# ---------------------------------------------------------------------------


def compute_financial_score(
    current_ratio: Optional[float],
    quick_ratio: Optional[float],
    debt_to_equity: Optional[float],
    net_revenue_cagr_5y: Optional[float],
    dso: Optional[float],
    working_capital: Optional[float],
    business_vintage_years: Optional[float],
    nic_code: Optional[str] = None,
) -> DomainScore:
    """
    Compute weighted financial domain score with sector-aware benchmarks.

    Parameters
    ----------
    current_ratio : float | None
    quick_ratio : float | None
    debt_to_equity : float | None
    net_revenue_cagr_5y : float | None
        5-year net revenue CAGR as a decimal (0.15 = 15%).
    dso : float | None
        Days Sales Outstanding.
    working_capital : float | None
        Absolute working capital value (INR). Sign matters.
    business_vintage_years : float | None
        Years since incorporation / GST registration.
    nic_code : str | None
        Raw NIC 2008 activity code from MCA/GST (e.g. '29254').
        Resolved to sector group via nic_mapper.resolve_sector().
        None or unrecognised codes fall back to GENERAL benchmarks.

    Returns
    -------
    DomainScore
        Weighted domain score with per-component breakdown and reason codes.
    """
    # --- Resolve sector group from NIC code ---
    sector = resolve_sector(nic_code)

    components = []

    # Current ratio (weight 20)
    s, r = _score_current_ratio(current_ratio, sector)
    components.append(
        ComponentScore(
            component_name="current_ratio",
            raw_value=current_ratio,
            normalized_score=s,
            weight=20.0,
            reason_code=r,
        )
    )

    # Quick ratio (weight 15)
    s, r = _score_quick_ratio(quick_ratio, sector)
    components.append(
        ComponentScore(
            component_name="quick_ratio",
            raw_value=quick_ratio,
            normalized_score=s,
            weight=15.0,
            reason_code=r,
        )
    )

    # Debt-to-equity (weight 20)
    s, r = _score_debt_to_equity(debt_to_equity, sector)
    components.append(
        ComponentScore(
            component_name="debt_to_equity",
            raw_value=debt_to_equity,
            normalized_score=s,
            weight=20.0,
            reason_code=r,
        )
    )

    # Revenue CAGR (weight 20)
    s, r = _score_cagr(net_revenue_cagr_5y, sector)
    components.append(
        ComponentScore(
            component_name="net_revenue_cagr_5y",
            raw_value=net_revenue_cagr_5y,
            normalized_score=s,
            weight=20.0,
            reason_code=r,
        )
    )

    # DSO (weight 10)
    s, r = _score_dso(dso, sector)
    components.append(
        ComponentScore(
            component_name="dso",
            raw_value=dso,
            normalized_score=s,
            weight=10.0,
            reason_code=r,
        )
    )

    # Working capital presence (weight 10)
    wc = working_capital
    wc_score = 100.0 if (wc is not None and wc > 0) else 30.0 if wc is not None else 0.0
    components.append(
        ComponentScore(
            component_name="working_capital",
            raw_value=wc,
            normalized_score=wc_score,
            weight=10.0,
            reason_code=("WORKING_CAPITAL_POSITIVE" if wc_score == 100 else "WORKING_CAPITAL_NEGATIVE_OR_MISSING"),
        )
    )

    # Business vintage (weight 5)
    v = business_vintage_years
    v_score = 100.0 if v and v >= 5 else 70.0 if v and v >= 3 else 40.0 if v and v >= 1 else 0.0
    components.append(
        ComponentScore(
            component_name="business_vintage",
            raw_value=v,
            normalized_score=v_score,
            weight=5.0,
            reason_code="VINTAGE_STRONG" if v_score == 100 else "VINTAGE_EARLY",
        )
    )

    return build_domain_score("financial", components)
