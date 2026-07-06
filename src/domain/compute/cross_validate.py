"""
part1/compute/cross_validate.py
================================
Revenue cross-validation and MCA data sufficiency classification.

Per RA Model Documentation Section 3.2:
  Rule: If MCA revenue is MISSING OR MCA revenue < 50% of GST declared turnover
  Action: Use GST turnover as the revenue proxy for scoring purposes
  Field: revenue_source = "mca" or "gst_proxy"

Data Sufficiency:
  "full"        → Y1, Y2, Y3 all have required fields
  "partial"     → Y1 + Y2 have required fields (Y3 missing)
  "insufficient"→ Even Y1 is missing required fields
"""
from __future__ import annotations

from typing import Any


def classify_mca_data_sufficiency(
    year1: dict[str, Any] | None, year2: dict[str, Any] | None, year3: dict[str, Any] | None
) -> str:
    """
    Classify MCA financial data availability.
    Required fields for a year to be considered "ok": revenue, total_debt, networth.
    EBIT and receivables are optional — finanvo ratios can supplement those.
    """
    # Primary required fields — minimum for financial scoring
    required_primary = ["revenue", "total_debt", "networth"]
    # Secondary fields — needed for ratios
    required_secondary = ["current_assets", "current_liabilities"]

    def ok(y: dict[str, Any] | None) -> bool:
        if not y:
            return False
        # At least one primary field must have a positive value
        has_revenue = bool(y.get("revenue") and float(y.get("revenue", 0)) > 0)
        has_balance = any(
            y.get(k) and float(y.get(k, 0)) > 0
            for k in ["total_debt", "networth", "current_assets"]
        )
        return has_revenue or has_balance

    y1_ok, y2_ok, y3_ok = ok(year1), ok(year2), ok(year3)
    if y1_ok and y2_ok and y3_ok:
        return "full"
    if y1_ok and y2_ok:
        return "partial"
    if y1_ok:
        return "partial"  # At least Y1 is available
    return "insufficient"


def maybe_switch_revenue_to_gst(
    mca_revenue: float | None,
    gst_turnover: float | None,
    threshold: float = 0.50,  # Doc: "MCA < 50% of GST" → switch
) -> tuple[str, list[str]]:
    """
    Revenue source cross-validation per RA Model Section 3.2.

    Rules:
    1. If MCA revenue is None or 0 → use GST proxy (MCA data unavailable)
    2. If MCA revenue < 50% of GST turnover → use GST proxy (MCA severely understates)
    3. Otherwise → use MCA as primary source

    Returns: (revenue_source, notes_list)
    """
    notes: list[str] = []

    # Rule 1: MCA data missing entirely
    if not mca_revenue or mca_revenue <= 0:
        if gst_turnover and gst_turnover > 0:
            notes.append("MCA_REVENUE_MISSING_USING_GST_PROXY")
            return "gst_proxy", notes
        else:
            notes.append("REVENUE_DATA_UNAVAILABLE")
            return "mca", notes  # Both missing — return mca, revenue will be None

    # Rule 2: MCA < 50% of GST → MCA severely understates, use GST
    if gst_turnover and gst_turnover > 0:
        ratio = mca_revenue / gst_turnover
        if ratio < threshold:
            notes.append("MCA_REVENUE_BELOW_50PCT_OF_GST")
            notes.append("REVENUE_FROM_GST_PROXY")
            return "gst_proxy", notes
        # Also flag if there's a significant variance (> 25%) for transparency
        if abs(gst_turnover - mca_revenue) / gst_turnover > 0.25:
            notes.append("MCA_GST_REVENUE_VARIANCE_NOTE")

    return "mca", notes
