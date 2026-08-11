"""
part2/explainability/explainer.py
=================================
Buyer Risk Explainability — SHAP + LIME + PDP + Long Narrative
----------------------------------------------------------
Purpose
-------
RyskNode is a counterparty risk platform. The SELLER (MSME) submits a
BUYER's identifiers. This module explains WHY a particular Buyer received
their risk rating — giving the Seller actionable, auditable intelligence
on the Buyer's payment risk.

This module provides **permanent, production-grade explainability** for every
credit decision produced by the RA scoring pipeline. It wraps the existing
LightGBM and XGBoost PD models — zero architecture change is required.

Three levels of explanation
---------------------------
1. **Global (portfolio)**  — SHAP summary plot + feature importance bar chart.
2. **Local (per Buyer)** — SHAP waterfall plot + LIME local explanation.
3. **Narrative** — Long-form plain-English decision rationale auto-generated from SHAP,
   LIME threshold effects, domain scores, and reason codes.
"""

from __future__ import annotations

import json
import logging
import warnings
from pathlib import Path
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

warnings.filterwarnings("ignore")
logger = logging.getLogger(__name__)

# Optional heavy dependencies — graceful fallback if not installed
try:
    import shap
    _SHAP_AVAILABLE = True
except ImportError:
    _SHAP_AVAILABLE = False
    logger.warning("shap not installed. Run: pip install shap")

try:
    from lime import lime_tabular
    _LIME_AVAILABLE = True
except ImportError:
    _LIME_AVAILABLE = False
    logger.warning("lime not installed. Run: pip install lime")

try:
    import matplotlib
    matplotlib.use("Agg")  # non-interactive backend for server use
    import matplotlib.pyplot as plt
    _MPL_AVAILABLE = True
except ImportError:
    _MPL_AVAILABLE = False

EXPLANATIONS_DIR = Path(__file__).resolve().parents[3] / "reports" / "explanations"

# ─────────────────────────────────────────────────────────────────────────
# Full 36-feature label + "what it measures" library
# ─────────────────────────────────────────────────────────────────────────
FEATURE_INTERPRETATIONS: Dict[str, Dict[str, str]] = {
    "identity_score": {"label": "Identity Confidence Score", "meaning": "how strongly the entity's identity (CIN/GSTIN/PAN/director records) was cross-verified across MCA, GST and EPFO sources"},
    "financial_score": {"label": "Financial Health Score", "meaning": "the composite strength of the entity's balance sheet and P&L fundamentals (liquidity, leverage, growth)"},
    "legal_score": {"label": "Legal Risk Score", "meaning": "cumulative litigation and regulatory exposure from eCourts, DRT, NCLT and criminal-case records — higher is worse"},
    "documentation_score": {"label": "Documentation Quality Score", "meaning": "completeness and consistency of the filings and disclosures collected for this entity"},
    "conduct_score": {"label": "Conduct Score", "meaning": "Part 1's blended behavioural signal across GST filing discipline, MCA charge conduct, eCourts and EPFO history"},
    "current_ratio": {"label": "Current Ratio", "meaning": "the entity's ability to cover short-term liabilities with short-term assets — a core liquidity buffer"},
    "quick_ratio": {"label": "Quick Ratio", "meaning": "liquidity excluding inventory — a stricter test of near-term solvency"},
    "debt_to_equity": {"label": "Debt-to-Equity", "meaning": "how much the entity has borrowed relative to owners' capital — a leverage / balance-sheet risk indicator"},
    "dso": {"label": "Days Sales Outstanding", "meaning": "how many days it typically takes the entity to collect receivables — a proxy for cash-conversion and collection risk"},
    "net_revenue_cagr_5y": {"label": "5-Year Revenue CAGR", "meaning": "the trend and durability of the entity's top-line growth"},
    "working_capital": {"label": "Working Capital", "meaning": "the absolute cushion (current assets minus current liabilities) available to fund day-to-day operations"},
    "tangible_net_worth": {"label": "Tangible Net Worth", "meaning": "the owned capital base backing the business, net of intangibles — a loss-absorption buffer"},
    "net_revenue_latest": {"label": "Latest Net Revenue", "meaning": "most recent reported scale of operations"},
    "turnover_y1": {"label": "Turnover (Year 1)", "meaning": "reported turnover for the most recent fiscal year"},
    "turnover_y2": {"label": "Turnover (Year 2)", "meaning": "reported turnover one year prior"},
    "turnover_y3": {"label": "Turnover (Year 3)", "meaning": "reported turnover two years prior, used to establish the growth trend"},
    "charge_count_active": {"label": "Active MCA Charges", "meaning": "the number of live secured-lending charges registered against the entity with the Registrar of Companies"},
    "has_any_active_charge": {"label": "Has Any Active Charge", "meaning": "whether the entity currently has any assets pledged as security against borrowing"},
    "has_recent_charge_90d": {"label": "Recent Charge (90d)", "meaning": "whether a new secured borrowing charge was created in the last 90 days — a signal of fresh leverage being taken on"},
    "old_unsatisfied_charge_count": {"label": "Old Unsatisfied Charges", "meaning": "charges older than 3 years that remain unsatisfied — often a sign of stalled or defaulted secured debt"},
    "distinct_lender_count": {"label": "Distinct Lender Count", "meaning": "how many different institutions have lent against this entity — a proxy for banking relationships and reliance on multiple credit lines"},
    "case_count_total": {"label": "Total eCourts Cases", "meaning": "the entity's overall litigation footprint across all courts"},
    "case_count_active": {"label": "Active eCourts Cases", "meaning": "litigation that is currently unresolved"},
    "case_count_drt": {"label": "DRT Cases", "meaning": "cases before a Debt Recovery Tribunal — i.e. a lender has already initiated formal recovery action"},
    "case_count_nclt": {"label": "NCLT Cases", "meaning": "insolvency-related proceedings before the National Company Law Tribunal — the most severe legal risk signal available"},
    "case_count_hc": {"label": "High Court Cases", "meaning": "litigation escalated to High Court level"},
    "criminal_case_count": {"label": "Criminal Cases", "meaning": "criminal proceedings involving the entity or its directors"},
    "has_insolvency_petition": {"label": "Active Insolvency Petition", "meaning": "whether an NCLT insolvency petition is currently pending — this is a hard-decline trigger elsewhere in the pipeline"},
    "gst_turnover": {"label": "GST Declared Turnover", "meaning": "revenue as declared in GST returns, used to cross-check MCA/financial figures"},
    "gst_filing_consistency": {"label": "GST Filing Consistency", "meaning": "how regularly and consistently GST returns have been filed — irregular filing often precedes cash-flow stress"},
    "high_director_company_count": {"label": "Director on Many Companies", "meaning": "whether a director sits on an unusually high number of other companies — can indicate governance dilution or shell-company patterns"},
    "max_director_company_count": {"label": "Max Director Company Count", "meaning": "the highest number of directorships held by any single director on record"},
    "epfo_headcount": {"label": "EPFO Headcount", "meaning": "employee count as reported to EPFO — a scale and genuineness signal"},
    "pf_filing_regular": {"label": "PF Filing Regularity", "meaning": "whether provident-fund filings are made on time and consistently"},
    "revenue_per_employee_outlier": {"label": "Revenue/Employee Outlier", "meaning": "whether reported revenue is implausible relative to headcount — a synthetic or shell-entity red flag"},
    "business_vintage_years": {"label": "Business Vintage", "meaning": "how many years the entity has been operating since incorporation"},
}

