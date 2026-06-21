"""
part1/scorecards/sector_benchmarks.py
======================================
Sector-specific financial benchmark thresholds for the RyskNode scoring engine.

Version history
---------------
  v1.0  Initial 8-bucket release
  v1.1  3 fixes applied:
         1. Split CAPITAL_GOODS div-29 into AUTO_COMPONENTS (291–293)
            and retained CAPITAL_GOODS for 294+ (see nic_mapper.py)
         2. TRADE_WHOLESALE D/E tolerance corrected to reflect actual
            Indian MSME trading leverage norms (2.5x low, not 1.5x)
         3. Added ELECTRONICS_ELECTRICAL (NIC 26–27) and
            RUBBER_PLASTIC_PAPER (NIC 17, 22) as explicit buckets
            replacing the catch-all GENERAL assignment

Sector Groups (10 total)
------------------------
  AUTO_COMPONENTS        NIC 291–293
  CAPITAL_GOODS          NIC 24–28, 294+, 30
  ELECTRONICS_ELECTRICAL NIC 26–27
  FMCG_CONSUMER          NIC 10–12
  PHARMA_CHEM            NIC 20–21
  RUBBER_PLASTIC_PAPER   NIC 17, 22
  TEXTILE_APPAREL        NIC 13–15
  INFRA_CONSTRUCTION     NIC 41–43
  TRADE_WHOLESALE        NIC 45–47
  SERVICES_IT            NIC 58–63
  GENERAL                fallback

Threshold Schema
----------------
  dso            : {excellent, good, high}         day thresholds (lower is better)
  current_ratio  : {strong, adequate, marginal}    ratio (higher is better)
  quick_ratio    : {strong, adequate}              ratio (higher is better)
  debt_to_equity : {low, moderate, high}           ratio (lower is better)
  cagr           : {strong, moderate}             decimal  e.g. 0.15 = 15%

Rationale Notes
---------------
  AUTO_COMPONENTS:
    OEM payment cycles are contractually fixed at 30–45 days.
    High buyer concentration (top 3 OEMs = 60%+ of revenue) means
    receivables are predictable but collection risk is concentrated.
    D/E tolerance moderate — auto ancillaries are capital-intensive
    but OEM relationships provide implicit credit support.

  ELECTRONICS_ELECTRICAL:
    Mix of B2B project sales (longer DSO) and component supply (tighter).
    Calibrated between IT services (asset-light) and capital goods (heavy).
    D/E moderate — component businesses need WC lines but stay lean.

  RUBBER_PLASTIC_PAPER:
    Commodity-adjacent manufacturing. Cash cycles moderate.
    D/E tolerance similar to CAPITAL_GOODS — machinery-heavy operations.
    CAGR expectations lower — mature, volume-driven sector.

  TRADE_WHOLESALE (v1.1 corrected):
    D/E low threshold raised from 1.5x to 2.5x to match actual Indian
    MSME trading leverage norms. Traders run CC limits, stock financing,
    and buyer credit structurally. A 1.5x cap was falsely penalising
    healthy operators. Current ratio floor (1.2x adequate) is the real
    liquidity signal for this sector, not D/E.

  GENERAL:
    Preserves original universal thresholds verbatim. Zero regression
    for entities with missing or unrecognised NIC codes.
"""

from __future__ import annotations

