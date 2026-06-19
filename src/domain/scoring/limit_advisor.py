"""
part2/scoring/limit_advisor.py
==============================
Limit Advisor — Safe credit exposure limit for Seller on this Buyer.

Context
-------
RyskNode is a counterparty risk platform.
The SELLER (MSME) submits a BUYER's identifiers to RyskNode.
This module answers two questions:

  1. How much trade credit can the Seller safely extend to this Buyer
     at the Seller's requested tenor?

  2. If the request EXCEEDS the evaluated safe limit at the Seller's
     preferred tenor, what is the shortest tenor at which the full
     request sits within the evaluated limit? And if no tenor clears
     it, how much advance is suggested before dispatch?

Seller inputs (at query time)
-----------------------------
  buyer_gstin / buyer_cin               Required — triggers Part 1 API pulls
  buyer_company_name                    Required — entity resolution
  expected_monthly_purchase_volume      Optional — Seller's expected monthly
                                        sales TO this Buyer (INR). Enables
                                        Anchor 3 (purchase volume anchor).
  credit_period_days                    Optional — Seller's preferred tenor.
                                        Default: 30 days (net-30 trade terms).
  buyer_email                           Optional — notification only, no scoring.

Pipeline-injected inputs (derived by Part 1 — never collected from Seller)
--------------------------------------------------------------------------
  net_revenue_latest      <- GST filings
  tangible_net_worth      <- MCA balance sheet (equity - intangibles)
  turnover_cagr_5y        <- GST multi-year CAGR
  turnover_y1/y2/y3       <- GST annual figures
  pd_band                 <- derived by pd_mapper.derive_pd_band() — NEVER manual
  data_penalty            <- derived by pd_mapper.derive_pd_band() (1.0 or 0.80)

Three-anchor limit logic
------------------------
  Anchor 1 (always):   Buyer annual revenue x BAND_LIMIT_PCT
  Anchor 2 (if TNW):   Buyer TNW x TNW_LIMIT_PCT
  Anchor 3 (if vol):   avg_monthly_purchase_volume x TENOR_MULTIPLIER[tenor]

                       NOTE: BILLING_CYCLES is intentionally NOT multiplied
                       into Anchor 3. It is retained as an audit-only
                       reference constant showing invoices-in-flight context.
                       Anchor 3 uses TENOR_MULTIPLIER only, so the purchase
                       volume anchor decreases monotonically as tenor increases.

  evaluated_limit = MIN(all available anchors)
                    x TENOR_MULTIPLIER[tenor_bucket]
                    x data_penalty          (1.0 or 0.80 from pd_mapper)

  Whichever anchor is lowest wins — most conservative safe exposure.
  If only Anchor 1 is available, the system falls back to single-anchor mode.

Tenor recommendation logic
--------------------------
  WITHIN_LIMIT    -> recommended_tenor_days = credit_period_days.
  EXCEEDS_ADVISED -> Tenor sweep fires (15 -> 150d).
                    Shortest tenor where evaluated_limit >= requested_amount.
                    If none clears -> advance collection suggested.
  NOT_REQUESTED   -> tenor_schedule shown for reference only.

Tenor Advisory Ladder (v1.1)
-----------------------------
  Every row in tenor_schedule now carries a tenor_remark field classifying
  each tenor bucket into one of four advisory labels:

    RECOMMENDED     — clears request with >=25% surplus headroom
                      (if no request: limit has dropped <=20% from 15d baseline)
    MODERATE        — clears request with 10–25% surplus headroom
                      (if no request: limit dropped 21–40% from 15d baseline)
    WITH_CAUTION    — clears request with <10% surplus headroom
                      (if no request: limit dropped 41–60% from 15d baseline)
    NOT_RECOMMENDED — does not clear request, or limit is zero
                      (if no request: limit dropped >60% from 15d baseline)

  The classification is purely relative to each tenor's own evaluated_limit
  versus the requested amount (or 15d baseline when no request is given).
  Labels become progressively more cautious as tenor increases, unless the
  buyer's profile is strong enough to remain comfortably within limit.

  tenor_best_evaluated_days is added to the top-level result: the shortest
  tenor labelled RECOMMENDED, or MODERATE if none qualify as RECOMMENDED.

Advance-against-shortfall
--------------------------
  Only when terms_vs_profile == "exceeds_advised" AND no tenor bucket clears.
    advance_to_collect = requested_amount - best_achievable_clean_limit (15d)

Tenor range
-----------
  15 -> 150 days (trade credit cycles only — not loan tenors).
  Default: 30 days.

All monetary values in INR.

IMPORTANT — Legal-safe output
------------------------------
All user-facing strings use evaluation language only:
  OK  'evaluation indicates' / 'evaluation suggests'
  OK  'the Seller may consider'
  OK  'it is recommended that the Seller consider'
  NO  'approved' / 'rejected' / 'sanctioned' / 'declined'

Field naming convention — legal-safe keys:
  evaluated_limit           (not 'approved_limit')
  evaluated_clean_limit     (not 'approved_clean_credit')
  advised_limit             (not 'sanctioned_limit')
  tenor_recommendation_note (not 'decision')
"""

