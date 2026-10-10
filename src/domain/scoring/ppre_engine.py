import logging
from typing import Any, Dict, List, Optional
from pathlib import Path

import numpy as np
import pandas as pd

from domain.scoring.pd_mapper import derive_pd_band
from domain.scoring.limit_advisor import advise_limit
from domain.scoring.stress_tester import run_stress_test, format_stress_table
from domain.lgd.openlgd_model import predict_lgd
from domain.explainability.explainer import CreditExplainer

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domain-knowledge XAI — feature direction rules (per RA_MODEL_Full_Documentation §17)
# These encode the CORRECT financial-economics direction for each feature.
# SHAP from a synthetic-trained model may be inverted; these rules are not.
# ---------------------------------------------------------------------------

# Features where HIGHER value = MORE risk (risk-increasing direction)
_RISK_INCREASING_HIGH = {
    "legal_score",          # higher legal_score = more legal risk
    "debt_to_equity",       # higher leverage = more risk
    "dso",                  # longer collection period = more risk
    "case_count_total",
    "case_count_active",
    "case_count_drt",
    "case_count_nclt",
    "criminal_case_count",
}

# Features where LOWER value = MORE risk (risk-increasing direction when low)
_RISK_INCREASING_LOW = {
    "current_ratio",        # below 1.0 = liquidity stress
    "quick_ratio",          # below 0.75 = immediate liquidity stress
    "working_capital",      # negative = operational stress
    "epfo_headcount",       # very low = micro/unregistered entity (higher risk)
    "financial_score",      # lower = worse financial health
    "identity_score",       # lower = weaker identity verification
    "documentation_score",  # lower = poorer data quality
    "conduct_score",        # lower = worse conduct
    "business_vintage_years",  # younger = less track record
    "revenue_cagr_5y",
    "net_revenue_cagr_5y",
}

# Thresholds below which a feature signals risk
_RISK_LOW_THRESHOLDS = {
    "current_ratio": 1.5,
    "quick_ratio": 1.0,
    "working_capital": 0,
    "epfo_headcount": 50,
    "financial_score": 60,
    "identity_score": 70,
    "documentation_score": 60,
    "conduct_score": 60,
    "business_vintage_years": 3,
    "revenue_cagr_5y": 0.05,
    "net_revenue_cagr_5y": 0.05,
}

# Thresholds above which a feature signals risk
_RISK_HIGH_THRESHOLDS = {
    "legal_score": 30,
    "debt_to_equity": 2.0,
    "dso": 60,
    "case_count_total": 3,
    "case_count_active": 2,
    "case_count_drt": 0,
    "case_count_nclt": 0,
    "criminal_case_count": 0,
}

# CIBIL-like credit score scale (300-850) per RA Model doc §13.7 & §20.3
CREDIT_SCORE_MAP: Dict[str, int] = {
    "AAA": 820,
    "AA": 780,
    "A": 740,
    "BBB": 680,
    "BB": 620,
    "B": 560,
    "CCC": 480,
    "D": 300,
    "UNSCOREABLE": 300,
}


def _fmt_val(feat: str, val) -> str:
    """Format feature value in human-readable financial terms."""
    if val is None or (isinstance(val, float) and np.isnan(val)):
        return "N/A"
    try:
        v = float(val)
        if feat == "working_capital" or "capital" in feat or "networth" in feat or "revenue" in feat:
            if abs(v) >= 1e7:
                return f"₹{v / 1e7:,.2f} Cr"
            elif abs(v) >= 1e5:
                return f"₹{v / 1e5:,.2f} Lakh"
            return f"₹{v:,.0f}"
        if feat == "epfo_headcount":
            return f"{int(v)} employees"
        if feat in ("dso", "dpo"):
            return f"{int(v)} days"
        if "vintage" in feat:
            return f"{v:.1f} years"
        if "score" in feat:
            return f"{v:.1f}/100"
        if "ratio" in feat or "equity" in feat:
            return f"{v:.2f}x"
        if "cagr" in feat:
            return f"{v*100:.1f}%"
        return f"{v:.3g}"
    except Exception:
        return str(val)


