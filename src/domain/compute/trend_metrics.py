"""Trend metric computation — CAGR, business vintage, GST consistency."""

from typing import Optional
from datetime import date
from domain.schemas.normalized_record import NormalizedRecord
from domain.compute.safe_math import safe_divide


def compute_cagr(
    start_value: Optional[float],
    end_value: Optional[float],
    years: int,
) -> Optional[float]:
    """
    Compound Annual Growth Rate = (end / start) ^ (1 / years) - 1

    Returns None if inputs are invalid or start_value is non-positive.
    """
    if start_value is None or end_value is None or years <= 0:
        return None
    if start_value <= 0:
        return None
    try:
        cagr = (end_value / start_value) ** (1 / years) - 1
        return round(cagr, 6)
    except (ZeroDivisionError, ValueError):
        return None


def compute_business_vintage(incorporation_date: Optional[date]) -> Optional[float]:
    """Business vintage in decimal years from incorporation date to today."""
    if incorporation_date is None:
        return None
    delta = date.today() - incorporation_date
    return round(delta.days / 365.25, 2)


def compute_gst_filing_consistency(record: NormalizedRecord) -> Optional[float]:
    """GST filing consistency ratio = filed periods / total periods."""
    total = record.gst_filing_periods_total
    filed = record.gst_filing_periods_filed
    return safe_divide(filed, total)


def compute_all_trends(record: NormalizedRecord) -> dict:
    """Compute CAGR for turnover, revenue, net revenue + business vintage + GST consistency."""
    return {
        "business_vintage_years": compute_business_vintage(record.incorporation_date),
        "gst_filing_consistency_ratio": compute_gst_filing_consistency(record),
        "turnover_cagr_5y": compute_cagr(record.turnover_y5, record.turnover_y1, 4),
        "revenue_cagr_5y": compute_cagr(record.revenue_y5, record.revenue_y1, 4),
        "net_revenue_cagr_5y": compute_cagr(record.net_revenue_y5, record.net_revenue_y1, 4),
    }