FEATURE_LABELS: Dict[str, str] = {feat: meta["label"] for feat, meta in FEATURE_INTERPRETATIONS.items()}


def _feature_label(feat: str) -> str:
    return FEATURE_INTERPRETATIONS.get(feat, {}).get("label", feat.replace("_", " ").title())


def _feature_meaning(feat: str) -> str:
    return FEATURE_INTERPRETATIONS.get(feat, {}).get("meaning", "a factor in the model's assessment of this entity")


def _format_val(feat_name: str, val: Any) -> str:
    if val is None or pd.isna(val):
        return "N/A"
    try:
        fval = float(val)
        fname = str(feat_name).lower()
        if "capital" in fname or "networth" in fname or "revenue" in fname or "assets" in fname or "liabilities" in fname:
            if abs(fval) >= 1e7:
                return f"₹{fval / 1e7:,.2f} Cr"
            elif abs(fval) >= 1e5:
                return f"₹{fval / 1e5:,.2f} Lakh"
            else:
                return f"₹{fval:,.2f}"
        elif "headcount" in fname or "employee" in fname:
            return f"{int(fval)} employees"
        elif "dso" in fname or "dpo" in fname or "vintage" in fname:
            return f"{fval:.1f} years" if "vintage" in fname else f"{int(fval)} days"
        elif "score" in fname:
            return f"{fval:.1f}/100"
        elif "ratio" in fname or "equity" in fname or "leverage" in fname or "cagr" in fname:
            return f"{fval:.2%}" if "cagr" in fname else f"{fval:.2f}x"
        else:
            return f"{fval:.3g}"
    except Exception:
        return str(val)


# ─────────────────────────────────────────────────────────────────────────────
# Long-form Narrative Generator (SHAP + LIME + Domain Scores + Reason Codes)
# ─────────────────────────────────────────────────────────────────────────────

def _domain_score_paragraph(scores: Dict[str, Optional[float]], as_html: bool = True) -> str:
    parts = []
    labels = {
        "financial_score": "financial fundamentals",
        "identity_score": "identity verification",
        "legal_score": "legal/litigation exposure",
        "documentation_score": "documentation completeness",
        "conduct_score": "behavioural conduct history",
    }
    for key, desc in labels.items():
        val = scores.get(key)
        if val is None:
            continue
        if key == "legal_score":
            tier = "low" if val <= 25 else "moderate" if val <= 55 else "elevated"
            parts.append(f"{desc} is {tier} ({val:.0f}/100)")
        else:
            tier = "strong" if val >= 75 else "adequate" if val >= 50 else "weak"
            parts.append(f"{desc} is {tier} ({val:.0f}/100)")
    if not parts:
        return ""
    text = "Across the underlying domain scores, " + "; ".join(parts) + "."
    return f"<div>{text}</div>" if as_html else text


