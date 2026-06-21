"""Report-only field computation.

Patch L — new module.

Computes fields that are shown in the credit report but do NOT
directly drive scoring or band decisions. These fields provide
additional context for the underwriter and credit committee.

Fields computed here:
  dpo               Days Payable Outstanding
                    = (trade_payables / revenue) * 365
                    Shows how long the entity takes to pay its suppliers.
                    Informational only — sector benchmarks needed before scoring.

  cash_coverage     Cash at Bank / Total Debt
                    = cash_and_bank / total_debt
                    Informational only — NOT scored because MCA balance sheet
                    data is 12-18 months old by the time of assessment.
                    Cash position can change materially in that window.

Design note:
  Both ratios are surfaced in FinalFeatureRow and the report output.
  Neither adjusts the conduct score or Part 2 dimension scores.
  In Phase 2 (CreditPy), DPO may become a scored feature once
  sector-level DPO benchmarks are established from portfolio data.
"""

from __future__ import annotations

from typing import Any, Optional

from domain.compute.safe_math import safe_divide


def compute_dpo(y1: dict[str, Any] | None) -> Optional[float]:
    """
    Days Payable Outstanding.
    DPO = (trade_payables / revenue) * 365

    Uses revenue as denominator (not COGS) because COGS is rarely
    available in MCA filings for SME entities.
    """
    if not y1:
        return None
    trade_payables = y1.get("trade_payables")
    revenue = y1.get("revenue")
    ratio = safe_divide(trade_payables, revenue)
    if ratio is None:
        return None
    return round(ratio * 365, 1)


def compute_cash_coverage(y1: dict[str, Any] | None) -> Optional[float]:
    """
    Cash Coverage Ratio (report-only).
    cash_coverage = cash_and_bank / total_debt

    NOT used in scoring. Shown in report as supplementary liquidity context.
    MCA BS data is 12-18 months old — unreliable for current cash position.
    """
    if not y1:
        return None
    cash = y1.get("cash_and_bank")
    debt = y1.get("total_debt")
    ratio = safe_divide(cash, debt)
    if ratio is None:
        return None
    return round(ratio, 3)
