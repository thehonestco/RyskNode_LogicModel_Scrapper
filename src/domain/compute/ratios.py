"""Financial ratio computation layer.

All ratios follow standard accounting definitions.
References: standard balance-sheet and working-capital ratio formulas.
"""

from typing import Optional
from domain.compute.raw_financials import RawFinancials
from domain.compute.safe_math import safe_divide


def compute_current_ratio(rf: RawFinancials) -> Optional[float]:
    """Current Ratio = Current Assets / Current Liabilities"""
    return safe_divide(rf.current_assets, rf.current_liabilities)


def compute_quick_ratio(rf: RawFinancials) -> Optional[float]:
    """Quick Ratio = (Current Assets - Inventory) / Current Liabilities"""
    return safe_divide(rf.quick_assets, rf.current_liabilities)


def compute_working_capital(rf: RawFinancials) -> Optional[float]:
    """Working Capital = Current Assets - Current Liabilities"""
    return rf.working_capital


def compute_debt_to_equity(rf: RawFinancials) -> Optional[float]:
    """Debt-to-Equity = Total Debt / Shareholders Equity"""
    return safe_divide(rf.total_debt, rf.equity)


def compute_debt_to_assets(rf: RawFinancials) -> Optional[float]:
    """Debt-to-Assets = Total Debt / Total Assets"""
    return safe_divide(rf.total_debt, rf.total_assets)


def compute_tangible_net_worth(rf: RawFinancials) -> Optional[float]:
    """Tangible Net Worth = Equity (intangible assets excluded if available)"""
    return rf.tangible_net_worth


def compute_dso(rf: RawFinancials) -> Optional[float]:
    """Days Sales Outstanding = (Accounts Receivable / Net Revenue) * 365"""
    ratio = safe_divide(rf.accounts_receivable, rf.net_revenue)
    return round(ratio * 365, 2) if ratio is not None else None


def compute_dpo(rf: RawFinancials) -> Optional[float]:
    """Days Payable Outstanding = (Accounts Payable / COGS) * 365"""
    ratio = safe_divide(rf.accounts_payable, rf.cogs)
    return round(ratio * 365, 2) if ratio is not None else None


def compute_all_ratios(rf: RawFinancials) -> dict:
    """Compute and return all financial ratios as a dict."""
    return {
        "current_ratio": compute_current_ratio(rf),
        "quick_ratio": compute_quick_ratio(rf),
        "working_capital": compute_working_capital(rf),
        "debt_to_equity": compute_debt_to_equity(rf),
        "debt_to_assets": compute_debt_to_assets(rf),
        "tangible_net_worth": compute_tangible_net_worth(rf),
        "dso": compute_dso(rf),
        "dpo": compute_dpo(rf),
    }