def _feature_label(feat: str) -> str:
    _LABELS = {
        "current_ratio": "Current Ratio",
        "quick_ratio": "Quick Ratio",
        "debt_to_equity": "Debt-to-Equity Ratio",
        "dso": "Days Sales Outstanding",
        "working_capital": "Working Capital",
        "epfo_headcount": "EPFO Workforce Headcount",
        "legal_score": "Legal Risk Score",
        "financial_score": "Financial Score",
        "identity_score": "Identity Score",
        "documentation_score": "Documentation Score",
        "conduct_score": "Conduct Score",
        "business_vintage_years": "Business Vintage",
        "net_revenue_cagr_5y": "Revenue CAGR (5Y)",
        "revenue_cagr_5y": "Revenue CAGR (5Y)",
        "case_count_active": "Active Legal Cases",
        "case_count_drt": "DRT Cases",
        "case_count_nclt": "NCLT / Insolvency Cases",
        "criminal_case_count": "Criminal Cases",
    }
    return _LABELS.get(feat, feat.replace("_", " ").title())


def build_domain_xai_narrative(feature_row: Dict, pd_map, conduct_reasons: List[str] = None) -> tuple:
    """
    Build XAI narrative and shap_ranked list from domain-knowledge rules.

    Uses the actual financial ratios, domain scores, and conduct signals
    (which are always directionally correct per RA_MODEL_Full_Documentation §17.3)
    rather than SHAP values from a synthetic-data model that may have inverted
    feature-default relationships.

    Returns
    -------
    (xai_narrative: str, shap_ranked: List[Dict])
    """
    risk_drivers = []   # list of (label, feat, val, reason)
    mitigants = []      # list of (label, feat, val, reason)

    def _add(feat, risk: bool, reason: str = ""):
        val = feature_row.get(feat)
        entry = {
            "feature": feat,
            "label": _feature_label(feat),
            "feature_value": val,
            "shap_value": None,   # no raw SHAP — domain-rule based
            "direction": "risk_increasing" if risk else "risk_reducing",
            "reason": reason,
        }
        if risk:
            risk_drivers.append(entry)
        else:
            mitigants.append(entry)

    # --- Legal score (higher = more risk per doc §6.4) ---
    legal_score = feature_row.get("legal_score")
    if legal_score is not None:
        if legal_score > _RISK_HIGH_THRESHOLDS.get("legal_score", 30):
            _add("legal_score", risk=True, reason=f"Legal risk score {legal_score:.0f}/100 is elevated (>30 = adverse signal)")
        else:
            _add("legal_score", risk=False, reason=f"Legal risk score {legal_score:.0f}/100 is low (minimal legal exposure)")

    # --- Current ratio ---
    cr = feature_row.get("current_ratio")
    if cr is not None:
        if cr < 1.0:
            _add("current_ratio", risk=True, reason=f"Current ratio {cr:.2f}x is below 1.0 — liquidity stress (hard override triggered)")
        elif cr < 1.5:
            _add("current_ratio", risk=True, reason=f"Current ratio {cr:.2f}x is below adequate threshold (1.5x)")
        else:
            _add("current_ratio", risk=False, reason=f"Current ratio {cr:.2f}x is adequate-to-strong (≥1.5x)")

    # --- Quick ratio ---
    qr = feature_row.get("quick_ratio")
    if qr is not None:
        if qr < 0.75:
            _add("quick_ratio", risk=True, reason=f"Quick ratio {qr:.2f}x is weak (<0.75x) — immediate liquidity risk")
        elif qr >= 1.0:
            _add("quick_ratio", risk=False, reason=f"Quick ratio {qr:.2f}x is strong (≥1.0x)")
        else:
            _add("quick_ratio", risk=True, reason=f"Quick ratio {qr:.2f}x is below 1.0x (adequate threshold)")

    # --- Debt to equity ---
    de = feature_row.get("debt_to_equity")
    if de is not None:
        if de > 3.0:
            _add("debt_to_equity", risk=True, reason=f"Debt-to-equity {de:.2f}x exceeds 3.0x — very high leverage (hard override)")
        elif de > 2.0:
            _add("debt_to_equity", risk=True, reason=f"Debt-to-equity {de:.2f}x exceeds 2.0x — elevated leverage")
        else:
            _add("debt_to_equity", risk=False, reason=f"Debt-to-equity {de:.2f}x is within acceptable range")

    # --- DSO ---
    dso = feature_row.get("dso")
    if dso is not None:
        if dso > 90:
            _add("dso", risk=True, reason=f"DSO {dso:.0f} days is very high (>90 days) — slow receivables collection")
        elif dso > 60:
            _add("dso", risk=True, reason=f"DSO {dso:.0f} days is elevated (>60 days)")
        else:
            _add("dso", risk=False, reason=f"DSO {dso:.0f} days is within acceptable range")

    # --- Working capital ---
    wc = feature_row.get("working_capital")
    if wc is not None:
        if wc < 0:
            _add("working_capital", risk=True, reason="Negative working capital — operational cash buffer deficit")
        else:
            _add("working_capital", risk=False, reason=f"Positive working capital ({_fmt_val('working_capital', wc)}) — adequate operational buffer")

    # --- EPFO headcount ---
    epfo = feature_row.get("epfo_headcount")
    if epfo is not None:
        if epfo < 10:
            _add("epfo_headcount", risk=True, reason=f"Only {epfo:.0f} registered EPFO employees — micro-entity with limited scale")
        elif epfo >= 100:
            _add("epfo_headcount", risk=False, reason=f"{epfo:.0f} EPFO-registered employees — established workforce scale")

    # --- Business vintage ---
    vintage = feature_row.get("business_vintage_years")
    if vintage is not None:
        if vintage < 1:
            _add("business_vintage_years", risk=True, reason="Business <1 year old — new entity, minimal track record (floor = B)")
        elif vintage < 3:
            _add("business_vintage_years", risk=True, reason=f"{vintage:.1f} years vintage — early stage entity")
        else:
            _add("business_vintage_years", risk=False, reason=f"{vintage:.1f} years vintage — established entity")

    # --- Revenue CAGR ---
    cagr = feature_row.get("net_revenue_cagr_5y") or feature_row.get("revenue_cagr_5y")
    if cagr is not None:
        if cagr < 0:
            _add("net_revenue_cagr_5y", risk=True, reason=f"Revenue CAGR {cagr*100:.1f}% — declining turnover (0.75 haircut applied)")
        elif cagr >= 0.10:
            _add("net_revenue_cagr_5y", risk=False, reason=f"Revenue CAGR {cagr*100:.1f}% — strong growth trend")

    # --- Domain score signals ---
    fin_score = feature_row.get("financial_score")
    if fin_score is not None and fin_score < 50:
        _add("financial_score", risk=True, reason=f"Financial score {fin_score:.0f}/100 is weak (<50)")

    # --- Conduct signals from reasons ---
    if conduct_reasons:
        for code in conduct_reasons:
            if "INSOLVENCY" in code:
                risk_drivers.insert(0, {
                    "feature": "ecourts_insolvency",
                    "label": "NCLT / Insolvency Petition",
                    "feature_value": 1,
                    "shap_value": None,
                    "direction": "risk_increasing",
                    "reason": "Active NCLT insolvency petition — severe legal stress signal",
                })
            elif "DRT" in code:
                risk_drivers.insert(0, {
                    "feature": "case_count_drt",
                    "label": "DRT Case (Debt Recovery)",
                    "feature_value": 1,
                    "shap_value": None,
                    "direction": "risk_increasing",
                    "reason": "Active Debt Recovery Tribunal case — creditor-initiated recovery",
                })
            elif "NBFC_ONLY" in code:
                risk_drivers.append({
                    "feature": "lender_quality",
                    "label": "Lender Quality",
                    "feature_value": "NBFC Only",
                    "shap_value": None,
                    "direction": "risk_increasing",
                    "reason": "NBFC-only lender — did not qualify for bank credit",
                })
            elif "PSU" in code:
                mitigants.append({
                    "feature": "lender_quality",
                    "label": "Lender Quality",
                    "feature_value": "PSU Bank",
                    "shap_value": None,
                    "direction": "risk_reducing",
                    "reason": "PSU bank lender — passed institutional due diligence",
                })
            elif "PF_FILING_IRREGULAR" in code:
                risk_drivers.append({
                    "feature": "pf_filing_regular",
                    "label": "PF Filing Regularity",
                    "feature_value": "Irregular",
                    "shap_value": None,
                    "direction": "risk_increasing",
                    "reason": "Irregular PF filings — non-compliance with employee obligations",
                })
            elif "GST_NON_FILER" in code:
                risk_drivers.append({
                    "feature": "gst_filing_consistency",
                    "label": "GST Filing Status",
                    "feature_value": "Non-Filer",
                    "shap_value": None,
                    "direction": "risk_increasing",
                    "reason": "GST non-filer — tax compliance failure",
                })

    # Override flags from pd_mapper
    for flag in pd_map.override_flags:
        if "CRIMINAL" in flag:
            risk_drivers.insert(0, {
                "feature": "criminal_case_count",
                "label": "Criminal Cases",
                "feature_value": feature_row.get("criminal_case_count", 1),
                "shap_value": None,
                "direction": "risk_increasing",
                "reason": "Criminal case detected — band downgraded 2 notches",
            })

    # Sort: risk drivers by severity (conduct signals first, then financial)
    # Mitigants by strength
    top_risks = risk_drivers[:4]
    top_mitigants = mitigants[:3]

    # Build HTML narrative
    lines = []
    if top_risks:
        lines.append("<b>Primary Risk Drivers:</b>")
        lines.append("<ul style='margin-top:4px; margin-bottom:12px; padding-left:20px;'>")
        for f in top_risks:
            val_str = _fmt_val(f["feature"], f["feature_value"])
            lines.append(f"  <li><b>{f['label']}</b> ({val_str}) — {f['reason']}</li>")
        lines.append("</ul>")

    if top_mitigants:
        lines.append("<b>Key Mitigating Factors:</b>")
        lines.append("<ul style='margin-top:4px; margin-bottom:0; padding-left:20px;'>")
        for f in top_mitigants:
            val_str = _fmt_val(f["feature"], f["feature_value"])
            lines.append(f"  <li><b>{f['label']}</b> ({val_str}) — {f['reason']}</li>")
        lines.append("</ul>")

    if not lines:
        lines.append("<i>No adverse risk signals identified from available data.</i>")

    narrative = "".join(lines)

    # Combine for shap_ranked output (risk drivers first, then mitigants)
    all_ranked = top_risks + top_mitigants

    return narrative, all_ranked

