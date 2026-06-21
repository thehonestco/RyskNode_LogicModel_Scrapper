from __future__ import annotations

from typing import Any


def classify_mca_data_sufficiency(
    year1: dict[str, Any] | None, year2: dict[str, Any] | None, year3: dict[str, Any] | None
) -> str:
    required = ["revenue", "ebit", "total_debt", "networth", "receivables"]

    def ok(y: dict[str, Any] | None) -> bool:
        if not y:
            return False
        return all(y.get(k) is not None for k in required)

    y1_ok, y2_ok, y3_ok = ok(year1), ok(year2), ok(year3)
    if y1_ok and y2_ok and y3_ok:
        return "full"
    if y1_ok and y2_ok:
        return "partial"
    return "insufficient"


def maybe_switch_revenue_to_gst(
    mca_revenue: float | None, gst_turnover: float | None, threshold: float = 0.25
) -> tuple[str, list[str]]:
    notes: list[str] = []
    if mca_revenue is None or gst_turnover is None or gst_turnover == 0:
        return "mca", notes
    variance = abs(gst_turnover - mca_revenue) / gst_turnover
    if variance > threshold:
        notes.append("MCA_GST_REVENUE_MISMATCH")
        notes.append("REVENUE_FROM_GST_PROXY")
        return "gst_proxy", notes
    return "mca", notes