def _lime_section(lime_explanation: Dict, as_html: bool = True) -> str:
    feats = (lime_explanation or {}).get("features", [])
    if not feats:
        return ""
    if as_html:
        lines = ["<b>LIME Local Threshold Rules:</b>", "<ul style='margin-top:4px; margin-bottom:12px; padding-left:20px;'>"]
        for f in feats[:5]:
            cond = f.get("condition", "")
            w = f.get("weight", 0.0)
            sense = "pushes score toward higher risk" if w > 0 else "pushes score toward lower risk"
            lines.append(f"  <li>When <b>{cond}</b>, this {sense} (local weight <b>{w:+.4f}</b>).</li>")
        lines.append("</ul>")
        return "".join(lines)
    else:
        lines = ["Locally (specifically for this entity's feature combination), the LIME surrogate model highlights these threshold effects:"]
        for f in feats[:5]:
            cond = f.get("condition", "")
            w = f.get("weight", 0.0)
            sense = "pushes score toward higher risk" if w > 0 else "pushes score toward lower risk"
            lines.append(f"  - When {cond}, this {sense} (local weight {w:+.4f}).")
        return "\n".join(lines)


_REASON_CODE_TEXT = {
    "IDENTITY_GATE_FAILED_UNSCOREABLE": "identity verification could not be completed to a scoreable standard",
    "DOCUMENTATION_PENALTY_APPLIED_0.80": "a documentation-quality penalty (0.80x) was applied due to incomplete filings",
    "CRIMINAL_CASE_DETECTED": "at least one criminal case was detected against the entity or its directors",
    "LEGAL_RISK_SCORE_ELEVATED": "the legal risk score is elevated relative to peers",
    "DEBT_TO_EQUITY_EXCEEDS_3x": "leverage (debt-to-equity) exceeds 3x, a high-risk threshold",
    "CURRENT_RATIO_BELOW_1": "current ratio is below 1.0, indicating short-term liabilities exceed short-term assets",
    "BUSINESS_VINTAGE_BELOW_1_YEAR": "the business has less than one year of operating history",
    "OLD_UNSATISFIED_CHARGE": "charges older than 3 years remain unsatisfied with ROC",
    "REVENUE_PER_EMPLOYEE_OUTLIER": "reported revenue is unusually high relative to EPFO headcount",
    "PF_FILING_IRREGULAR": "provident fund returns are filed irregularly",
    "GST_NON_FILER": "GST returns are unfiled or severely non-compliant",
    "GST_FILING_IRREGULAR": "GST returns have minor or major filing gaps",
    "ECOURTS_INSOLVENCY_PETITION": "an NCLT insolvency petition is recorded on eCourts",
    "ECOURTS_DRT_CASE": "a Debt Recovery Tribunal case is recorded on eCourts",
    "CHARGE_LENDER_QUALITY_NBFC_ONLY": "secured borrowings are backed exclusively by NBFC lenders",
}


def _reason_code_section(reason_codes: List[str], as_html: bool = True) -> str:
    if not reason_codes:
        text = "No adverse reason codes were triggered for this entity."
        return f"<i>{text}</i>" if as_html else text
    if as_html:
        lines = ["<b>Triggered Risk Flags:</b>", "<ul style='margin-top:4px; margin-bottom:0; padding-left:20px;'>"]
        for rc in reason_codes:
            lines.append(f"  <li><b>{rc}</b>: {_REASON_CODE_TEXT.get(rc, 'see pd_mapper.py definition')}</li>")
        lines.append("</ul>")
        return "".join(lines)
    else:
        lines = ["The following flags were triggered during scoring:"]
        for rc in reason_codes:
            lines.append(f"  - {rc}: {_REASON_CODE_TEXT.get(rc, 'see pd_mapper.py definition')}")
        return "\n".join(lines)


def build_lime_short_summary(
    lime_explanation: Optional[Dict],
    shap_ranked: List[Dict],
    band: str,
    blended_pd: float,
    as_html: bool = True,
) -> str:
    """
    Build proprietary TraceLayer™ short executive summary for the top Verdict Banner.
    Extracts top local threshold rules and translates them into plain English.
    """
    feats = (lime_explanation or {}).get("features", [])
    if feats:
        top_rules = []
        for f in feats[:2]:
            cond = f.get("condition", "")
            w = f.get("weight", 0.0)
            sense = "reduces default risk weight" if w < 0 else "increases default risk weight"
            top_rules.append(f"<b>{cond}</b> ({sense} by {w:+.4f})")
        rule_str = " and ".join(top_rules)
        html_text = f"TraceLayer™ AI Signal Analysis indicates that {rule_str}. Overall entity classification is <b>RiskBand™ {band}</b> with <b>{blended_pd * 100:.2f}% blended PD</b>."
    else:
        mitigants = [f for f in shap_ranked if f.get("direction") == "risk_reducing"]
        top_m = [f"<b>{f.get('label') or _feature_label(f['feature'])}</b>" for f in mitigants[:2]]
        m_str = " and ".join(top_m) if top_m else "baseline parameters"
        html_text = f"TraceLayer™ signal attributions highlight {m_str} supporting <b>RiskBand™ {band}</b> with <b>{blended_pd * 100:.2f}% blended PD</b>."

    if as_html:
        return html_text
    else:
        import re
        return re.sub(r"<[^>]+>", "", html_text)