from __future__ import annotations

import logging
import statistics
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Configuration tables
# ---------------------------------------------------------------------------

BAND_LIMIT_PCT: Dict[str, float] = {
    "AAA": 0.35,
    "AA":  0.28,
    "A":   0.20,
    "BBB": 0.12,
    "BB":  0.06,
    "B":   0.03,
    "CCC": 0.00,
    "D":   0.00,
}

TNW_LIMIT_PCT: Dict[str, float] = {
    "AAA": 0.25,
    "AA":  0.20,
    "A":   0.15,
    "BBB": 0.10,
    "BB":  0.08,
    "B":   0.05,
    "CCC": 0.00,
    "D":   0.00,
}

# BILLING_CYCLES — AUDIT/REFERENCE ONLY.
# Shows how many invoice cycles are outstanding at each tenor.
# NOT used in any limit computation. Retained for UI display and audit trail.
BILLING_CYCLES: Dict[int, float] = {
    15:  0.5,
    30:  1.0,
    45:  1.5,
    60:  2.0,
    75:  2.5,
    90:  3.0,
    120: 4.0,
    150: 5.0,
}

# Tenor multipliers — longer tenor = more risk = lower safe exposure.
# Applied to MIN(anchors) at Step 3.
TENOR_MULTIPLIER: Dict[int, float] = {
    15:  1.00,
    30:  0.95,
    45:  0.88,
    60:  0.80,
    75:  0.72,
    90:  0.65,
    120: 0.55,
    150: 0.45,
}

DECLINING_TURNOVER_HAIRCUT = 0.75
VOLATILITY_HAIRCUT         = 0.85
VOLATILITY_CV_THRESHOLD    = 0.30

# ---------------------------------------------------------------------------
# Tenor advisory remark labels + classification thresholds
# ---------------------------------------------------------------------------

TENOR_REMARK_LABELS = {
    "RECOMMENDED":     "Recommended",
    "MODERATE":        "Moderate",
    "WITH_CAUTION":    "With caution",
    "NOT_RECOMMENDED": "Not recommended",
}

_HEADROOM_RECOMMENDED  = 0.25
_HEADROOM_MODERATE     = 0.10

_DROP_RECOMMENDED  = 0.20
_DROP_MODERATE     = 0.40
_DROP_CAUTION      = 0.60


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _classify_tenor_remark(
    evaluated_limit:  float,
    requested_amount: Optional[float],
    limit_at_15d:     Optional[float],
) -> str:
    """
    Classify a single tenor bucket into one of four advisory labels.

    Mode A — request provided: surplus headroom vs requested_amount.
    Mode B — no request: drop from 15d baseline.
    Returns: RECOMMENDED | MODERATE | WITH_CAUTION | NOT_RECOMMENDED
    """
    if evaluated_limit <= 0:
        return "NOT_RECOMMENDED"

    if requested_amount is not None:
        req = float(requested_amount)
        if req <= 0:
            return "RECOMMENDED"
        surplus_pct = (evaluated_limit - req) / req
        if surplus_pct >= _HEADROOM_RECOMMENDED:
            return "RECOMMENDED"
        elif surplus_pct >= _HEADROOM_MODERATE:
            return "MODERATE"
        elif surplus_pct >= 0:
            return "WITH_CAUTION"
        else:
            return "NOT_RECOMMENDED"
    else:
        if limit_at_15d is None or limit_at_15d <= 0:
            return "WITH_CAUTION"
        drop_pct = (limit_at_15d - evaluated_limit) / limit_at_15d
        if drop_pct <= _DROP_RECOMMENDED:
            return "RECOMMENDED"
        elif drop_pct <= _DROP_MODERATE:
            return "MODERATE"
        elif drop_pct <= _DROP_CAUTION:
            return "WITH_CAUTION"
        else:
            return "NOT_RECOMMENDED"


