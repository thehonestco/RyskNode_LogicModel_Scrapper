from __future__ import annotations

from typing import Any


SECTOR_REVENUE_PER_EMPLOYEE_NORM: dict[str, float] = {
    "TRADING":       500.0,   # ₹L per employee
    "MANUFACTURING": 200.0,
    "SERVICES":      150.0,
    "CONSTRUCTION":  180.0,
    "DEFAULT":       200.0,
}


def derive_epfo_conduct_signals(
    epfo_raw: dict[str, Any] | None,
    revenue: float | None,
    sector_bucket: str | None,
) -> dict[str, Any]:
    """
    Derive headcount and PF compliance signals from EPFO data.
    These feed Part 1 conduct scorecard only.
    """
    if not epfo_raw:
        return {
            "epfo_headcount": None,
            "pf_filing_regular": None,
            "revenue_per_employee_outlier": False,
            "source_notes": ["EPFO_DATA_UNAVAILABLE"],
        }

    headcount: int | None = epfo_raw.get("employee_count") or epfo_raw.get("headcount")
    pf_regular: bool | None = epfo_raw.get("pf_filing_regular")
    notes: list[str] = []
    outlier = False

    if headcount and revenue:
        norm = SECTOR_REVENUE_PER_EMPLOYEE_NORM.get(
            (sector_bucket or "").upper(),
            SECTOR_REVENUE_PER_EMPLOYEE_NORM["DEFAULT"],
        )
        rev_per_emp = revenue / headcount
        if rev_per_emp > norm * 5:
            outlier = True
            notes.append("REVENUE_PER_EMPLOYEE_OUTLIER")

    if pf_regular is False:
        notes.append("PF_FILING_IRREGULAR")

    return {
        "epfo_headcount": headcount,
        "pf_filing_regular": pf_regular,
        "revenue_per_employee_outlier": outlier,
        "source_notes": notes,
    }