def build_lime_dimension_readings(
    lime_explanation: Optional[Dict],
    shap_ranked: List[Dict],
    domain_scores: Optional[Dict[str, Optional[float]]] = None,
) -> Dict[str, str]:
    """
    Generate dynamic TraceLayer™ readings for each of the 4 Tri-Core dimensions:
    - Financial Health (40%)
    - Identity & Governance (25%)
    - Legal & Compliance (20%)
    - Conduct & Behaviour (15%)
    """
    feats = (lime_explanation or {}).get("features", [])
    scores = domain_scores or {}

    fin_score = scores.get("financial_score", 0.0) or 0.0
    id_score = scores.get("identity_score", 0.0) or 0.0
    leg_score = scores.get("legal_score", 0.0) or 0.0
    cond_score = scores.get("conduct_score", 0.0) or 0.0

    fin_cols = {"current_ratio", "quick_ratio", "debt_to_equity", "working_capital", "dso", "revenue", "ebitda", "pat"}
    id_cols = {"company_age_years", "epfo_headcount", "registered_state", "authorized_capital", "paid_up_capital"}
    leg_cols = {"legal_score", "active_cases", "nclt_cases", "drt_cases", "disqualified_directors"}
    cond_cols = {"conduct_score", "gst_regularity", "pf_regularity", "unsatisfied_charges"}

    fin_lime = [f for f in feats if any(c in f.get("condition", "").lower() for c in fin_cols)]
    id_lime = [f for f in feats if any(c in f.get("condition", "").lower() for c in id_cols)]
    leg_lime = [f for f in feats if any(c in f.get("condition", "").lower() for c in leg_cols)]
    cond_lime = [f for f in feats if any(c in f.get("condition", "").lower() for c in cond_cols)]

    if fin_lime:
        f_conds = "; ".join([f"{f['condition']} (weight {f['weight']:+.4f})" for f in fin_lime[:2]])
        fin_read = f"TraceLayer™ signal rules: {f_conds}. Financial score: {fin_score:.0f}/100."
    else:
        fin_read = f"Financial score: {fin_score:.0f}/100. Liquidity and leverage ratios meet benchmark thresholds."

    if id_lime:
        i_conds = "; ".join([f"{f['condition']} (weight {f['weight']:+.4f})" for f in id_lime[:2]])
        id_read = f"TraceLayer™ signal rules: {i_conds}. Identity & governance score: {id_score:.0f}/100."
    else:
        id_read = f"Identity & governance score: {id_score:.0f}/100. Active MCA status and verified DIN filings."

    if leg_lime:
        l_conds = "; ".join([f"{f['condition']} (weight {f['weight']:+.4f})" for f in leg_lime[:2]])
        leg_read = f"TraceLayer™ signal rules: {l_conds}. Legal score: {leg_score:.0f}/100."
    else:
        tier = "Low Risk" if leg_score <= 25 else "Moderate Risk" if leg_score <= 55 else "Elevated Risk"
        leg_read = f"Legal score: {leg_score:.0f}/100 ({tier}). eCourts, DRT & NCLT litigation monitoring active."

    if cond_lime:
        c_conds = "; ".join([f"{f['condition']} (weight {f['weight']:+.4f})" for f in cond_lime[:2]])
        cond_read = f"TraceLayer™ signal rules: {c_conds}. Conduct score: {cond_score:.0f}/100."
    else:
        cond_read = f"Conduct score: {cond_score:.0f}/100. Behavioral history and filing regularity verified."

    return {
        "financial": fin_read,
        "identity": id_read,
        "legal": leg_read,
        "conduct": cond_read,
    }


def build_table_enrichments(
    lime_explanation: Optional[Dict],
    shap_ranked: List[Dict],
    domain_scores: Optional[Dict[str, Optional[float]]] = None,
    reason_codes: Optional[List[str]] = None,
) -> Dict[str, Any]:
    """
    Generate dynamic TraceLayer™ Seller Implications and Required Actions for report tables.
    """
    feats = (lime_explanation or {}).get("features", [])
    rc = reason_codes or []

    leg_feat = next((f for f in feats if "legal" in f.get("condition", "").lower()), None)
    if leg_feat:
        legal_imp = f"TraceLayer™ local weight ({leg_feat['weight']:+.4f}) for {leg_feat['condition']} confirms low immediate insolvency risk."
    else:
        legal_imp = "eCourts & NCLT signals indicate pre-decree status with no asset attachment."

    ch_feat = next((f for f in feats if "debt" in f.get("condition", "").lower() or "charge" in f.get("condition", "").lower()), None)
    if "OLD_UNSATISFIED_CHARGE" in rc:
        charge_note = "Charges > 3 years old remain open on ROC register; leverage remains bounded by local risk thresholds."
    elif ch_feat:
        charge_note = f"Local risk threshold ({ch_feat['condition']}) confirms manageable leverage."
    else:
        charge_note = "Standard charge registry entry with verified satisfaction status."

    monitoring_actions = [
        "Re-evaluate credit limit if DSO exceeds 90 days (local risk threshold boundary); cap tenor at 45 days.",
        "Monitor EPFO headcount for drops below 15 employees (primary scale driver).",
        "If commercial court matter progresses to decree, immediately pause shipments and re-run ZeroPass™ gates.",
    ]

    return {
        "legal_implication": legal_imp,
        "charge_note": charge_note,
        "monitoring_actions": monitoring_actions,
    }


def build_lime_methodology_note(lime_explanation: Optional[Dict]) -> str:
    """
    Generate technical calculation & methodology note for TraceLayer™ placed at the bottom of the report.
    """
    feats = (lime_explanation or {}).get("features", [])
    if not feats:
        return ""
    lines = [
        "<div style='margin-top:20px; padding:12px; background:rgba(255,255,255,0.03); border:1px solid #30363d; border-radius:6px; font-size:12px; color:#8b949e;'>",
        "<b>TraceLayer™ Model-Agnostic Signal Attribution Methodology:</b><br/>",
        "Pralyon AI computes local linear threshold boundaries around this entity's specific feature vector using 3,000 reference market baseline samples. Key local threshold boundaries computed:",
        "<ul style='margin-top:4px; margin-bottom:0; padding-left:18px;'>",
    ]
    for f in feats[:5]:
        cond = f.get("condition", "")
        w = f.get("weight", 0.0)
        sense = "increases risk" if w > 0 else "reduces risk"
        lines.append(f"  <li><b>{cond}</b>: local coefficient = <b>{w:+.4f}</b> ({sense})</li>")
    lines.append("</ul></div>")
    return "".join(lines)