def _tenor_bucket(requested_tenor_days: int) -> int:
    """Snap requested tenor to nearest configured trade-credit bucket (15-150d)."""
    buckets = sorted(TENOR_MULTIPLIER.keys())
    for b in buckets:
        if requested_tenor_days <= b:
            return b
    return buckets[-1]


def _compute_anchors(
    net_revenue_latest:          float,
    pd_band:                     str,
    tangible_net_worth:          Optional[float],
    avg_monthly_purchase_volume: Optional[float],
    bucket:                      int,
) -> Tuple[float, Optional[float], Optional[float]]:
    """
    Compute all three anchors for a given tenor bucket.
    Returns (anchor_revenue, anchor_tnw_or_None, anchor_purchase_or_None).
    """
    band_pct = BAND_LIMIT_PCT.get(pd_band, 0.0)
    tnw_pct  = TNW_LIMIT_PCT.get(pd_band, 0.0)

    anchor_revenue = float(net_revenue_latest or 0) * band_pct

    anchor_tnw: Optional[float] = None
    if tangible_net_worth is not None and float(tangible_net_worth) > 0:
        anchor_tnw = float(tangible_net_worth) * tnw_pct

    anchor_purchase: Optional[float] = None
    if avg_monthly_purchase_volume is not None and float(avg_monthly_purchase_volume) > 0:
        anchor_purchase = float(avg_monthly_purchase_volume)

    return anchor_revenue, anchor_tnw, anchor_purchase


def _apply_haircuts(
    base: float,
    turnover_cagr_5y: Optional[float],
    turnover_y1: Optional[float],
    turnover_y2: Optional[float],
    turnover_y3: Optional[float],
    data_penalty: float = 1.0,
) -> Tuple[float, bool, bool]:
    """
    Apply declining-CAGR, volatility, and data-penalty haircuts.
    Returns (adjusted_limit, haircut_applied, volatility_haircut).
    """
    haircut_applied    = False
    volatility_haircut = False
    adjusted           = base

    if turnover_cagr_5y is not None and float(turnover_cagr_5y) < 0:
        adjusted        *= DECLINING_TURNOVER_HAIRCUT
        haircut_applied  = True

    t_vals = [v for v in [turnover_y1, turnover_y2, turnover_y3] if v is not None]
    if len(t_vals) >= 2:
        mean_t  = statistics.mean(t_vals)
        stdev_t = statistics.stdev(t_vals)
        cv      = stdev_t / mean_t if mean_t > 0 else 0
        if cv > VOLATILITY_CV_THRESHOLD:
            adjusted           *= VOLATILITY_HAIRCUT
            volatility_haircut  = True

    adjusted *= data_penalty

    return round(adjusted, 2), haircut_applied, volatility_haircut


def _advised_at_bucket(
    bucket:                      int,
    net_revenue_latest:          float,
    pd_band:                     str,
    tangible_net_worth:          Optional[float],
    avg_monthly_purchase_volume: Optional[float],
    turnover_cagr_5y:            Optional[float],
    turnover_y1:                 Optional[float],
    turnover_y2:                 Optional[float],
    turnover_y3:                 Optional[float],
    data_penalty:                float = 1.0,
) -> float:
    """Compute evaluated_limit for a specific tenor bucket."""
    a_rev, a_tnw, a_purch = _compute_anchors(
        net_revenue_latest, pd_band, tangible_net_worth,
        avg_monthly_purchase_volume, bucket,
    )
    candidates = {"revenue": a_rev}
    if a_tnw   is not None: candidates["tnw"]             = a_tnw
    if a_purch is not None: candidates["purchase_volume"] = a_purch

    base       = min(candidates.values())
    tenor_mult = TENOR_MULTIPLIER[bucket]
    adjusted, _, _ = _apply_haircuts(
        base * tenor_mult,
        turnover_cagr_5y, turnover_y1, turnover_y2, turnover_y3,
        data_penalty=data_penalty,
    )
    return adjusted


