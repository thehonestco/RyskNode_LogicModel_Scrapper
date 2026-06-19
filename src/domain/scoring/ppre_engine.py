from __future__ import annotations

import logging
from typing import Any, Dict, Optional
from pathlib import Path

import joblib
import numpy as np
import pandas as pd

from domain.scoring.pd_mapper import derive_pd_band
from domain.scoring.limit_advisor import advise_limit
from domain.scoring.stress_tester import run_stress_test, format_stress_table
from domain.lgd.openlgd_model import predict_lgd
from domain.explainability.explainer import CreditExplainer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Engine Identity — injected into every scored output dict
# ---------------------------------------------------------------------------
ENGINE_NAME    = "Pralyon AI Predictive Risk Engine"
ENGINE_SHORT   = "PPRE"
ENGINE_VERSION = "1.0"
PLATFORM_NAME  = "RyskNode Labs"


def score_entity(
    feature_row:                 Dict[str, Any],
    artifact_dir:                str   = "src/domain/models",
    requested_amount:            Optional[float] = None,
    avg_monthly_purchase_volume: Optional[float] = None,
    credit_period_days:          int   = 30,
    ead:                         Optional[float] = None,
) -> Dict:
    """
    Score a single Buyer entity end-to-end using the
    Pralyon AI Predictive Risk Engine (PPRE).
    """
    adir = Path(artifact_dir)

    logger.info(
        "[%s v%s | %s] Scoring entity: %s",
        ENGINE_SHORT, ENGINE_VERSION, PLATFORM_NAME,
        feature_row.get("entity_id", "UNKNOWN")
    )

    # -------------------------------------------------------------------------
    # Step 1: PD Mapper -> Pralyon Risk Score + Credit Band
    # -------------------------------------------------------------------------
    pd_map = derive_pd_band(
        identity_score         = feature_row.get("identity_score", 50),
        financial_score        = feature_row.get("financial_score", 50),
        legal_risk_score       = feature_row.get("legal_score", 50),
        documentation_score    = feature_row.get("documentation_score", 50),
        criminal_case_count    = feature_row.get("criminal_case_count"),
        debt_to_equity         = feature_row.get("debt_to_equity"),
        current_ratio          = feature_row.get("current_ratio"),
        business_vintage_years = feature_row.get("business_vintage_years"),
    )

    # -------------------------------------------------------------------------
    # Step 2: Load PPRE artifacts
    # All four are optional — pipeline degrades gracefully without them.
    # -------------------------------------------------------------------------
    lgbm_art = joblib.load(adir / "lgbm_calibrated.pkl") if (adir / "lgbm_calibrated.pkl").exists() else None
    xgb_art  = joblib.load(adir / "xgb_calibrated.pkl")  if (adir / "xgb_calibrated.pkl").exists()  else None
    sc_art   = joblib.load(adir / "scorecard.pkl")        if (adir / "scorecard.pkl").exists()        else None
    lgd_art  = joblib.load(adir / "lgd.pkl")              if (adir / "lgd.pkl").exists()              else None
    meta     = joblib.load(adir / "meta.pkl")             if (adir / "meta.pkl").exists()             else {}
    X_train  = joblib.load(adir / "X_train.pkl")          if (adir / "X_train.pkl").exists()          else None

    feature_cols = meta.get("feature_cols", list(feature_row.keys()))

    # -------------------------------------------------------------------------
    # Steps 3-5: PD prediction + blend
    # -------------------------------------------------------------------------
    X = pd.DataFrame([feature_row])
    pds: Dict[str, float] = {}

    if sc_art and sc_art.model:
        woe_cols = [c for c in X.columns if c.endswith("_woe")]
        X_woe    = X[woe_cols] if woe_cols else X
        pds["lr"] = float(sc_art.model.predict_proba(X_woe)[:, 1][0])

    if lgbm_art:
        pds["lgbm"] = float(lgbm_art.predict_proba(X.values)[:, 1][0])

    if xgb_art:
        pds["xgb"] = float(xgb_art.predict_proba(X.values)[:, 1][0])

    pd_lr      = pds.get("lr",   0.05)
    pd_lgbm    = pds.get("lgbm", 0.05)
    pd_xgb     = pds.get("xgb",  0.05)
    blended_pd = float(np.mean([pd_lr, pd_lgbm, pd_xgb]))

    # -------------------------------------------------------------------------
    # Step 6: LGD
    # -------------------------------------------------------------------------
    lgd_pred  = None
    el_pct    = None
    el_amount = None
    if lgd_art:
        lgd_row   = pd.DataFrame([{**feature_row, "ead": ead or 0}])
        lgd_out   = predict_lgd(lgd_art, lgd_row)
        lgd_pred  = float(lgd_out["lgd_pred"].iloc[0])
        el_pct    = round(blended_pd * lgd_pred, 6)
        el_amount = round(el_pct * (ead or 0), 2)

    # -------------------------------------------------------------------------
    # Step 7: Limit advisory  (Panel C — sanctioned limit)
    # -------------------------------------------------------------------------
    limit_result = advise_limit(
        net_revenue_latest          = feature_row.get("net_revenue_latest", 0),
        pd_band                     = pd_map.pd_band,
        tangible_net_worth          = feature_row.get("tangible_net_worth"),
        turnover_cagr_5y            = feature_row.get("turnover_cagr_5y"),
        turnover_y1                 = feature_row.get("turnover_y1"),
        turnover_y2                 = feature_row.get("turnover_y2"),
        turnover_y3                 = feature_row.get("turnover_y3"),
        data_penalty                = pd_map.data_penalty,
        credit_period_days          = credit_period_days,
        requested_amount            = requested_amount,
        avg_monthly_purchase_volume = avg_monthly_purchase_volume,
        buyer_id                    = str(feature_row.get("entity_id", "UNKNOWN")),
        blended_pd                  = blended_pd,
    )
    evaluated_limit = limit_result["advised_limit"]

    # -------------------------------------------------------------------------
    # Step 8: Financial Stress Test  (Panel D — informational only)
    # Runs 7 shock scenarios against the base feature row.
    # Results do NOT change the evaluated_limit from Step 7.
    # -------------------------------------------------------------------------
    stress_results = run_stress_test(
        base_row         = feature_row,
        base_pd_result   = pd_map,
        base_limit       = evaluated_limit,
        blended_pd       = blended_pd,
        requested_amount = requested_amount or 0,
    )
    stress_table_text = format_stress_table(stress_results)

    # -------------------------------------------------------------------------
    # Step 9: XAI Explanation  (Panel E)
    # -------------------------------------------------------------------------
    xai_narrative  = ""
    shap_ranked    = []
    lime_explanation: Dict = {}
    xai_plot_paths = []

    if lgbm_art and xgb_art and X_train is not None:
        try:
            explainer = CreditExplainer(
                lgbm_model    = lgbm_art,
                xgb_model     = xgb_art,
                feature_names = feature_cols,
                X_train       = X_train,
                primary_model = "lgbm",
            )
            x_instance = np.array(
                [feature_row.get(c, 0.0) for c in feature_cols],
                dtype=float,
            )
            # Determine decision label for narrative
            _req       = requested_amount or 0
            _decision  = (
                "declined"       if pd_map.pd_band in ("D", "UNSCOREABLE") else
                "within_limit"   if evaluated_limit >= _req else
                "exceeds_advised"
            )
            xai_report = explainer.explain_buyer(
                buyer_id      = str(feature_row.get("entity_id", "UNKNOWN")),
                x_instance    = x_instance,
                blended_pd    = blended_pd,
                band          = pd_map.pd_band,
                decision      = _decision,
                advised_limit = evaluated_limit,
                save          = True,
            )
            xai_narrative   = xai_report.get("narrative", "")
            shap_ranked     = xai_report.get("shap_ranked", [])
            lime_explanation= xai_report.get("lime_explanation", {})
            xai_plot_paths  = xai_report.get("plot_paths", [])

        except Exception as e:
            logger.warning("[PPRE] CreditExplainer failed (non-fatal): %s", e)
            xai_narrative = (
                f"BUYER RISK ASSESSMENT\n"
                f"Band: {pd_map.pd_band} | Pralyon Risk Score: {pd_map.governance_score}\n"
                f"Reason codes: {', '.join(pd_map.reason_codes) or 'RC00: No adverse signals'}"
            )
        else:
            pass
    else:
        # No trained artifacts yet — narrative from reason codes
        xai_narrative = (
            f"BUYER RISK ASSESSMENT\n"
            f"Band: {pd_map.pd_band} | Pralyon Risk Score: {pd_map.governance_score}\n"
            f"Reason codes: {', '.join(pd_map.reason_codes) or 'RC00: No adverse signals'}\n"
            f"(Install trained artifacts in {artifact_dir} to activate SHAP + LIME)"
        )

    # -------------------------------------------------------------------------
    # Step 10: Assemble and return full output
    # -------------------------------------------------------------------------
    return {
        # Panel A — Engine identity (top of every response)
        "engine_name":          ENGINE_NAME,
        "engine_short":         ENGINE_SHORT,
        "engine_version":       ENGINE_VERSION,
        "platform_name":        PLATFORM_NAME,

        # Entity
        "entity_id":            feature_row.get("entity_id"),

        # Panel A — Pralyon Risk Score + Credit Band
        "pralyon_risk_score":   pd_map.governance_score,
        "governance_score":     pd_map.governance_score,
        "pd_band":              pd_map.pd_band,
        "data_penalty":         pd_map.data_penalty,
        "override_flags":       pd_map.override_flags,

        # Panel B — Default Probability + LGD + EL
        "blended_pd":           round(blended_pd, 6),
        "model_pds":            pds,
        "lgd_pred":             lgd_pred,
        "el_pct":               el_pct,
        "el_amount":            el_amount,

        # Panel C — 3-Anchor Limit Advisory (sanctioned limit)
        "evaluated_limit":      evaluated_limit,
        "recommended_tenor":    limit_result["recommended_tenor_days"],
        "advance_required":     limit_result["advance_required"],
        "tenor_schedule":       limit_result["tenor_schedule"],
        "tenor_note":           limit_result["tenor_recommendation_note"],

        # Panel D — Financial Stress Test
        "stress_table":         [vars(r) for r in stress_results],
        "stress_table_text":    stress_table_text,

        # Panel E — XAI Explanation
        "xai_narrative":        xai_narrative,
        "shap_ranked":          shap_ranked,
        "lime_explanation":     lime_explanation,
        "reason_codes":         pd_map.reason_codes,
        "xai_plot_paths":       xai_plot_paths,
    }
