"""Raw financial field extraction and validation from NormalizedRecord."""
from typing import Optional
from domain.schemas.normalized_record import NormalizedRecord
from domain.compute.safe_math import safe_subtract


class RawFinancials:
    """Container for validated raw financial fields used in ratio computation."""

    def __init__(self, record: NormalizedRecord):
        self.current_assets = record.current_assets_latest
        self.current_liabilities = record.current_liabilities_latest
        self.total_assets = record.total_assets_latest
        self.total_debt = record.total_debt_latest
        self.equity = record.equity_latest
        self.inventory = record.inventory_latest
        self.accounts_receivable = record.accounts_receivable_latest
        self.accounts_payable = record.accounts_payable_latest
        self.net_revenue = record.net_revenue_latest
        self.cogs = record.cogs_latest

        # Derived working capital
        self.working_capital: Optional[float] = safe_subtract(
            self.current_assets, self.current_liabilities
        )

        # Quick assets = current assets - inventory
        self.quick_assets: Optional[float] = safe_subtract(
            self.current_assets, self.inventory
        )

        # Tangible net worth = equity - intangible assets (approximated as equity here)
        self.tangible_net_worth: Optional[float] = self.equity

    @property
    def is_balance_sheet_available(self) -> bool:
        return any([
            self.current_assets is not None,
            self.total_assets is not None,
            self.equity is not None,
        ])