# ---------------------------------------------------------------------------
# Primary entry-point
# ---------------------------------------------------------------------------

def advise_limit(
    # Pipeline-injected from Part 1 FinalFeatureRow + pd_mapper
    net_revenue_latest:              float,
    pd_band:                         str,
    tangible_net_worth:              Optional[float] = None,
    turnover_cagr_5y:                Optional[float] = None,
    turnover_y1:                     Optional[float] = None,
    turnover_y2:                     Optional[float] = None,
    turnover_y3:                     Optional[float] = None,
    data_penalty:                    float           = 1.0,
    # Seller-provided at query time
    credit_period_days:              int             = 30,
    requested_amount:                Optional[float] = None,
    avg_monthly_purchase_volume:     Optional[float] = None,
    # XAI integration
    buyer_id:                        str             = "UNKNOWN",
    blended_pd:                      float           = 0.0,
    x_instance:                      Any             = None,
    explainer:                       Any             = None,
) -> dict:
    """
    Compute safe credit exposure limit for Seller on this Buyer.

    Returns a dict. Key field naming follows legal-safe convention:
      evaluated_clean_limit  (formerly approved_clean_credit — renamed v1.2)
      advised_limit
      tenor_recommendation_note
    """

    # -------------------------------------------------------------------------
    # Guard: UNSCOREABLE band
    # -------------------------------------------------------------------------
    if pd_band == "UNSCOREABLE":
        return {
            "advised_limit":              0.0,
            "base_limit":                 0.0,
            "binding_anchor":             None,
            "all_anchors":                {},
            "tenor_multiplier":           None,
            "tenor_bucket_days":          None,
            "haircut_applied":            False,
            "volatility_haircut":         False,
            "terms_vs_profile":           "unscoreable",
            "requested_amount":           requested_amount,
            "credit_period_days":         int(credit_period_days or 30),
            "explanation":                None,
            "recommended_tenor_days":     None,
            "tenor_recommendation_note":  "Entity could not be reliably identified. No credit limit evaluated.",
            "evaluated_clean_limit":      0.0,
            "advance_required":           float(requested_amount or 0),
            "advance_pct_of_request":     100.0 if requested_amount else 0.0,
            "advance_recommendation":     None,
            "tenor_schedule":             [],
            "tenor_best_evaluated_days":  None,
        }

    # -------------------------------------------------------------------------
    # Step 1: Evaluate at Seller's preferred tenor
    # -------------------------------------------------------------------------
    bucket     = _tenor_bucket(int(credit_period_days or 30))
    tenor_mult = TENOR_MULTIPLIER[bucket]

    anchor_revenue, anchor_tnw, anchor_purchase = _compute_anchors(
        net_revenue_latest, pd_band, tangible_net_worth,
        avg_monthly_purchase_volume, bucket,
    )

    # -------------------------------------------------------------------------
    # Step 2: Binding anchor
    # -------------------------------------------------------------------------
    anchor_candidates = {
        "revenue":         anchor_revenue,
        "tnw":             anchor_tnw,
        "purchase_volume": anchor_purchase,
    }
    active_anchors = {k: v for k, v in anchor_candidates.items() if v is not None}
    binding_anchor = min(active_anchors, key=lambda k: active_anchors[k])
    base_limit     = active_anchors[binding_anchor]

    # -------------------------------------------------------------------------
    # Step 3: Tenor multiplier + haircuts
    # -------------------------------------------------------------------------
    pre_haircut = base_limit * tenor_mult
    advised_limit, haircut_applied, volatility_haircut = _apply_haircuts(
        pre_haircut,
        turnover_cagr_5y, turnover_y1, turnover_y2, turnover_y3,
        data_penalty=data_penalty,
    )

    # -------------------------------------------------------------------------
    # Step 4: Terms vs profile
    # -------------------------------------------------------------------------
    if requested_amount is None:
        terms_vs_profile = "not_requested"
    elif float(requested_amount) <= advised_limit:
        terms_vs_profile = "within_limit"
    else:
        terms_vs_profile = "exceeds_advised"

    # -------------------------------------------------------------------------
    # Step 5: Tenor schedule — all 8 buckets with advisory remarks
    # -------------------------------------------------------------------------
    limit_at_15d: float = _advised_at_bucket(
        15,
        net_revenue_latest, pd_band, tangible_net_worth,
        avg_monthly_purchase_volume,
        turnover_cagr_5y, turnover_y1, turnover_y2, turnover_y3,
        data_penalty=data_penalty,
    )

    tenor_schedule: List[Dict] = []
    for t_bucket in sorted(TENOR_MULTIPLIER.keys()):
        lim = _advised_at_bucket(
            t_bucket,
            net_revenue_latest, pd_band, tangible_net_worth,
            avg_monthly_purchase_volume,
            turnover_cagr_5y, turnover_y1, turnover_y2, turnover_y3,
            data_penalty=data_penalty,
        )
        clears = (requested_amount is not None) and (lim >= float(requested_amount))
        adv    = round(max(0.0, float(requested_amount or 0) - lim), 2)

        remark_key   = _classify_tenor_remark(lim, requested_amount, limit_at_15d)
        remark_label = TENOR_REMARK_LABELS[remark_key]

        tenor_schedule.append({
            "tenor_days":           t_bucket,
            "advised_limit":        lim,
            "billing_cycles_ref":   BILLING_CYCLES[t_bucket],
            "clears_request":       clears,
            "advance_to_collect":   adv,
            "tenor_remark":         remark_key,
            "tenor_remark_label":   remark_label,
        })

    # -------------------------------------------------------------------------
    # Step 5b: Best evaluated tenor
    # -------------------------------------------------------------------------
    tenor_best_evaluated_days: Optional[int] = None
    for preferred_remark in ("RECOMMENDED", "MODERATE"):
        for row in tenor_schedule:
            if row["tenor_remark"] == preferred_remark:
                tenor_best_evaluated_days = row["tenor_days"]
                break
        if tenor_best_evaluated_days is not None:
            break

    # -------------------------------------------------------------------------
    # Step 6: Evaluated clean limit
    # Formerly 'approved_clean_credit' — renamed to evaluated_clean_limit (v1.2)
    # -------------------------------------------------------------------------
    recommended_tenor_days:  Optional[int] = None
    evaluated_clean_limit:   float         = advised_limit

    if terms_vs_profile == "within_limit":
        recommended_tenor_days = bucket
        evaluated_clean_limit  = advised_limit

    elif terms_vs_profile == "exceeds_advised":
        for row in tenor_schedule:
            if row["clears_request"]:
                recommended_tenor_days = row["tenor_days"]
                evaluated_clean_limit  = row["advised_limit"]
                break
        if recommended_tenor_days is None:
            evaluated_clean_limit = tenor_schedule[0]["advised_limit"]

    # -------------------------------------------------------------------------
    # Step 7: Advance + recommendation notes
    # -------------------------------------------------------------------------
    advance_required       = 0.0
    advance_pct_of_request = 0.0
    advance_recommendation: Optional[str] = None
    tenor_recommendation_note: str        = ""

    if requested_amount is not None:
        req = float(requested_amount)

        if terms_vs_profile == "within_limit":
            tenor_recommendation_note = (
                f"The evaluation indicates the requested \u20b9{req/1e5:.2f}L sits within "
                f"the evaluated safe limit of \u20b9{advised_limit/1e5:.2f}L at {bucket}-day terms. "
                f"The Seller may consider extending credit under these terms at their sole discretion. "
                f"No advance collection is suggested."
            )

        elif terms_vs_profile == "exceeds_advised":
            if recommended_tenor_days is not None:
                advance_required = 0.0
                if recommended_tenor_days < bucket:
                    tenor_recommendation_note = (
                        f"The evaluation indicates the requested \u20b9{req/1e5:.2f}L exceeds the "
                        f"evaluated safe limit of \u20b9{advised_limit/1e5:.2f}L at the preferred "
                        f"{bucket}-day terms. Evaluation suggests tightening to "
                        f"{recommended_tenor_days}-day terms — at that tenor the evaluated "
                        f"safe limit is \u20b9{evaluated_clean_limit/1e5:.2f}L, which covers the full "
                        f"request. The Seller may consider extending credit at "
                        f"{recommended_tenor_days}-day terms at their sole discretion. "
                        f"No advance collection is suggested at that tenor."
                    )
                else:
                    tenor_recommendation_note = (
                        f"The evaluation indicates the requested \u20b9{req/1e5:.2f}L sits within "
                        f"the evaluated safe limit at {recommended_tenor_days}-day terms "
                        f"(evaluated limit: \u20b9{evaluated_clean_limit/1e5:.2f}L). "
                        f"The Seller may consider extending credit at their sole discretion. "
                        f"No advance collection is suggested."
                    )
            else:
                advance_required       = round(req - evaluated_clean_limit, 2)
                advance_pct_of_request = round(advance_required / req * 100, 2)
                advance_recommendation = (
                    f"The evaluation indicates a maximum evaluated safe limit of "
                    f"\u20b9{evaluated_clean_limit/1e5:.2f}L on 15-day terms. "
                    f"It is recommended that the Seller consider extending "
                    f"\u20b9{evaluated_clean_limit/1e5:.2f}L as clean trade credit "
                    f"and collecting \u20b9{advance_required/1e5:.2f}L "
                    f"({advance_pct_of_request:.1f}% of order value) as an advance "
                    f"payment, post-dated cheque, or security deposit from the Buyer "
                    f"prior to dispatch. The Seller retains full discretion. "
                    f"Total coverage: \u20b9{req/1e5:.2f}L."
                )
                tenor_recommendation_note = (
                    f"The evaluation indicates the requested \u20b9{req/1e5:.2f}L exceeds the "
                    f"evaluated safe capacity at all tenor buckets "
                    f"(maximum evaluated limit: \u20b9{evaluated_clean_limit/1e5:.2f}L at 15-day terms). "
                    f"Advance collection of \u20b9{advance_required/1e5:.2f}L is suggested. "
                    f"See advance_recommendation for details."
                )
    else:
        tenor_recommendation_note = (
            "No requested amount provided. "
            "See tenor_schedule for evaluated safe limits and advisory remarks at all tenors."
        )

    # -------------------------------------------------------------------------
    # Step 8: Assemble result
    # -------------------------------------------------------------------------
    result = {
        "advised_limit":              advised_limit,
        "base_limit":                 round(base_limit, 2),
        "binding_anchor":             binding_anchor,
        "all_anchors": {
            "revenue_anchor":          round(anchor_revenue, 2),
            "tnw_anchor":              round(anchor_tnw, 2) if anchor_tnw is not None else None,
            "purchase_volume_anchor":  round(anchor_purchase, 2) if anchor_purchase is not None else None,
        },
        "tenor_multiplier":           tenor_mult,
        "tenor_bucket_days":          bucket,
        "haircut_applied":            haircut_applied,
        "volatility_haircut":         volatility_haircut,
        "data_penalty":               data_penalty,
        "terms_vs_profile":           terms_vs_profile,
        "requested_amount":           requested_amount,
        "credit_period_days":         int(credit_period_days or 30),
        "explanation":                None,
        "recommended_tenor_days":     recommended_tenor_days,
        "tenor_recommendation_note":  tenor_recommendation_note,
        "evaluated_clean_limit":      round(evaluated_clean_limit, 2),
        "advance_required":           round(advance_required, 2),
        "advance_pct_of_request":     advance_pct_of_request,
        "advance_recommendation":     advance_recommendation,
        "tenor_schedule":             tenor_schedule,
        "tenor_best_evaluated_days":  tenor_best_evaluated_days,
    }

    # -------------------------------------------------------------------------
    # Step 9: XAI
    # -------------------------------------------------------------------------
    if explainer is not None and x_instance is not None:
        try:
            explanation = explainer.explain_buyer(
                buyer_id      = buyer_id,
                x_instance    = np.array(x_instance).flatten(),
                blended_pd    = blended_pd,
                band          = pd_band,
                assessment    = terms_vs_profile,
                advised_limit = advised_limit,
                save          = True,
            )
            result["explanation"] = explanation
        except Exception as e:
            logger.warning("CreditExplainer failed for Buyer %s (non-fatal): %s", buyer_id, e)

    return result