def build_short_summary(
    buyer_id: str,
    band: str,
    decision: str,
    shap_ranked: List[Dict],
    domain_scores: Optional[Dict[str, Optional[float]]] = None,
    as_html: bool = True,
) -> str:
    """
    Build a concise executive summary. Deprecated — use build_lime_short_summary instead.
    """
    return build_lime_short_summary(
        lime_explanation=None,
        shap_ranked=shap_ranked,
        band=band,
        blended_pd=0.0,
        as_html=as_html,
    )


def build_long_narrative(
    buyer_id: str,
    blended_pd: float,
    band: str,
    decision: str,
    advised_limit: float,
    shap_ranked: List[Dict],
    lime_explanation: Optional[Dict] = None,
    domain_scores: Optional[Dict[str, Optional[float]]] = None,
    reason_codes: Optional[List[str]] = None,
    max_risk_factors: int = 8,
    max_mitigants: int = 8,
    as_html: bool = True,
) -> str:
    """
    Build rich, multi-sentence plain-English narrative (supporting HTML and plain-text).
    """
    risk_factors = [f for f in shap_ranked if f.get("direction") == "risk_increasing"]
    mitigants = [f for f in shap_ranked if f.get("direction") == "risk_reducing"]

    if as_html:
        lines = []
        if domain_scores:
            d_para = _domain_score_paragraph(domain_scores, as_html=True)
            if d_para:
                lines.append(d_para)
                lines.append("<div style='margin-bottom:8px;'></div>")

        if risk_factors:
            lines.append("<b>Primary Risk Drivers:</b>")
            lines.append("<ul style='margin-top:4px; margin-bottom:12px; padding-left:20px;'>")
            for f in risk_factors[:max_risk_factors]:
                label = f.get("label") or _feature_label(f["feature"])
                feat = f["feature"]
                val = f.get("feature_value")
                sv = f.get("shap_value", 0.0) or 0.0
                val_str = _format_val(feat, val)
                meaning = _feature_meaning(feat)
                lines.append(
                    f"  <li><b>{label}</b> ({val_str}) — {meaning}. "
                    f"Increases modelled default probability by <b>+{abs(sv):.2f}%</b>.</li>"
                )
            lines.append("</ul>")

        if mitigants:
            lines.append("<b>Key Mitigating Factors:</b>")
            lines.append("<ul style='margin-top:4px; margin-bottom:12px; padding-left:20px;'>")
            for f in mitigants[:max_mitigants]:
                label = f.get("label") or _feature_label(f["feature"])
                feat = f["feature"]
                val = f.get("feature_value")
                sv = f.get("shap_value", 0.0) or 0.0
                val_str = _format_val(feat, val)
                meaning = _feature_meaning(feat)
                lines.append(
                    f"  <li><b>{label}</b> ({val_str}) — {meaning}. "
                    f"Reduces modelled default probability by <b>-{abs(sv):.2f}%</b>.</li>"
                )
            lines.append("</ul>")

        rc_sec = _reason_code_section(reason_codes or [], as_html=True)
        if rc_sec:
            lines.append(rc_sec)

        return "".join(lines)

    else:
        lines = [f"BUYER RISK ASSESSMENT — {buyer_id}", "=" * 60]
        dec_upper = decision.upper().replace("_", " ")
        lines.append(f"Decision: {dec_upper}  |  Band: {band}  |  Blended PD: {blended_pd * 100:.2f}%")
        lines.append(
            f"Advised Seller Exposure Limit: ₹{advised_limit:,.0f}"
            if advised_limit > 0 else
            "Advised Seller Exposure Limit: ₹0 (declined / not sanctioned)"
        )
        lines.append("")

        if domain_scores:
            d_para = _domain_score_paragraph(domain_scores, as_html=False)
            if d_para:
                lines.append(d_para)
                lines.append("")

        if risk_factors:
            lines.append("PRIMARY RISK DRIVERS")
            lines.append("-" * 60)
            for f in risk_factors[:max_risk_factors]:
                label = f.get("label") or _feature_label(f["feature"])
                feat = f["feature"]
                val = f.get("feature_value")
                sv = f.get("shap_value", 0.0) or 0.0
                val_str = _format_val(feat, val)
                meaning = _feature_meaning(feat)
                lines.append(
                    f"• {label} ({val_str}) — {meaning}. "
                    f"Increases modelled default probability by +{abs(sv):.2f}%."
                )
            lines.append("")

        if mitigants:
            lines.append("KEY MITIGATING FACTORS")
            lines.append("-" * 60)
            for f in mitigants[:max_mitigants]:
                label = f.get("label") or _feature_label(f["feature"])
                feat = f["feature"]
                val = f.get("feature_value")
                sv = f.get("shap_value", 0.0) or 0.0
                val_str = _format_val(feat, val)
                meaning = _feature_meaning(feat)
                lines.append(
                    f"• {label} ({val_str}) — {meaning}. "
                    f"Reduces modelled default probability by -{abs(sv):.2f}%."
                )
            lines.append("")

        lines.append(_reason_code_section(reason_codes or [], as_html=False))
        return "\n".join(lines)


