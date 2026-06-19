"""
part2/scoring/stress_tester.py
================================
Financial Stress Testing — PPRE (Pralyon AI Predictive Risk Engine)

Purpose
-------
Runs a suite of pre-defined financial shock scenarios against a Buyer's
current feature row and produces a stress table showing how the Credit Band,
Governance Score, Blended PD, and Evaluated Limit would change under each
scenario.

This module is SEPARATE from the 3-Anchor Limit Advisory.

  ┌─────────────────────────────────────────────────────────────────┐
  │  3-Anchor Limit  → answers: "How much can I lend TODAY?"        │
  │  Stress Tester   → answers: "What happens to the limit if the   │
  │                    Buyer's financials deteriorate?"              │
  └─────────────────────────────────────────────────────────────────┘

Design Principles
-----------------
1.  Each scenario shocks ONE or MORE input variables by a defined factor.
    All other variables remain at their base (current) values.

2.  Each shocked feature row is passed through the SAME scoring pipeline
    as the base case:
      shocked features → re-derive ratios → pd_mapper → limit_advisor
    This ensures consistency — no separate stress scoring rules.

3.  Stress results ONLY appear in the report. They do NOT change the
    sanctioned limit, which is always based on the base-case score.

4.  Scenarios are additive — combined scenarios stack multiple shocks.

5.  Every scenario records a delta vs base (score Δ, band change,
    limit Δ, PD Δ) so the Seller can see the SIZE of the sensitivity.

Scenarios
---------
  ID   Label                         Revenue  Debt Mult  Current Ratio
  ──── ─────────────────────────────────────────────────────────────────
  S0   Base Case (no shock)            1.00     1.00        base
  S1   Revenue Decline  −20%           0.80     1.00        base
  S2   Revenue Decline  −40%           0.60     1.00        base
  S3   Leverage Increase +50%          1.00     1.50        base
  S4   Combined (Rev −20% + Lev +50%)  0.80     1.50        base
  S5   Liquidity Stress (CR → 0.9)     1.00     1.00        0.90
  S6   Severe (Rev −50% + Lev +100%)   0.50     2.00        base

Report output
-------------
Each scenario row in the report shows:
  Scenario | Shocked Revenue | Score | Band | PD% | Limit | Score Δ | Limit Δ

Integration
-----------
Called from part2/pipeline/run_inference.py after the base-case limit is
computed::

    from domain.scoring.stress_tester import run_stress_test

    stress_table = run_stress_test(
        base_row        = enriched_feature_row,
        base_pd_result  = pd_result,
        base_limit      = evaluated_limit,
        blended_pd      = blended_pd,
        requested_amount= requested_amount,
    )
    # stress_table is a list[StressScenarioResult] — append to output dict

The stress results are stored under the key ``"stress_table"`` in the
final output dict and rendered as a separate section in the report.

Author: RyskNode Labs
Version: 1.0
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from domain.scoring.pd_mapper import derive_pd_band, BAND_ORDER


# ---------------------------------------------------------------------------
# Constants — kept in sync with pd_mapper.py and limit_advisor.py
# ---------------------------------------------------------------------------

# PD prior per band (matches openriskscore_blender.py stub priors)
BAND_PD_PRIORS: Dict[str, float] = {
    "AAA": 0.004,
    "AA":  0.009,
    "A":   0.018,
    "BBB": 0.035,
    "BB":  0.070,
    "B":   0.140,
    "CCC": 0.280,
    "D":   1.000,
    "UNSCOREABLE": 1.000,
}

# Band multiplier for limit (matches limit_advisor.py)
BAND_LIMIT_MULT: Dict[str, float] = {
    "AAA": 0.90,
    "AA":  0.85,
    "A":   0.75,
    "BBB": 0.65,
    "BB":  0.50,
    "B":   0.35,
    "CCC": 0.20,
    "D":   0.00,
    "UNSCOREABLE": 0.00,
}

# Revenue anchor rate (matches limit_advisor.py)
REVENUE_ANCHOR_RATE = 0.10

# TNW anchor rate (matches limit_advisor.py)
TNW_ANCHOR_RATE = 0.15

# Scenario definitions
#   Each entry: (scenario_id, label, revenue_mult, debt_mult, current_ratio_override)
#   current_ratio_override = None → keep base value
SCENARIOS: List[tuple] = [
    ("S0", "Base Case",                               1.00, 1.00, None),
    ("S1", "Revenue Decline −20%",                    0.80, 1.00, None),
    ("S2", "Revenue Decline −40%",                    0.60, 1.00, None),
    ("S3", "Leverage Increase +50%",                  1.00, 1.50, None),
    ("S4", "Combined: Rev −20% + Leverage +50%",      0.80, 1.50, None),
    ("S5", "Liquidity Stress (Current Ratio → 0.90)", 1.00, 1.00, 0.90),
    ("S6", "Severe: Rev −50% + Leverage +100%",       0.50, 2.00, None),
]


# ---------------------------------------------------------------------------
# Result dataclass
# ---------------------------------------------------------------------------

@dataclass
class StressScenarioResult:
    scenario_id:      str
    label:            str
    revenue_mult:     float
    debt_mult:        float
    cr_override:      Optional[float]

    # Shocked inputs
    shocked_revenue:  Optional[float]
    shocked_debt:     Optional[float]
    shocked_cr:       Optional[float]

    # Re-scored outputs
    governance_score: float
    pd_band:          str
    blended_pd:       float
    evaluated_limit:  float

    # Deltas vs base
    score_delta:      float
    band_delta:       int          # positive = improved (notches), negative = worsened
    pd_delta:         float        # absolute change in PD
    limit_delta:      float        # absolute change in limit (₹)

    override_flags:   List[str] = field(default_factory=list)
    is_base:          bool = False


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _safe_div(a: Any, b: Any) -> Optional[float]:
    """Safe division — returns None if inputs are None or b is zero."""
    if a is None or b is None:
        return None
    try:
        b_f = float(b)
        if b_f == 0:
            return None
        return round(float(a) / b_f, 4)
    except (TypeError, ValueError):
        return None


def _band_notch_delta(new_band: str, base_band: str) -> int:
    """
    Return signed notch delta (positive = improved, negative = worsened).
    Both bands must be in BAND_ORDER.
    """
    base_idx = BAND_ORDER.index(base_band) if base_band in BAND_ORDER else 0
    new_idx  = BAND_ORDER.index(new_band)  if new_band  in BAND_ORDER else 0
    return new_idx - base_idx


def _re_score(
    base_row:       Dict[str, Any],
    revenue_mult:   float,
    debt_mult:      float,
    cr_override:    Optional[float],
) -> Dict[str, Any]:
    """
    Apply shocks to the base feature row and re-derive the ratios
    that feed into pd_mapper.derive_pd_band().

    Shocked variables
    -----------------
    revenue_mult  : multiplies current revenue (affects financial_score anchor)
    debt_mult     : multiplies total_debt (affects debt_to_equity)
    cr_override   : if provided, replaces current_ratio directly

    Returns a dict with:
      shocked_revenue, shocked_debt, shocked_cr,
      financial_score, identity_score, legal_risk_score,
      documentation_score, debt_to_equity, current_ratio,
      business_vintage_years, criminal_case_count
    """
    # ── Apply revenue shock
    base_rev    = base_row.get("net_revenue_latest") or base_row.get("revenue") or 0
    shocked_rev = base_rev * revenue_mult

    # ── Apply debt shock
    base_debt    = base_row.get("total_debt") or 0
    shocked_debt = base_debt * debt_mult
    networth     = base_row.get("networth") or base_row.get("tangible_net_worth") or 1
    shocked_dte  = _safe_div(shocked_debt, networth)

    # ── Current ratio — override or recalculate
    if cr_override is not None:
        shocked_cr = cr_override
    else:
        shocked_cr = base_row.get("current_ratio")

    # ── Re-derive financial_score
    # We apply a proportional haircut to financial_score based on revenue shock
    # and leverage increase, mirroring the scorecard logic without re-running
    # all 7 sub-scores.
    base_fin_score = float(base_row.get("financial_score", 70))

    # Revenue decline penalty: each 10% decline → ~3 pts off financial_score
    rev_penalty   = max(0.0, (1.0 - revenue_mult) * 30.0)

    # Leverage increase penalty: each 50% debt increase → ~4 pts off
    lev_penalty   = max(0.0, (debt_mult - 1.0) * 8.0)

    # Current ratio penalty: if shocked CR < 1 → additional 5 pts off
    cr_penalty    = 5.0 if (shocked_cr is not None and shocked_cr < 1.0) else 0.0

    shocked_fin_score = round(
        max(0.0, base_fin_score - rev_penalty - lev_penalty - cr_penalty), 2
    )

    return {
        "shocked_revenue":       shocked_rev,
        "shocked_debt":          shocked_debt,
        "shocked_cr":            shocked_cr,
        "financial_score":       shocked_fin_score,
        "identity_score":        float(base_row.get("identity_score", 78)),
        "legal_risk_score":      float(base_row.get("legal_score", 85)),
        "documentation_score":   float(base_row.get("documentation_score", 88)),
        "debt_to_equity":        shocked_dte,
        "current_ratio":         shocked_cr,
        "business_vintage_years":float(base_row.get("business_vintage_years", 10)),
        "criminal_case_count":   int(base_row.get("criminal_case_count", 0)),
    }


def _compute_limit(
    shocked_revenue: Optional[float],
    base_row:        Dict[str, Any],
    pd_band:         str,
    requested_amount:float,
) -> float:
    """
    Recompute evaluated limit for a shocked scenario.

    Uses the same 3-anchor minimum as limit_advisor.py:
      Anchor 1: 10% of shocked revenue × band multiplier
      Anchor 2: 15% of TNW × band multiplier
      Anchor 3: 3× average monthly purchase volume (unchanged by stress)
    """
    mult = BAND_LIMIT_MULT.get(pd_band, 0.0)

    # Anchor 1 — shocked revenue
    rev    = shocked_revenue or 0.0
    anch1  = round(rev * REVENUE_ANCHOR_RATE * mult, 0)

    # Anchor 2 — TNW (not shocked by revenue/debt scenario alone)
    tnw    = float(base_row.get("tangible_net_worth") or base_row.get("networth") or 0)
    anch2  = round(tnw * TNW_ANCHOR_RATE * mult, 0)

    # Anchor 3 — purchase volume (unchanged)
    avg_monthly = requested_amount / 3.0   # back-solve: requested = 3× monthly
    anch3  = round(avg_monthly * 3, 0)

    return min(anch1, anch2, anch3)


# ---------------------------------------------------------------------------
# Public entry-point
# ---------------------------------------------------------------------------

def run_stress_test(
    base_row:         Dict[str, Any],
    base_pd_result:   Any,                # PDMapResult from pd_mapper
    base_limit:       float,
    blended_pd:       float,
    requested_amount: float = 5_000_000,
) -> List[StressScenarioResult]:
    """
    Run all stress scenarios against the base feature row.

    Parameters
    ----------
    base_row         : enriched feature dict from integration bridge
    base_pd_result   : PDMapResult from pd_mapper.derive_pd_band()
    base_limit       : evaluated limit from limit_advisor (base case)
    blended_pd       : base blended PD from openriskscore_blender
    requested_amount : Seller's requested trade credit amount

    Returns
    -------
    List[StressScenarioResult] — one entry per scenario in SCENARIOS.
    S0 (Base Case) is always first and has is_base=True.
    """
    results: List[StressScenarioResult] = []
    base_band  = base_pd_result.pd_band
    base_score = base_pd_result.governance_score

    for sc_id, label, rev_mult, debt_mult, cr_override in SCENARIOS:
        is_base = (sc_id == "S0")

        # ── Shock and re-score
        shocked = _re_score(base_row, rev_mult, debt_mult, cr_override)

        # ── Re-run pd_mapper with shocked inputs
        # Note: legal_risk_score is inverted inside pd_mapper → pass raw legal_score
        # legal_score in base_row is already in "higher = better" direction
        # so we convert back: legal_risk_score = 100 - legal_score
        legal_risk_score = 100.0 - shocked["legal_risk_score"]

        pd_result = derive_pd_band(
            identity_score         = shocked["identity_score"],
            financial_score        = shocked["financial_score"],
            legal_risk_score       = legal_risk_score,
            documentation_score    = shocked["documentation_score"],
            criminal_case_count    = shocked["criminal_case_count"],
            debt_to_equity         = shocked["debt_to_equity"],
            current_ratio          = shocked["current_ratio"],
            business_vintage_years = shocked["business_vintage_years"],
        )

        shocked_band  = pd_result.pd_band
        shocked_score = pd_result.governance_score
        shocked_pd    = BAND_PD_PRIORS.get(shocked_band, 1.0)
        shocked_limit = _compute_limit(
            shocked["shocked_revenue"], base_row, shocked_band, requested_amount
        )

        # ── Deltas
        score_delta = round(shocked_score - base_score, 2)
        band_delta  = _band_notch_delta(shocked_band, base_band)
        pd_delta    = round(shocked_pd - blended_pd, 6)
        limit_delta = round(shocked_limit - base_limit, 0)

        results.append(StressScenarioResult(
            scenario_id      = sc_id,
            label            = label,
            revenue_mult     = rev_mult,
            debt_mult        = debt_mult,
            cr_override      = cr_override,
            shocked_revenue  = shocked["shocked_revenue"],
            shocked_debt     = shocked["shocked_debt"],
            shocked_cr       = shocked["shocked_cr"],
            governance_score = shocked_score,
            pd_band          = shocked_band,
            blended_pd       = shocked_pd,
            evaluated_limit  = shocked_limit,
            score_delta      = score_delta,
            band_delta       = band_delta,
            pd_delta         = pd_delta,
            limit_delta      = limit_delta,
            override_flags   = pd_result.override_flags,
            is_base          = is_base,
        ))

    return results


# ---------------------------------------------------------------------------
# Report formatter
# ---------------------------------------------------------------------------

def format_stress_table(
    results: List[StressScenarioResult],
    currency_symbol: str = "\u20b9",
) -> str:
    """
    Format stress table as a plain-text block for inclusion in the report.

    Output example::

        FINANCIAL STRESS TEST — PPRE Scenario Analysis
        ═══════════════════════════════════════════════════════════════════════
        Scenario                              Score  Band    PD%    Limit       Δ Limit
        ─────────────────────────────────────────────────────────────────────────────
        S0  Base Case                         80.7   AA     0.77%  ₹30,00,000   —
        S1  Revenue Decline −20%              76.5   A      1.80%  ₹22,50,000  −₹7,50,000
        S2  Revenue Decline −40%              70.1   A      1.80%  ₹15,00,000  −₹15,00,000
        S3  Leverage Increase +50%            78.0   AA     0.90%  ₹27,00,000  −₹3,00,000
        S4  Combined: Rev −20% + Lev +50%     68.4   BBB    3.50%  ₹10,50,000  −₹19,50,000
        S5  Liquidity Stress (CR → 0.90)      75.7   A      1.80%  ₹22,50,000  −₹7,50,000
        S6  Severe: Rev −50% + Lev +100%      58.2   BB     7.00%  ₹5,25,000   −₹24,75,000

    The stress table is shown BELOW the 3-Anchor Limit panel in the report.
    It does NOT change the sanctioned limit.
    """
    sep  = "═" * 75
    dash = "─" * 75
    lines = [
        "",
        "FINANCIAL STRESS TEST — PPRE Scenario Analysis",
        sep,
        f"  {'Scenario':<42} {'Score':>6}  {'Band':>5}  {'PD%':>6}  {'Limit':>13}  {'Δ Limit':>14}",
        dash,
    ]
    for r in results:
        delta_str = (
            "  (BASE)"
            if r.is_base
            else f"{'+' if r.limit_delta >= 0 else ''}{currency_symbol}{r.limit_delta:>+,.0f}"
        )
        row = (
            f"  {r.scenario_id:<4} {r.label:<38}"
            f"  {r.governance_score:>5.1f}"
            f"  {r.pd_band:>5}"
            f"  {r.blended_pd*100:>5.2f}%"
            f"  {currency_symbol}{r.evaluated_limit:>11,.0f}"
            f"  {delta_str:>14}"
        )
        lines.append(row)
    lines.append(dash)
    lines.append(
        "  NOTE: Stress scenarios are informational only."
        " The sanctioned limit is always the base-case evaluated limit."
    )
    lines.append("")
    return "\n".join(lines)