# ---------------------------------------------------------------------------
# Engine Identity — injected into every scored output dict
# ---------------------------------------------------------------------------
ENGINE_NAME = "Pralyon AI Predictive Risk Engine"
ENGINE_SHORT = "PPRE"
ENGINE_VERSION = "1.0"
PLATFORM_NAME = "RyskNode Labs"


def score_entity(
    feature_row: Dict[str, Any],
    artifacts: Dict[str, Any],
    requested_amount: Optional[float] = None,
    avg_monthly_purchase_volume: Optional[float] = None,
    credit_period_days: int = 30,
    ead: Optional[float] = None,
    include_xai: bool = True,
) -> Dict:
    """
    Score a single Buyer entity end-to-end using the
    Pralyon AI Predictive Risk Engine (PPRE).
    """
    logger.info(
        "[%s v%s | %s] Scoring entity: %s",
        ENGINE_SHORT,
        ENGINE_VERSION,
        PLATFORM_NAME,
        feature_row.get("entity_id", "UNKNOWN"),
    )

    # -------------------------------------------------------------------------
    # Step 1: PD Mapper -> Pralyon Risk Score + Credit Band
    # -------------------------------------------------------------------------
    pd_map = derive_pd_band(
        identity_score=feature_row.get("identity_score", 50),
        financial_score=feature_row.get("financial_score", 50),
        legal_risk_score=feature_row.get("legal_score", 50),
        documentation_score=feature_row.get("documentation_score", 50),
        criminal_case_count=feature_row.get("criminal_case_count"),
        debt_to_equity=feature_row.get("debt_to_equity"),
        current_ratio=feature_row.get("current_ratio"),
        business_vintage_years=feature_row.get("business_vintage_years"),
    )

    # -------------------------------------------------------------------------
    # Step 2: Extract PPRE artifacts
    # All are optional — pipeline degrades gracefully without them.
    # -------------------------------------------------------------------------
    lgbm_art = artifacts.get("lgbm_art")
    xgb_art = artifacts.get("xgb_art")
    sc_art = artifacts.get("sc_art")
    lgd_art = artifacts.get("lgd_art")
    meta = artifacts.get("meta", {})
    X_train = artifacts.get("X_train")

    FEATURE_NAMES = [
        "identity_score",
        "financial_score",
        "legal_score",
        "documentation_score",
        "current_ratio",
        "quick_ratio",
        "debt_to_equity",
        "dso",
        "net_revenue_cagr_5y",
        "working_capital",
        "tangible_net_worth",
        "net_revenue_latest",
        "turnover_y1",
        "turnover_y2",
        "turnover_y3",
        "charge_count_active",
        "has_any_active_charge",
        "has_recent_charge_90d",
        "old_unsatisfied_charge_count",
        "distinct_lender_count",
        "case_count_total",
        "case_count_active",
        "case_count_drt",
        "case_count_nclt",
        "case_count_hc",
        "criminal_case_count",
        "has_insolvency_petition",
        "gst_turnover",
        "gst_filing_consistency",
        "high_director_company_count",
        "max_director_company_count",
        "epfo_headcount",
        "pf_filing_regular",
        "revenue_per_employee_outlier",
        "business_vintage_years",
        "conduct_score",
    ]

    feature_cols = meta.get("feature_cols", FEATURE_NAMES)

    # -------------------------------------------------------------------------
    # Steps 3-5: PD prediction + blend
    # -------------------------------------------------------------------------
    X = pd.DataFrame([feature_row]).reindex(columns=FEATURE_NAMES, fill_value=0.0)
    # Ensure any residual None values are filled
    X = X.fillna(0.0)

    # Categorical columns must be strings or mapped appropriately.
    # 'gst_filing_consistency' is categorical.
    # In training, OrdinalEncoder maps it. Let's make sure it has numeric representation if needed,
    # but since it was parsed as category in train, or mapped, we can map common strings to codes:
    gst_map = {"REGULAR": 0.0, "MINOR_GAPS": 1.0, "MAJOR_GAPS": 2.0, "NON_FILER": 3.0, "UNKNOWN": 4.0}
    if X["gst_filing_consistency"].dtype == object or X["gst_filing_consistency"].dtype == str:
        X["gst_filing_consistency"] = X["gst_filing_consistency"].map(gst_map).fillna(4.0)

    pds: Dict[str, float] = {}
    # The trained models (LGBM, XGB, LGD) expect exactly 7 input features in the order of available_features.
    # From training_data.csv columns intersecting with FEATURE_NAMES:
    # ['current_ratio', 'quick_ratio', 'debt_to_equity', 'dso', 'working_capital', 'epfo_headcount', 'legal_score']
    # Let's map these keys from feature_row (handling aliases if needed).
    features_7 = [
        float(feature_row.get("current_ratio") or 0.0),
        float(feature_row.get("quick_ratio") or 0.0),
        float(feature_row.get("debt_to_equity") or 0.0),
        float(feature_row.get("dso") or 0.0),
        float(feature_row.get("working_capital") or 0.0),
        float(feature_row.get("epfo_headcount") or 0.0),
        float(feature_row.get("legal_score") or 0.0),
    ]
    X_7 = np.array([features_7])

    pds: Dict[str, float] = {}

    if sc_art and hasattr(sc_art, "predict_proba"):
        try:
            pds["lr"] = float(sc_art.predict_proba(X_7)[:, 1][0])
        except Exception as e:
            logger.warning("[PPRE] Scorecard prediction failed: %s", e)

    if lgbm_art:
        try:
            pds["lgbm"] = float(lgbm_art.predict_proba(X_7)[:, 1][0])
        except Exception as e:
            logger.warning("[PPRE] LightGBM prediction failed: %s", e)

    if xgb_art:
        try:
            pds["xgb"] = float(xgb_art.predict_proba(X_7)[:, 1][0])
        except Exception as e:
            logger.warning("[PPRE] XGBoost prediction failed: %s", e)

    pd_lr = pds.get("lr", 0.0)
    pd_lgbm = pds.get("lgbm", 0.0)
    pd_xgb = pds.get("xgb", 0.0)

    # Blend per §14 Hardcoded Values Master Register: LGBM=0.40, XGB=0.30, SC=0.30
    if pds:
        # At least one model produced a PD — blend available models proportionally
        # Weights sum to 1.0 for whichever models fired; missing models contribute 0
        total_weight = (0.40 if "lgbm" in pds else 0.0) + (0.30 if "xgb" in pds else 0.0) + (0.30 if "lr" in pds else 0.0)
        blended_pd = float(pd_lgbm * 0.40 + pd_xgb * 0.30 + pd_lr * 0.30) / total_weight
    else:
        # No ML models loaded — derive proxy PD from governance_score band to maintain
        # consistency between pd_band and blended_pd (avoid constant 0.05 mismatch).
        # Maps each band to its approximate midpoint PD.
        _BAND_MIDPOINT_PD = {
            "AAA": 0.001, "AA": 0.003, "A": 0.007, "BBB": 0.015,
            "BB": 0.035, "B": 0.075, "CCC": 0.150, "D": 0.500, "UNSCOREABLE": 1.0,
        }
        blended_pd = _BAND_MIDPOINT_PD.get(pd_map.pd_band, 0.050)
        logger.info(
            "[PPRE] No ML models loaded — blended_pd derived from band %s → %.4f",
            pd_map.pd_band, blended_pd,
        )
    blended_pd = float(blended_pd)

    # -------------------------------------------------------------------------
    # Step 6: LGD
    # -------------------------------------------------------------------------
    lgd_pred = None
    el_pct = None
    el_amount = None
    if lgd_art:
        try:
            if isinstance(lgd_art, dict):
                lgd_row = pd.DataFrame([{**feature_row, "ead": ead or 0}])
                lgd_out = predict_lgd(lgd_art, lgd_row)
                lgd_pred = float(lgd_out["lgd_pred"].iloc[0])
            else:
                # Raw LGBMRegressor
                lgd_pred = float(lgd_art.predict(X_7)[0])
            el_pct = round(blended_pd * lgd_pred, 6)
            ead_basis = ead if ead is not None else (requested_amount if requested_amount is not None else None)
            el_amount = round(el_pct * ead_basis, 2) if ead_basis is not None else None
        except Exception as e:
            logger.warning("[PPRE] LGD prediction failed: %s", e)

    # -------------------------------------------------------------------------
    # Step 7: Limit advisory  (Panel C — sanctioned limit)
    # -------------------------------------------------------------------------
    limit_result = advise_limit(
        net_revenue_latest=feature_row.get("net_revenue_latest", 0),
        pd_band=pd_map.pd_band,
        tangible_net_worth=feature_row.get("tangible_net_worth"),
        turnover_cagr_5y=feature_row.get("turnover_cagr_5y"),
        turnover_y1=feature_row.get("turnover_y1"),
        turnover_y2=feature_row.get("turnover_y2"),
        turnover_y3=feature_row.get("turnover_y3"),
        data_penalty=pd_map.data_penalty,
        credit_period_days=credit_period_days,
        requested_amount=requested_amount,
        avg_monthly_purchase_volume=avg_monthly_purchase_volume,
        buyer_id=str(feature_row.get("entity_id", "UNKNOWN")),
        blended_pd=blended_pd,
    )
    evaluated_limit = limit_result["advised_limit"]
    if el_amount is None and el_pct is not None and evaluated_limit:
        el_amount = round(el_pct * evaluated_limit, 2)
    credit_score = CREDIT_SCORE_MAP.get(pd_map.pd_band, 300)

    # -------------------------------------------------------------------------
    # Step 8: Financial Stress Test  (Panel D — informational only)
    # Runs 7 shock scenarios against the base feature row.
    # Results do NOT change the evaluated_limit from Step 7.
    # -------------------------------------------------------------------------
    stress_results = run_stress_test(
        base_row=feature_row,
        base_pd_result=pd_map,
        base_limit=evaluated_limit,
        blended_pd=blended_pd,
        requested_amount=requested_amount or 0,
    )
    stress_table_text = format_stress_table(stress_results)

    # -------------------------------------------------------------------------
    # Step 9: XAI Explanation  (Panel E)
    # SHAP + LIME + Long Narrative using reference dataset background
    # -------------------------------------------------------------------------
    xai_narrative = ""
    shap_ranked = []
    lime_explanation: Dict = {}
    xai_plot_paths = []

    domain_scores = {
        "financial_score": feature_row.get("financial_score"),  # actual financial scorecard score (0-100)
        "identity_score": feature_row.get("identity_score"),
        "legal_score": feature_row.get("legal_score"),
        "documentation_score": feature_row.get("documentation_score"),
        "conduct_score": feature_row.get("conduct_score"),
    }
    all_reason_codes = list(pd_map.reason_codes) + list(feature_row.get("conduct_reasons", []))

    if lgbm_art and X_train is not None and include_xai:
        try:
            explainer = CreditExplainer(
                lgbm_model=lgbm_art,
                xgb_model=xgb_art,
                feature_names=feature_cols,
                X_train=X_train,
                primary_model="lgbm",
            )
            x_instance = np.array(
                [feature_row.get(c, 0.0) for c in feature_cols],
                dtype=float,
            )
            _req = requested_amount or 0
            _decision = (
                "declined"
                if pd_map.pd_band in ("D", "UNSCOREABLE")
                else "within_limit"
                if evaluated_limit >= _req
                else "exceeds_advised"
            )
            xai_report = explainer.explain_buyer(
                buyer_id=str(feature_row.get("entity_id", "UNKNOWN")),
                x_instance=x_instance,
                blended_pd=blended_pd,
                band=pd_map.pd_band,
                decision=_decision,
                advised_limit=evaluated_limit,
                domain_scores=domain_scores,
                reason_codes=all_reason_codes,
                save=True,
                as_html=True,
            )
            xai_summary = xai_report.get("short_summary", "")
            xai_summary_text = xai_report.get("short_summary_text", "")
            dimension_readings = xai_report.get("dimension_readings", {})
            table_enrichments = xai_report.get("table_enrichments", {})
            lime_methodology_note = xai_report.get("lime_methodology_note", "")
            xai_narrative = xai_report.get("detailed_narrative") or xai_report.get("narrative", "")
            xai_narrative_text = xai_report.get("narrative_text", "")
            xai_narrative_lines = xai_report.get("narrative_lines", [])
            shap_ranked = xai_report.get("shap_ranked", [])
            lime_explanation = xai_report.get("lime_explanation", {})
            xai_plot_paths = xai_report.get("plot_paths", [])

        except Exception as e:
            logger.warning("[PPRE] CreditExplainer failed (non-fatal): %s", e)
            xai_narrative, shap_ranked = build_domain_xai_narrative(
                feature_row=feature_row,
                pd_map=pd_map,
                conduct_reasons=feature_row.get("conduct_reasons", []),
            )
            xai_summary = "RiskBand assessment completed with active monitoring."
            xai_summary_text = xai_summary
            dimension_readings = {}
            table_enrichments = {}
            lime_methodology_note = ""
            xai_narrative_text = xai_narrative
            xai_narrative_lines = [line.strip() for line in xai_narrative.split("\n") if line.strip()]
    else:
        xai_narrative, shap_ranked = build_domain_xai_narrative(
            feature_row=feature_row,
            pd_map=pd_map,
            conduct_reasons=feature_row.get("conduct_reasons", []),
        )
        xai_summary = "RiskBand assessment completed with active monitoring."
        xai_summary_text = xai_summary
        dimension_readings = {}
        table_enrichments = {}
        lime_methodology_note = ""
        xai_narrative_text = xai_narrative
        xai_narrative_lines = [line.strip() for line in xai_narrative.split("\n") if line.strip()]

    # -------------------------------------------------------------------------
    # Step 10: Assemble and return full output
    # -------------------------------------------------------------------------
    return {
        # Panel A — Engine identity (top of every response)
        "engine_name": ENGINE_NAME,
        "engine_short": ENGINE_SHORT,
        "engine_version": ENGINE_VERSION,
        "platform_name": PLATFORM_NAME,
        # Entity
        "entity_id": feature_row.get("entity_id"),
        # Panel A — Pralyon Risk Score + Credit Band
        "pralyon_risk_score": pd_map.governance_score,
        "governance_score": pd_map.governance_score,
        "pd_band": pd_map.pd_band,
        "band_before_override": pd_map.band_before_override,
        "data_penalty": pd_map.data_penalty,
        "legal_health_score": pd_map.legal_health_score,
        "override_flags": pd_map.override_flags,
        # Panel B — Default Probability + LGD + EL
        "blended_pd": round(blended_pd, 6),
        "model_pds": pds,
        "lgd_pred": lgd_pred,
        "el_pct": el_pct,
        "el_amount": el_amount,
        "credit_score": credit_score,
        # Panel C — 3-Anchor Limit Advisory (sanctioned limit)
        "advised_limit": evaluated_limit,
        "evaluated_limit": evaluated_limit,
        "evaluated_clean_limit": limit_result.get("evaluated_clean_limit", evaluated_limit),
        "base_limit": limit_result.get("base_limit"),
        "binding_anchor": limit_result.get("binding_anchor"),
        "all_anchors": limit_result.get("all_anchors"),
        "tenor_multiplier": limit_result.get("tenor_multiplier"),
        "tenor_bucket_days": limit_result.get("tenor_bucket_days"),
        "haircut_applied": limit_result.get("haircut_applied"),
        "volatility_haircut": limit_result.get("volatility_haircut"),
        "terms_vs_profile": limit_result.get("terms_vs_profile"),
        "recommended_tenor": limit_result.get("recommended_tenor_days") or limit_result.get("credit_period_days") or 30,
        "recommended_tenor_days": limit_result.get("recommended_tenor_days") or limit_result.get("credit_period_days") or 30,
        "advance_required": limit_result["advance_required"],
        "advance_pct_of_request": limit_result.get("advance_pct_of_request"),
        "advance_recommendation": limit_result.get("advance_recommendation"),
        "tenor_schedule": limit_result["tenor_schedule"],
        "tenor_note": limit_result["tenor_recommendation_note"],
        "tenor_recommendation_note": limit_result["tenor_recommendation_note"],
        "tenor_best_evaluated_days": limit_result.get("tenor_best_evaluated_days"),
        # Panel D — Financial Stress Test
        "stress_table": [vars(r) for r in stress_results],
        "stress_table_text": stress_table_text,
        # Panel E — XAI Explanation
        "xai_summary": xai_summary,
        "xai_summary_text": xai_summary_text,
        "dimension_readings": dimension_readings,
        "table_enrichments": table_enrichments,
        "lime_methodology_note": lime_methodology_note,
        "xai_narrative": xai_narrative,
        "xai_narrative_text": xai_narrative_text,
        "xai_narrative_lines": xai_narrative_lines,
        "shap_ranked": shap_ranked,
        "lime_explanation": lime_explanation,
        "reason_codes": pd_map.reason_codes,
        "xai_plot_paths": xai_plot_paths,
    }