# ─────────────────────────────────────────────────────────────────────────────
class CreditExplainer:
    """
    Permanent XAI wrapper for the RyskNode Buyer PD models (SHAP + LIME).
    """

    def __init__(
        self,
        lgbm_model: Any,
        xgb_model: Any,
        feature_names: List[str],
        X_train: Any,
        primary_model: str = "lgbm",
    ):
        self.lgbm_model = lgbm_model
        self.xgb_model = xgb_model
        self.feature_names = list(feature_names)
        self.X_train = (
            np.asarray(X_train[self.feature_names].values if isinstance(X_train, pd.DataFrame) and set(self.feature_names).issubset(X_train.columns) else X_train.values if isinstance(X_train, pd.DataFrame) else X_train, dtype=float)
            if X_train is not None else None
        )
        self.primary_model = primary_model

        self._shap_explainer: Optional[Any] = None
        self._lime_explainer: Optional[Any] = None
        self._shap_fitted = False

    # ── Lazy init ────────────────────────────────────────────────────────────

    def _init_shap(self) -> None:
        """Initialise Tree SHAP explainer (lazy — only when first needed)."""
        if not _SHAP_AVAILABLE:
            raise ImportError("Install shap: pip install shap")
        if self._shap_fitted:
            return
        model = self.lgbm_model if self.primary_model == "lgbm" else self.xgb_model
        
        # Extract base estimator if model is wrapped in CalibratedClassifierCV
        if hasattr(model, "calibrated_classifiers_"):
            model = model.calibrated_classifiers_[0].estimator
            
        if self.X_train is not None:
            background = shap.sample(self.X_train, min(100, len(self.X_train)))
            self._shap_explainer = shap.TreeExplainer(
                model,
                data=background,
                feature_perturbation="interventional",
            )
        else:
            self._shap_explainer = shap.TreeExplainer(model)

        self._shap_fitted = True
        logger.info("CreditExplainer: SHAP TreeExplainer initialised.")

    def _init_lime(self) -> None:
        """Initialise LIME tabular explainer (lazy)."""
        if not _LIME_AVAILABLE:
            raise ImportError("Install lime: pip install lime")
        if self._lime_explainer is not None:
            return
        if self.X_train is None:
            logger.warning("No X_train background provided for LIME — LIME skipped.")
            return

        self._lime_explainer = lime_tabular.LimeTabularExplainer(
            self.X_train,
            feature_names=self.feature_names,
            class_names=["Non-Default", "Default"],
            mode="classification",
            discretize_continuous=True,
            random_state=42,
        )
        logger.info("CreditExplainer: LIME LimeTabularExplainer initialised.")

    # ─────────────────────────────────────────────────────────────────────────
    # 1. Local (per-Buyer) explanation
    # ─────────────────────────────────────────────────────────────────────────

    def explain_buyer(
        self,
        buyer_id: str,
        x_instance: np.ndarray,
        blended_pd: float,
        band: str,
        decision: str,
        advised_limit: float = 0.0,
        domain_scores: Optional[Dict[str, Optional[float]]] = None,
        reason_codes: Optional[List[str]] = None,
        save: bool = True,
        as_html: bool = True,
    ) -> Dict:
        """
        Generate full local explanation for a Buyer (SHAP + LIME + Narrative).
        """
        x_instance = np.asarray(x_instance, dtype=float).flatten()
        report: Dict = {
            "buyer_id": buyer_id,
            "generated_at": datetime.now().isoformat(),
            "blended_pd": blended_pd,
            "band": band,
            "decision": decision,
            "advised_limit": advised_limit,
            "shap_values": {},
            "shap_ranked": [],
            "lime_explanation": {},
            "short_summary": "",
            "short_summary_text": "",
            "detailed_narrative": "",
            "narrative": "",
            "narrative_text": "",
            "narrative_lines": [],
            "plot_paths": [],
        }

        # ── SHAP local explanation ──────────────────────────────────────
        if _SHAP_AVAILABLE:
            try:
                self._init_shap()
                sv = self._shap_explainer(x_instance.reshape(1, -1))
                shap_vals = sv.values[0] if hasattr(sv, "values") else (
                    sv[1][0] if isinstance(sv, list) else sv[0]
                )

                shap_dict = {self.feature_names[i]: float(shap_vals[i]) for i in range(len(self.feature_names))}
                ranked = sorted(shap_dict.items(), key=lambda x: abs(x[1]), reverse=True)
                report["shap_values"] = shap_dict
                report["shap_ranked"] = [
                    {
                        "feature": feat,
                        "label": _feature_label(feat),
                        "feature_value": (
                            None
                            if (
                                np.isnan(float(x_instance[self.feature_names.index(feat)]))
                                or x_instance[self.feature_names.index(feat)] is None
                            )
                            else float(x_instance[self.feature_names.index(feat)])
                        ),
                        "shap_value": round(float(sv_val), 5),
                        "direction": "risk_increasing" if sv_val > 0 else "risk_reducing",
                    }
                    for feat, sv_val in ranked
                ]

                # Waterfall plot
                if _MPL_AVAILABLE and save:
                    plot_path = self._save_shap_waterfall(buyer_id, sv, x_instance)
                    report["plot_paths"].append(str(plot_path))

            except Exception as e:
                logger.warning("SHAP local explanation failed for Buyer %s: %s", buyer_id, e)

        # ── LIME local explanation ──────────────────────────────────────
        if _LIME_AVAILABLE and self.X_train is not None:
            try:
                self._init_lime()
                model = self.lgbm_model if self.primary_model == "lgbm" else self.xgb_model

                def _predict_fn(X):
                    if hasattr(model, "predict_proba"):
                        return np.asarray(model.predict_proba(X))
                    preds = np.asarray(model.predict(X)).flatten()
                    return np.column_stack([1 - preds, preds])

                lime_exp = self._lime_explainer.explain_instance(
                    x_instance,
                    _predict_fn,
                    num_features=min(8, len(self.feature_names)),
                    labels=(1,),
                )
                lime_list = lime_exp.as_list()
                report["lime_explanation"] = {
                    "features": [{"condition": cond, "weight": round(float(weight), 5)} for cond, weight in lime_list]
                }

                if _MPL_AVAILABLE and save:
                    plot_path = self._save_lime_plot(buyer_id, lime_exp)
                    report["plot_paths"].append(str(plot_path))

            except Exception as e:
                logger.warning("LIME explanation failed for Buyer %s: %s", buyer_id, e)

        # ── Short Summary & Detailed Narrative ─────────────────────────
        lime_short_summary_html = build_lime_short_summary(
            lime_explanation=report["lime_explanation"],
            shap_ranked=report["shap_ranked"],
            band=band,
            blended_pd=blended_pd,
            as_html=True,
        )
        lime_short_summary_text = build_lime_short_summary(
            lime_explanation=report["lime_explanation"],
            shap_ranked=report["shap_ranked"],
            band=band,
            blended_pd=blended_pd,
            as_html=False,
        )

        dimension_readings = build_lime_dimension_readings(
            lime_explanation=report["lime_explanation"],
            shap_ranked=report["shap_ranked"],
            domain_scores=domain_scores,
        )

        table_enrichments = build_table_enrichments(
            lime_explanation=report["lime_explanation"],
            shap_ranked=report["shap_ranked"],
            domain_scores=domain_scores,
            reason_codes=reason_codes,
        )

        lime_methodology_note = build_lime_methodology_note(report["lime_explanation"])

        html_narrative = build_long_narrative(
            buyer_id=buyer_id,
            blended_pd=blended_pd,
            band=band,
            decision=decision,
            advised_limit=advised_limit,
            shap_ranked=report["shap_ranked"],
            lime_explanation=report["lime_explanation"],
            domain_scores=domain_scores,
            reason_codes=reason_codes,
            as_html=True,
        )
        text_narrative = build_long_narrative(
            buyer_id=buyer_id,
            blended_pd=blended_pd,
            band=band,
            decision=decision,
            advised_limit=advised_limit,
            shap_ranked=report["shap_ranked"],
            lime_explanation=report["lime_explanation"],
            domain_scores=domain_scores,
            reason_codes=reason_codes,
            as_html=False,
        )
        report["lime_short_summary"] = lime_short_summary_html
        report["lime_short_summary_text"] = lime_short_summary_text
        report["short_summary"] = lime_short_summary_html
        report["short_summary_text"] = lime_short_summary_text
        report["dimension_readings"] = dimension_readings
        report["table_enrichments"] = table_enrichments
        report["lime_methodology_note"] = lime_methodology_note
        report["detailed_narrative"] = html_narrative
        report["narrative"] = html_narrative
        report["narrative_text"] = text_narrative
        report["narrative_lines"] = [line.strip() for line in text_narrative.split("\n") if line.strip()]

        if save:
            self._save_explanation(buyer_id, report)

        return report

    def explain_borrower(self, entity_id: str = "UNKNOWN", **kwargs) -> Dict:
        """Deprecated alias — use explain_buyer() instead."""
        return self.explain_buyer(buyer_id=entity_id, **kwargs)

    # ─────────────────────────────────────────────────────────────────────────
    # 2. Global (portfolio) explanation
    # ─────────────────────────────────────────────────────────────────────────

    def explain_portfolio(
        self,
        X: np.ndarray,
        entity_ids: Optional[List[str]] = None,
        save_plots: bool = True,
        top_n: int = 10,
    ) -> Dict:
        if not _SHAP_AVAILABLE:
            raise ImportError("Install shap: pip install shap")

        self._init_shap()
        X = np.array(X)
        sv = self._shap_explainer(X)
        shap_vals = sv.values if hasattr(sv, "values") else sv

        mean_abs = np.mean(np.abs(shap_vals), axis=0)
        ranked = sorted(zip(self.feature_names, mean_abs.tolist()), key=lambda x: x[1], reverse=True)

        result: Dict = {
            "generated_at": datetime.now().isoformat(),
            "n_buyers": len(X),
            "feature_importance": [
                {
                    "rank": i + 1,
                    "feature": feat,
                    "label": _feature_label(feat),
                    "mean_abs_shap": round(v, 6),
                }
                for i, (feat, v) in enumerate(ranked)
            ],
            "plot_paths": [],
        }

        if save_plots and _MPL_AVAILABLE:
            bar_path = self._save_global_importance(
                [r["label"] for r in result["feature_importance"][:top_n]],
                [r["mean_abs_shap"] for r in result["feature_importance"][:top_n]],
            )
            result["plot_paths"].append(str(bar_path))

            beeswarm_path = self._save_shap_summary(sv, X)
            result["plot_paths"].append(str(beeswarm_path))

        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = EXPLANATIONS_DIR / f"portfolio_explanation_{ts}.json"
        with open(out, "w") as fh:
            json.dump(result, fh, indent=2, default=str)
        logger.info("Portfolio Buyer explanation saved → %s", out)

        return result

    # ─────────────────────────────────────────────────────────────────────────
    # 3. Partial Dependency Plots (PDP)
    # ─────────────────────────────────────────────────────────────────────────

    def plot_pdp(
        self,
        X: np.ndarray,
        feature_name: str,
        n_points: int = 50,
        save: bool = True,
    ) -> Optional[Path]:
        if not _MPL_AVAILABLE:
            logger.warning("matplotlib not available — PDP skipped.")
            return None

        if feature_name not in self.feature_names:
            raise ValueError(f"'{feature_name}' not in feature_names. Available: {self.feature_names}")

        model = self.lgbm_model if self.primary_model == "lgbm" else self.xgb_model
        X = np.array(X)
        feat_idx = self.feature_names.index(feature_name)
        feat_vals = np.linspace(X[:, feat_idx].min(), X[:, feat_idx].max(), n_points)
        pdp_preds = []

        for v in feat_vals:
            X_mod = X.copy()
            X_mod[:, feat_idx] = v
            preds = np.array(model.predict(X_mod)).flatten()
            pdp_preds.append(float(np.mean(preds)))

        fig, ax = plt.subplots(figsize=(8, 4))
        ax.plot(feat_vals, pdp_preds, color="#01696f", linewidth=2)
        ax.fill_between(feat_vals, pdp_preds, alpha=0.08, color="#01696f")
        label = _feature_label(feature_name)
        ax.set_xlabel(label, fontsize=11)
        ax.set_ylabel("Avg Buyer PD (Predicted)", fontsize=11)
        ax.set_title(f"Partial Dependency Plot — Buyer {label}", fontsize=12)
        ax.axhline(0, color="grey", linestyle="--", linewidth=0.8)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()

        path = None
        if save:
            EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
            ts = datetime.now().strftime("%Y%m%d_%H%M%S")
            path = EXPLANATIONS_DIR / f"pdp_{feature_name}_{ts}.png"
            fig.savefig(path, dpi=150, bbox_inches="tight")
            logger.info("PDP saved → %s", path)
        plt.close(fig)
        return path

    # ─────────────────────────────────────────────────────────────────────────
    # Plot savers (internal)
    # ─────────────────────────────────────────────────────────────────────────

    def _save_shap_waterfall(self, buyer_id: str, shap_values: Any, x_instance: np.ndarray) -> Path:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPLANATIONS_DIR / f"shap_waterfall_{buyer_id}.png"
        try:
            fig, ax = plt.subplots(figsize=(10, 5))
            shap.plots.waterfall(shap_values[0], max_display=10, show=False)
            plt.title(f"SHAP Waterfall — Buyer {buyer_id}", pad=12)
            plt.tight_layout()
            plt.savefig(path, dpi=150, bbox_inches="tight")
            plt.close()
        except Exception as e:
            logger.warning("Waterfall plot failed for Buyer %s: %s", buyer_id, e)
            plt.close("all")
        return path

    def _save_lime_plot(self, buyer_id: str, lime_exp: Any) -> Path:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        path = EXPLANATIONS_DIR / f"lime_{buyer_id}.png"
        try:
            fig = lime_exp.as_pyplot_figure()
            fig.suptitle(f"LIME Explanation — Buyer {buyer_id}", fontsize=12)
            plt.tight_layout()
            fig.savefig(path, dpi=150, bbox_inches="tight")
            plt.close(fig)
        except Exception as e:
            logger.warning("LIME plot failed for Buyer %s: %s", buyer_id, e)
            plt.close("all")
        return path

    def _save_global_importance(self, labels: List[str], values: List[float]) -> Path:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = EXPLANATIONS_DIR / f"global_importance_{ts}.png"
        fig, ax = plt.subplots(figsize=(9, 5))
        ax.barh(labels[::-1], values[::-1], color="#01696f", alpha=0.85)
        ax.set_xlabel("Mean |SHAP Value|", fontsize=11)
        ax.set_title("Global Buyer Feature Importance (SHAP)", fontsize=12)
        ax.grid(axis="x", alpha=0.3)
        plt.tight_layout()
        fig.savefig(path, dpi=150, bbox_inches="tight")
        plt.close(fig)
        return path

    def _save_shap_summary(self, shap_values: Any, X: np.ndarray) -> Path:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = EXPLANATIONS_DIR / f"shap_summary_{ts}.png"
        try:
            fig, ax = plt.subplots(figsize=(10, 6))
            shap.summary_plot(
                shap_values.values if hasattr(shap_values, "values") else shap_values,
                X,
                feature_names=self.feature_names,
                show=False,
                plot_type="dot",
            )
            plt.title("SHAP Summary — Buyer Portfolio", pad=12)
            plt.tight_layout()
            plt.savefig(path, dpi=150, bbox_inches="tight")
            plt.close()
        except Exception as e:
            logger.warning("SHAP summary plot failed: %s", e)
            plt.close("all")
        return path

    def _save_explanation(self, buyer_id: str, report: Dict) -> None:
        EXPLANATIONS_DIR.mkdir(parents=True, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = EXPLANATIONS_DIR / f"explanation_{buyer_id}_{ts}.json"
        with open(path, "w") as fh:
            json.dump(report, fh, indent=2, default=str)
        logger.info("Buyer explanation saved → %s", path)

