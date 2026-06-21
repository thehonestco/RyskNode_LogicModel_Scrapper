"""
part2/scoring/openriskscore_blender.py
======================================
PD Blender — Expert Scorecard + LightGBM + XGBoost ensemble.

Blends three PD sources into one calibrated blended PD, maps it to the
master scale band (AAA→D) and a 300–850 credit score.

Purpose
-------
RyskNode is a counterparty risk platform. The SELLER (MSME) submits a
BUYER's identifiers (GSTIN / PAN / CIN). This module scores the BUYER —
producing the probability that the BUYER defaults on payment to the SELLER.

New in this version
-------------------
bland_and_explain() — single entry-point that:
  1. Blends Buyer PDs
  2. Maps to band + score
  3. Calls CreditExplainer.explain_buyer() automatically
  4. Returns a unified result dict (score_result + explanation)

Original low-level helpers (blend_pd, pd_to_band, pd_to_score) are kept
untouched for backward compatibility.

Conceptual lineage
------------------
Ensemble PD approach inspired by openrisk / CreditPy blending patterns.
XAI layer (SHAP + LIME) inspired by:
  Nallakaruppan et al. (2024). Applied Soft Computing.
  doi: 10.1016/j.asoc.2024.111307
"""

from __future__ import annotations

import logging
from typing import Any, Dict, Optional

import numpy as np

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Blending weights
# ---------------------------------------------------------------------------

BLEND_WEIGHTS = {"expert": 0.40, "lgbm": 0.35, "xgb": 0.25}

# ---------------------------------------------------------------------------
# Master scale — PD upper boundary per band
# ---------------------------------------------------------------------------

MASTER_SCALE = [
    ("AAA", 0.002),
    ("AA", 0.005),
    ("A", 0.010),
    ("BBB", 0.020),
    ("BB", 0.050),
    ("B", 0.100),
    ("CCC", 0.200),
    ("D", 1.000),
]

# Credit score: PD band → 300–850
BAND_SCORE = {
    "AAA": 820,
    "AA": 780,
    "A": 740,
    "BBB": 680,
    "BB": 620,
    "B": 560,
    "CCC": 480,
    "D": 300,
}


# ---------------------------------------------------------------------------
# Low-level helpers (backward compatible)
# ---------------------------------------------------------------------------


def blend_pd(
    expert_pd: float,
    lgbm_pd: float,
    xgb_pd: float,
    weights: dict = None,
) -> float:
    """Return weighted ensemble PD for the Buyer from three model sources."""
    w = weights or BLEND_WEIGHTS
    return expert_pd * w["expert"] + lgbm_pd * w["lgbm"] + xgb_pd * w["xgb"]


def pd_to_band(pd: float) -> str:
    """Map a blended Buyer PD value to its master scale band."""
    for band, upper in MASTER_SCALE:
        if pd <= upper:
            return band
    return "D"


def pd_to_score(pd: float) -> int:
    """Map blended Buyer PD directly to credit score (300–850)."""
    band = pd_to_band(pd)
    return BAND_SCORE.get(band, 300)


# ---------------------------------------------------------------------------
# Primary entry-point: blend + explain in one call
# ---------------------------------------------------------------------------


def blend_and_explain(
    expert_pd: float,
    lgbm_pd: float,
    xgb_pd: float,
    buyer_id: str = "UNKNOWN",
    x_instance: Any = None,
    explainer: Any = None,  # CreditExplainer instance
    weights: dict = None,
    advised_limit: float = 0.0,
    decision: str = "pending",
) -> Dict:
    """
    Blend Buyer PDs, map to band + score, and generate XAI explanation.

    This is the recommended single entry-point for the full scoring pipeline.
    It replaces calling blend_pd() + pd_to_band() + pd_to_score() separately,
    and additionally fires CreditExplainer.explain_buyer() when an
    explainer instance and feature vector are provided.

    Context
    -------
    The SELLER submits a BUYER's GSTIN/PAN/CIN to RyskNode.
    Part 1 pulls public data on the BUYER.
    This function scores the BUYER's probability of payment default.
    The output informs the SELLER how much trade credit is safe to extend.

    Parameters
    ----------
    expert_pd     : PD from WoE scorecard (scorecardpy) for this Buyer
    lgbm_pd       : PD from LightGBM model for this Buyer
    xgb_pd        : PD from XGBoost model for this Buyer
    buyer_id      : Buyer entity identifier (GSTIN / PAN / CIN or internal ID)
    x_instance    : 1-D numpy array of Buyer feature values — required for XAI
    explainer     : fitted CreditExplainer instance from domain.explainability
    weights       : optional override for BLEND_WEIGHTS
    advised_limit : advised credit exposure limit from limit_advisor.advise_limit() (INR)
    decision      : "approved" | "declined" | "within_limit" | "exceeds_advised"

    Returns
    -------
    dict with keys:
        buyer_id, blended_pd, band, credit_score,
        expert_pd, lgbm_pd, xgb_pd, weights_used,
        explanation  (full CreditExplainer report dict, or None)

    Example
    -------
    ::

        from domain.scoring.openriskscore_blender import blend_and_explain
        from domain.explainability import CreditExplainer

        explainer = CreditExplainer(
            lgbm_model=lgbm, xgb_model=xgb,
            feature_names=FEATURE_COLS, X_train=X_train
        )

        # Seller wants to check Buyer "BUYER_GST_001" before extending credit
        result = blend_and_explain(
            expert_pd=0.04,  lgbm_pd=0.06,  xgb_pd=0.05,
            buyer_id="BUYER_GST_001",
            x_instance=x_row,
            explainer=explainer,
            advised_limit=1_500_000,
            decision="within_limit",
        )

        print(result["band"])                          # → "BB"
        print(result["credit_score"])                  # → 620
        print(result["explanation"]["narrative"])      # → plain-English memo for Seller
    """
    w = weights or BLEND_WEIGHTS
    blended = blend_pd(expert_pd, lgbm_pd, xgb_pd, w)
    band = pd_to_band(blended)
    score = pd_to_score(blended)

    result: Dict = {
        "buyer_id": buyer_id,
        "blended_pd": round(blended, 6),
        "band": band,
        "credit_score": score,
        "expert_pd": expert_pd,
        "lgbm_pd": lgbm_pd,
        "xgb_pd": xgb_pd,
        "weights_used": w,
        "explanation": None,
    }

    # Fire explainer if wired up
    if explainer is not None and x_instance is not None:
        try:
            explanation = explainer.explain_buyer(
                buyer_id=buyer_id,
                x_instance=np.array(x_instance).flatten(),
                blended_pd=blended,
                band=band,
                decision=decision,
                advised_limit=advised_limit,
                save=True,
            )
            result["explanation"] = explanation
            logger.info("Buyer explanation generated for %s | Band: %s | PD: %.4f", buyer_id, band, blended)
        except Exception as e:
            logger.warning("CreditExplainer failed for Buyer %s (non-fatal): %s", buyer_id, e)
    else:
        if explainer is None:
            logger.debug(
                "No explainer provided for Buyer %s — skipping XAI. Pass a fitted CreditExplainer instance to enable.",
                buyer_id,
            )
        if x_instance is None:
            logger.debug("No x_instance provided for Buyer %s — skipping XAI.", buyer_id)

    return result
