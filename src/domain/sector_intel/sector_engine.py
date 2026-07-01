"""
part3/services/s5_sector/sector_engine.py
RyskNode Labs — S5 Sector Intelligence

Buyer-vs-peer comparison engine.

Produces a SectorReport dict with:
  nic_code, sector_name, sector_risk
  benchmark          — sector median values
  buyer              — buyer's actual values
  comparison         — per-metric delta + percentile rank (higher = better)
  overall_percentile — average percentile across all metrics
  narrative          — plain-language summary

Percentile rank convention:
  100 = best in sector, 0 = worst.
  For metrics where lower is better (D/E, DSO, PD, EL%):
    percentile = 100 * (1 - buyer_val / (2 * median))  clamped 0–100
  For metrics where higher is better (CR, Gov Score, Limit):
    percentile = 100 * (buyer_val / (2 * median))       clamped 0–100
"""
from __future__ import annotations

from typing import Dict, Optional

from domain.sector_intel.nic_store import NICStore


def _pct_lower_is_better(buyer: float, median: float) -> float:
    if median <= 0:
        return 50.0
    return min(100.0, max(0.0, 100.0 * (1 - buyer / (2 * median))))


def _pct_higher_is_better(buyer: float, median: float) -> float:
    if median <= 0:
        return 50.0
    return min(100.0, max(0.0, 100.0 * (buyer / (2 * median))))


class SectorEngine:
    """Compare a buyer's financials against NIC-sector benchmarks."""

    def __init__(self, nic_store: Optional[NICStore] = None) -> None:
        self.store = nic_store or NICStore()

    def run(
        self,
        nic_code: int,
        buyer_data: Dict,
    ) -> Dict:
        """
        buyer_data expected keys (all optional):
          current_ratio, debt_to_equity, dso,
          blended_pd, el_pct, governance_score, evaluated_limit

        Returns SectorReport dict.
        """
        benchmark = self.store.get(nic_code)
        if benchmark is None:
            return {
                "nic_code":    nic_code,
                "error":       f"NIC {nic_code} not in benchmark store",
                "supported":   self.store.list_nic_codes(),
            }

        # Buyer values with fallback to benchmark median
        b_cr    = float(buyer_data.get("current_ratio")    or benchmark["median_cr"])
        b_de    = float(buyer_data.get("debt_to_equity")   or benchmark["median_de"])
        b_dso   = float(buyer_data.get("dso")              or benchmark["median_dso"])
        b_pd    = float(buyer_data.get("blended_pd")       or benchmark["median_pd"])
        b_el    = float(buyer_data.get("el_pct")           or benchmark["median_el_pct"])
        b_gov   = float(buyer_data.get("governance_score") or benchmark["median_gov_score"])
        b_lim   = float(buyer_data.get("evaluated_limit")  or benchmark["median_limit_cr"] * 1e7)
        b_lim_cr = b_lim / 1e7  # convert INR to Cr for comparison

        comparison = {
            "current_ratio": {
                "buyer": b_cr, "median": benchmark["median_cr"],
                "delta": round(b_cr - benchmark["median_cr"], 3),
                "percentile": round(_pct_higher_is_better(b_cr, benchmark["median_cr"]), 1),
                "better_if": "HIGHER",
            },
            "debt_to_equity": {
                "buyer": b_de, "median": benchmark["median_de"],
                "delta": round(b_de - benchmark["median_de"], 3),
                "percentile": round(_pct_lower_is_better(b_de, benchmark["median_de"]), 1),
                "better_if": "LOWER",
            },
            "dso": {
                "buyer": b_dso, "median": benchmark["median_dso"],
                "delta": round(b_dso - benchmark["median_dso"], 1),
                "percentile": round(_pct_lower_is_better(b_dso, benchmark["median_dso"]), 1),
                "better_if": "LOWER",
            },
            "blended_pd": {
                "buyer": b_pd, "median": benchmark["median_pd"],
                "delta": round(b_pd - benchmark["median_pd"], 4),
                "percentile": round(_pct_lower_is_better(b_pd, benchmark["median_pd"]), 1),
                "better_if": "LOWER",
            },
            "el_pct": {
                "buyer": b_el, "median": benchmark["median_el_pct"],
                "delta": round(b_el - benchmark["median_el_pct"], 4),
                "percentile": round(_pct_lower_is_better(b_el, benchmark["median_el_pct"]), 1),
                "better_if": "LOWER",
            },
            "governance_score": {
                "buyer": b_gov, "median": benchmark["median_gov_score"],
                "delta": round(b_gov - benchmark["median_gov_score"], 2),
                "percentile": round(_pct_higher_is_better(b_gov, benchmark["median_gov_score"]), 1),
                "better_if": "HIGHER",
            },
            "evaluated_limit_cr": {
                "buyer": b_lim_cr, "median": benchmark["median_limit_cr"],
                "delta": round(b_lim_cr - benchmark["median_limit_cr"], 3),
                "percentile": round(_pct_higher_is_better(b_lim_cr, benchmark["median_limit_cr"]), 1),
                "better_if": "HIGHER",
            },
        }

        overall_pct = round(
            sum(v["percentile"] for v in comparison.values()) / len(comparison), 1
        )

        # Narrative
        risk_adj = {
            "LOW": "a low-risk sector",
            "MODERATE": "a moderate-risk sector",
            "HIGH": "a high-risk sector",
            "CRITICAL": "a critical-risk sector",
        }.get(benchmark["sector_risk"], "this sector")

        narrative = (
            f"NIC {nic_code} — {benchmark['sector_name']} is {risk_adj} "
            f"with a median PD of {benchmark['median_pd']*100:.2f}% across "
            f"{benchmark['peer_count']} peers. "
            f"This buyer ranks at the {overall_pct:.0f}th percentile overall. "
        )
        if b_dso > benchmark["median_dso"] * 1.2:
            narrative += f"DSO ({b_dso:.0f}d) is elevated vs sector median ({benchmark['median_dso']:.0f}d). "
        if b_de > benchmark["median_de"] * 1.2:
            narrative += f"Leverage (D/E {b_de:.2f}) is above sector median ({benchmark['median_de']:.2f}). "
        if b_gov >= benchmark["median_gov_score"]:
            narrative += "Governance score is at or above sector median — positive signal."

        return {
            "nic_code":          nic_code,
            "sector_name":       benchmark["sector_name"],
            "sector_risk":       benchmark["sector_risk"],
            "peer_count":        benchmark["peer_count"],
            "benchmark":         benchmark,
            "buyer":             buyer_data,
            "comparison":        comparison,
            "overall_percentile": overall_pct,
            "narrative":         narrative,
        }