SECTOR_BENCHMARKS: dict[str, dict] = {
    # ------------------------------------------------------------------
    "AUTO_COMPONENTS": {
        "dso": {
            "excellent": 30,
            "good": 45,
            "high": 75,
        },
        "current_ratio": {
            "strong": 1.8,
            "adequate": 1.3,
            "marginal": 1.0,
        },
        "quick_ratio": {
            "strong": 1.00,
            "adequate": 0.70,
        },
        "debt_to_equity": {
            "low": 1.5,
            "moderate": 2.5,
            "high": 3.5,
        },
        "cagr": {
            "strong": 0.12,
            "moderate": 0.06,
        },
    },
    # ------------------------------------------------------------------
    "CAPITAL_GOODS": {
        "dso": {
            "excellent": 60,
            "good": 90,
            "high": 120,
        },
        "current_ratio": {
            "strong": 1.8,
            "adequate": 1.3,
            "marginal": 1.0,
        },
        "quick_ratio": {
            "strong": 1.00,
            "adequate": 0.70,
        },
        "debt_to_equity": {
            "low": 1.5,
            "moderate": 2.5,
            "high": 3.5,
        },
        "cagr": {
            "strong": 0.12,
            "moderate": 0.06,
        },
    },
    # ------------------------------------------------------------------
    "ELECTRONICS_ELECTRICAL": {
        "dso": {
            "excellent": 45,
            "good": 75,
            "high": 105,
        },
        "current_ratio": {
            "strong": 1.8,
            "adequate": 1.3,
            "marginal": 1.0,
        },
        "quick_ratio": {
            "strong": 1.10,
            "adequate": 0.80,
        },
        "debt_to_equity": {
            "low": 1.0,
            "moderate": 2.0,
            "high": 3.0,
        },
        "cagr": {
            "strong": 0.15,
            "moderate": 0.08,
        },
    },
    # ------------------------------------------------------------------
    "FMCG_CONSUMER": {
        "dso": {
            "excellent": 20,
            "good": 40,
            "high": 60,
        },
        "current_ratio": {
            "strong": 1.5,
            "adequate": 1.2,
            "marginal": 1.0,
        },
        "quick_ratio": {
            "strong": 0.90,
            "adequate": 0.65,
        },
        "debt_to_equity": {
            "low": 1.0,
            "moderate": 2.0,
            "high": 3.0,
        },
        "cagr": {
            "strong": 0.15,
            "moderate": 0.08,
        },
    },
    # ------------------------------------------------------------------
    "PHARMA_CHEM": {
        "dso": {
            "excellent": 45,
            "good": 75,
            "high": 100,
        },
        "current_ratio": {
            "strong": 2.0,
            "adequate": 1.5,
            "marginal": 1.1,
        },
        "quick_ratio": {
            "strong": 1.20,
            "adequate": 0.85,
        },
        "debt_to_equity": {
            "low": 1.0,
            "moderate": 2.0,
            "high": 3.0,
        },
        "cagr": {
            "strong": 0.15,
            "moderate": 0.08,
        },
    },
    # ------------------------------------------------------------------
    "RUBBER_PLASTIC_PAPER": {
        "dso": {
            "excellent": 45,
            "good": 75,
            "high": 105,
        },
        "current_ratio": {
            "strong": 1.6,
            "adequate": 1.2,
            "marginal": 1.0,
        },
        "quick_ratio": {
            "strong": 0.90,
            "adequate": 0.65,
        },
        "debt_to_equity": {
            "low": 1.5,
            "moderate": 2.5,
            "high": 3.5,
        },
        "cagr": {
            "strong": 0.10,
            "moderate": 0.05,
        },
    },
    # ------------------------------------------------------------------
    "TEXTILE_APPAREL": {
        "dso": {
            "excellent": 45,
            "good": 75,
            "high": 100,
        },
        "current_ratio": {
            "strong": 1.5,
            "adequate": 1.2,
            "marginal": 1.0,
        },
        "quick_ratio": {
            "strong": 0.80,
            "adequate": 0.60,
        },
        "debt_to_equity": {
            "low": 1.5,
            "moderate": 2.5,
            "high": 3.5,
        },
        "cagr": {
            "strong": 0.10,
            "moderate": 0.05,
        },
    },
    # ------------------------------------------------------------------
    "INFRA_CONSTRUCTION": {
        "dso": {
            "excellent": 75,
            "good": 120,
            "high": 150,
        },
        "current_ratio": {
            "strong": 1.3,
            "adequate": 1.0,
            "marginal": 0.8,
        },
        "quick_ratio": {
            "strong": 0.80,
            "adequate": 0.60,
        },
        "debt_to_equity": {
            "low": 2.0,
            "moderate": 3.5,
            "high": 5.0,
        },
        "cagr": {
            "strong": 0.12,
            "moderate": 0.06,
        },
    },
    # ------------------------------------------------------------------
    # v1.1 FIX: D/E tolerance corrected for Indian MSME trading norms.
    # Traders structurally run CC limits + stock financing.
    # Primary liquidity signal is current_ratio (floor 1.2x adequate),
    # not D/E cap. Old low=1.5 was generating false WARN flags.
    "TRADE_WHOLESALE": {
        "dso": {
            "excellent": 20,
            "good": 45,
            "high": 60,
        },
        "current_ratio": {
            "strong": 1.5,
            "adequate": 1.2,  # tighter floor — primary liquidity signal
            "marginal": 1.0,
        },
        "quick_ratio": {
            "strong": 0.70,
            "adequate": 0.50,
        },
        "debt_to_equity": {
            "low": 2.5,  # was 1.5 — raised to match actual trading leverage
            "moderate": 3.5,  # was 2.5
            "high": 5.0,  # was 3.5
        },
        "cagr": {
            "strong": 0.12,
            "moderate": 0.06,
        },
    },
    # ------------------------------------------------------------------
    "SERVICES_IT": {
        "dso": {
            "excellent": 30,
            "good": 60,
            "high": 90,
        },
        "current_ratio": {
            "strong": 2.0,
            "adequate": 1.5,
            "marginal": 1.0,
        },
        "quick_ratio": {
            "strong": 1.50,
            "adequate": 1.00,
        },
        "debt_to_equity": {
            "low": 0.5,
            "moderate": 1.0,
            "high": 2.0,
        },
        "cagr": {
            "strong": 0.20,
            "moderate": 0.10,
        },
    },
    # ------------------------------------------------------------------
    # GENERAL — original universal thresholds preserved verbatim.
    # DO NOT change these values without a full regression review.
    "GENERAL": {
        "dso": {
            "excellent": 30,
            "good": 60,
            "high": 90,
        },
        "current_ratio": {
            "strong": 2.0,
            "adequate": 1.5,
            "marginal": 1.0,
        },
        "quick_ratio": {
            "strong": 1.00,
            "adequate": 0.75,
        },
        "debt_to_equity": {
            "low": 1.0,
            "moderate": 2.0,
            "high": 3.0,
        },
        "cagr": {
            "strong": 0.20,
            "moderate": 0.10,
        },
    },
}


# ---------------------------------------------------------------------------
# Public helper
# ---------------------------------------------------------------------------


def get_benchmarks(sector: str) -> dict:
    """
    Return benchmark threshold dict for a given sector group.
    Falls back to GENERAL silently. Never raises.

    Examples
    --------
    >>> get_benchmarks("AUTO_COMPONENTS")["dso"]["excellent"]
    30
    >>> get_benchmarks("TRADE_WHOLESALE")["debt_to_equity"]["low"]
    2.5
    >>> get_benchmarks("UNKNOWN")["dso"]["excellent"]
    30
    """
    return SECTOR_BENCHMARKS.get(sector, SECTOR_BENCHMARKS["GENERAL"])
