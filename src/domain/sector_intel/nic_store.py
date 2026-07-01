"""
part3/services/s5_sector/nic_store.py
RyskNode Labs — S5 Sector Intelligence

NIC-code benchmark data store.
Synthetic proxies based on RBI MSME sector default statistics and
CIBIL MSME Pulse industry benchmarks (publicly available ranges).

Each NIC entry:
  nic_code          int     NIC 2008 2-digit code
  sector_name       str
  sector_risk       str     LOW | MODERATE | HIGH | CRITICAL
  median_cr         float   Current Ratio
  median_de         float   Debt-to-Equity
  median_dso        float   Days Sales Outstanding
  median_pd         float   Blended PD (decimal)
  median_el_pct     float   Expected Loss %
  median_gov_score  float   Pralyon Governance Score proxy
  median_limit_cr   float   Typical evaluated limit (INR Cr)
  peer_count        int     Reference peer count in dataset

Currently covers 10 manufacturing NIC codes.
Live RBI data adapter: plug in by subclassing NICStore and overriding load().
"""
from __future__ import annotations

from typing import Dict, List, Optional

_BENCHMARK_DATA: List[Dict] = [
    {
        "nic_code": 10, "sector_name": "Food Products",
        "sector_risk": "MODERATE",
        "median_cr": 1.38, "median_de": 1.42, "median_dso": 48,
        "median_pd": 0.052, "median_el_pct": 0.023,
        "median_gov_score": 59.1, "median_limit_cr": 1.42, "peer_count": 312,
    },
    {
        "nic_code": 13, "sector_name": "Textiles",
        "sector_risk": "HIGH",
        "median_cr": 1.21, "median_de": 1.89, "median_dso": 62,
        "median_pd": 0.071, "median_el_pct": 0.032,
        "median_gov_score": 54.3, "median_limit_cr": 1.18, "peer_count": 487,
    },
    {
        "nic_code": 14, "sector_name": "Wearing Apparel",
        "sector_risk": "HIGH",
        "median_cr": 1.19, "median_de": 1.76, "median_dso": 58,
        "median_pd": 0.068, "median_el_pct": 0.031,
        "median_gov_score": 53.8, "median_limit_cr": 1.05, "peer_count": 221,
    },
    {
        "nic_code": 15, "sector_name": "Leather Products",
        "sector_risk": "MODERATE",
        "median_cr": 1.31, "median_de": 1.51, "median_dso": 52,
        "median_pd": 0.055, "median_el_pct": 0.025,
        "median_gov_score": 57.2, "median_limit_cr": 1.28, "peer_count": 143,
    },
    {
        "nic_code": 17, "sector_name": "Paper & Paper Products",
        "sector_risk": "MODERATE",
        "median_cr": 1.35, "median_de": 1.48, "median_dso": 50,
        "median_pd": 0.050, "median_el_pct": 0.022,
        "median_gov_score": 60.4, "median_limit_cr": 1.55, "peer_count": 178,
    },
    {
        "nic_code": 20, "sector_name": "Chemicals & Chemical Products",
        "sector_risk": "MODERATE",
        "median_cr": 1.44, "median_de": 1.31, "median_dso": 44,
        "median_pd": 0.045, "median_el_pct": 0.020,
        "median_gov_score": 63.7, "median_limit_cr": 1.82, "peer_count": 264,
    },
    {
        "nic_code": 22, "sector_name": "Rubber & Plastics",
        "sector_risk": "MODERATE",
        "median_cr": 1.40, "median_de": 1.38, "median_dso": 46,
        "median_pd": 0.048, "median_el_pct": 0.022,
        "median_gov_score": 61.9, "median_limit_cr": 1.61, "peer_count": 196,
    },
    {
        "nic_code": 24, "sector_name": "Basic Metals",
        "sector_risk": "HIGH",
        "median_cr": 1.28, "median_de": 1.72, "median_dso": 57,
        "median_pd": 0.065, "median_el_pct": 0.029,
        "median_gov_score": 56.1, "median_limit_cr": 1.38, "peer_count": 309,
    },
    {
        "nic_code": 25, "sector_name": "Metal Products (Fabricated)",
        "sector_risk": "MODERATE",
        "median_cr": 1.41, "median_de": 1.35, "median_dso": 42,
        "median_pd": 0.049, "median_el_pct": 0.022,
        "median_gov_score": 62.0, "median_limit_cr": 1.60, "peer_count": 418,
    },
    {
        "nic_code": 26, "sector_name": "Computer & Electronics",
        "sector_risk": "LOW",
        "median_cr": 1.62, "median_de": 0.98, "median_dso": 38,
        "median_pd": 0.032, "median_el_pct": 0.014,
        "median_gov_score": 68.4, "median_limit_cr": 2.14, "peer_count": 152,
    },
]

_INDEX: Dict[int, Dict] = {row["nic_code"]: row for row in _BENCHMARK_DATA}


class NICStore:
    """Read-only NIC benchmark data store."""

    def get(self, nic_code: int) -> Optional[Dict]:
        """Return benchmark dict for a NIC code, or None if not found."""
        return _INDEX.get(nic_code)

    def list_all(self) -> List[Dict]:
        return list(_BENCHMARK_DATA)

    def list_nic_codes(self) -> List[int]:
        return sorted(_INDEX.keys())

    def sector_risk(self, nic_code: int) -> str:
        entry = self.get(nic_code)
        return entry["sector_risk"] if entry else "UNKNOWN"
