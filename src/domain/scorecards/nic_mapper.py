"""
part1/scorecards/nic_mapper.py
==============================
NIC 2008 activity code → internal sector group router.

Purpose
-------
Resolves a raw NIC code (2–6 digit string as issued by MCA/GST) into one
of 10 standardised sector groups used by the financial scoring functions
to select sector-appropriate benchmark thresholds.

Sector Groups (10 total as of v1.1)
------------------------------------
  AUTO_COMPONENTS        NIC 291x–293x  (auto parts, vehicle components, OEM supply)
  CAPITAL_GOODS          NIC 24–28, 294x–299x, 30  (machinery, electricals* via explicit
                         split, fabricated metals, basic metals, transport equip excl auto)
  ELECTRONICS_ELECTRICAL NIC 26–27      (electronics, semiconductor, electrical equipment)
  FMCG_CONSUMER          NIC 10–12      (food, beverages, tobacco)
  PHARMA_CHEM            NIC 20–21      (pharmaceuticals, chemicals)
  RUBBER_PLASTIC_PAPER   NIC 17, 22     (paper products, rubber, plastic)
  TEXTILE_APPAREL        NIC 13–15      (textiles, garments, leather)
  INFRA_CONSTRUCTION     NIC 41–43      (civil construction, contractors)
  TRADE_WHOLESALE        NIC 45–47      (wholesale & retail trade)
  SERVICES_IT            NIC 58–63      (IT, software, publishing)
  GENERAL                all others / unknown / None  (safe fallback)

NIC Division 29 Split Logic
----------------------------
Division 29 (Motor vehicles, trailers, semi-trailers) covers two distinct
risk profiles:
  29100–29300 (sub-class prefix 291–293)  →  AUTO_COMPONENTS
    - OEM-driven payment cycles (30–45 days)
    - High buyer concentration (Maruti, Tata, M&M)
    - PLI/export sensitivity
  29400–29900 (sub-class prefix 294–299)  →  CAPITAL_GOODS
    - Trailers, semi-trailers, general vehicle equipment
    - Longer project/order cycles, higher DSO tolerance

The split is performed in resolve_sector() via a 3-digit prefix check
before the 2-digit division fallback. NIC codes shorter than 3 digits
from division 29 default to CAPITAL_GOODS (conservative).

Usage
-----
    from domain.scorecards.nic_mapper import resolve_sector

    resolve_sector("29150")  # -> "AUTO_COMPONENTS"  (sub-class 291)
    resolve_sector("29254")  # -> "CAPITAL_GOODS"    (sub-class 292 -> wait, 292=auto)
    resolve_sector("29400")  # -> "CAPITAL_GOODS"    (sub-class 294)
    resolve_sector("27100")  # -> "ELECTRONICS_ELECTRICAL"
    resolve_sector("22210")  # -> "RUBBER_PLASTIC_PAPER"
    resolve_sector(None)     # -> "GENERAL"

Notes
-----
- NIC codes from MCA are typically 5-digit (e.g. 29254).
- NIC codes from GST/Udyam registrations can be 2–5 digits.
- 2-digit resolution is used for all divisions except 29 (which uses 3-digit).
- All lookups are O(1). Never raises.

NIC Division 29 Sub-class Reference
-------------------------------------
  291 — Manufacture of motor vehicles
  292 — Manufacture of bodies for motor vehicles; trailers (mixed — routed AUTO)
  293 — Manufacture of parts & accessories for motor vehicles  <- core auto ancillary
  294 — Manufacture of motorcycles
  295 — Manufacture of bicycles and invalid carriages
  296 — Manufacture of other transport equipment NEC
  299 — Repair of other transport equipment NEC
"""

from __future__ import annotations
from typing import Optional

# ---------------------------------------------------------------------------
# Sector group label constants
# ---------------------------------------------------------------------------

AUTO_COMPONENTS        = "AUTO_COMPONENTS"
CAPITAL_GOODS          = "CAPITAL_GOODS"
ELECTRONICS_ELECTRICAL = "ELECTRONICS_ELECTRICAL"
FMCG_CONSUMER          = "FMCG_CONSUMER"
PHARMA_CHEM            = "PHARMA_CHEM"
RUBBER_PLASTIC_PAPER   = "RUBBER_PLASTIC_PAPER"
TEXTILE_APPAREL        = "TEXTILE_APPAREL"
INFRA_CONSTRUCTION     = "INFRA_CONSTRUCTION"
TRADE_WHOLESALE        = "TRADE_WHOLESALE"
SERVICES_IT            = "SERVICES_IT"
GENERAL                = "GENERAL"

# ---------------------------------------------------------------------------
# Division 29 sub-class router
# 3-digit prefix 291–293 = AUTO_COMPONENTS; 294+ = CAPITAL_GOODS
# ---------------------------------------------------------------------------

_DIV29_AUTO_PREFIXES = {"291", "292", "293"}

def _resolve_div29(digits: str) -> str:
    """
    Route a normalised digit string for NIC division 29.
    Uses the 3-digit sub-class prefix to split auto vs machinery.
    Falls back to CAPITAL_GOODS for codes shorter than 3 digits.
    """
    if len(digits) >= 3 and digits[:3] in _DIV29_AUTO_PREFIXES:
        return AUTO_COMPONENTS
    return CAPITAL_GOODS


# ---------------------------------------------------------------------------
# NIC 2008 Division → Sector Group mapping
# Keys are 2-digit division strings ("01" … "99")
# Division 29 is NOT in this table — handled via _resolve_div29()
# ---------------------------------------------------------------------------

_DIVISION_MAP: dict[str, str] = {
    # ── Agriculture, forestry, fishing (01–03) ────────────────────────────
    "01": GENERAL, "02": GENERAL, "03": GENERAL,

    # ── Mining & quarrying (05–09) ────────────────────────────────────────
    "05": GENERAL, "06": GENERAL, "07": GENERAL, "08": GENERAL, "09": GENERAL,

    # ── Manufacturing — Food, beverages, tobacco (10–12) ─────────────────
    "10": FMCG_CONSUMER, "11": FMCG_CONSUMER, "12": FMCG_CONSUMER,

    # ── Manufacturing — Textiles, apparel, leather (13–15) ────────────────
    "13": TEXTILE_APPAREL, "14": TEXTILE_APPAREL, "15": TEXTILE_APPAREL,

    # ── Manufacturing — Wood (16) ──────────────────────────────────────────
    "16": GENERAL,

    # ── Manufacturing — Paper products (17) ────────────────────────────────
    "17": RUBBER_PLASTIC_PAPER,

    # ── Manufacturing — Printing (18) ─────────────────────────────────────
    "18": GENERAL,

    # ── Manufacturing — Coke, refined petroleum (19) ──────────────────────
    "19": GENERAL,

    # ── Manufacturing — Chemicals & pharma (20–21) ────────────────────────
    "20": PHARMA_CHEM, "21": PHARMA_CHEM,

    # ── Manufacturing — Rubber & plastics (22) ────────────────────────────
    "22": RUBBER_PLASTIC_PAPER,

    # ── Manufacturing — Non-metallic minerals (23) ────────────────────────
    "23": GENERAL,

    # ── Manufacturing — Basic metals (24) ─────────────────────────────────
    "24": CAPITAL_GOODS,

    # ── Manufacturing — Fabricated metals (25) ────────────────────────────
    "25": CAPITAL_GOODS,

    # ── Manufacturing — Computer, electronic, optical (26) ────────────────
    "26": ELECTRONICS_ELECTRICAL,

    # ── Manufacturing — Electrical equipment (27) ─────────────────────────
    "27": ELECTRONICS_ELECTRICAL,

    # ── Manufacturing — Machinery & equipment NEC (28) ────────────────────
    "28": CAPITAL_GOODS,

    # Division 29 intentionally absent — handled by _resolve_div29()

    # ── Manufacturing — Other transport equipment (30) ────────────────────
    "30": CAPITAL_GOODS,

    # ── Manufacturing — Furniture, other mfg, repair (31–33) ─────────────
    "31": GENERAL, "32": GENERAL, "33": GENERAL,

    # ── Utilities (35–39) ───────────────────────────────────────────────
    "35": GENERAL, "36": GENERAL, "37": GENERAL, "38": GENERAL, "39": GENERAL,

    # ── Construction (41–43) ──────────────────────────────────────────────
    "41": INFRA_CONSTRUCTION, "42": INFRA_CONSTRUCTION, "43": INFRA_CONSTRUCTION,

    # ── Wholesale & retail trade (45–47) ──────────────────────────────────
    "45": TRADE_WHOLESALE, "46": TRADE_WHOLESALE, "47": TRADE_WHOLESALE,

    # ── Transportation & storage (49–53) ──────────────────────────────────
    "49": GENERAL, "50": GENERAL, "51": GENERAL, "52": GENERAL, "53": GENERAL,

    # ── Accommodation & food services (55–56) ─────────────────────────────
    "55": GENERAL, "56": GENERAL,

    # ── IT / Information & communication (58–63) ─────────────────────────
    "58": SERVICES_IT, "59": SERVICES_IT, "60": SERVICES_IT,
    "61": SERVICES_IT, "62": SERVICES_IT, "63": SERVICES_IT,

    # ── Financial & insurance (64–66) ─────────────────────────────────────
    "64": GENERAL, "65": GENERAL, "66": GENERAL,

    # ── Real estate (68) ──────────────────────────────────────────────────
    "68": GENERAL,

    # ── Professional & technical (69–75) ──────────────────────────────────
    "69": GENERAL, "70": GENERAL, "71": GENERAL,
    "72": GENERAL, "73": GENERAL, "74": GENERAL, "75": GENERAL,

    # ── Administrative & support (77–82) ──────────────────────────────────
    "77": GENERAL, "78": GENERAL, "79": GENERAL,
    "80": GENERAL, "81": GENERAL, "82": GENERAL,

    # ── Public admin, education, health (84–88) ───────────────────────────
    "84": GENERAL, "85": GENERAL,
    "86": GENERAL, "87": GENERAL, "88": GENERAL,

    # ── Arts, recreation, other services (90–98) ──────────────────────────
    "90": GENERAL, "91": GENERAL, "92": GENERAL, "93": GENERAL,
    "94": GENERAL, "95": GENERAL, "96": GENERAL,
    "97": GENERAL, "98": GENERAL, "99": GENERAL,
}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def normalize_nic(raw_nic: Optional[str]) -> Optional[str]:
    """
    Normalise a raw NIC code to a clean digit string (leading zeros stripped).
    Returns None for empty / non-numeric input.

    Examples
    --------
    normalize_nic("29254") -> "29254"
    normalize_nic("09")    -> "9"     (stripped; caller zero-pads for display)
    normalize_nic("  29 ") -> "29"
    normalize_nic(None)    -> None
    normalize_nic("")      -> None
    """
    if not raw_nic:
        return None
    digits = "".join(c for c in str(raw_nic).strip() if c.isdigit())
    return digits if digits else None


def resolve_sector(raw_nic: Optional[str]) -> str:
    """
    Resolve a raw NIC code to one of the 10 internal sector group labels.

    Resolution order
    ----------------
    1. If division == 29: route via _resolve_div29() using 3-digit sub-class
    2. Otherwise: look up 2-digit division in _DIVISION_MAP
    3. Fallback: GENERAL

    Always returns a string. Never raises.

    Parameters
    ----------
    raw_nic : str | None
        Raw NIC 2008 activity code as sourced from MCA/GST/Udyam.

    Returns
    -------
    str
        One of: AUTO_COMPONENTS | CAPITAL_GOODS | ELECTRONICS_ELECTRICAL |
                FMCG_CONSUMER | PHARMA_CHEM | RUBBER_PLASTIC_PAPER |
                TEXTILE_APPAREL | INFRA_CONSTRUCTION | TRADE_WHOLESALE |
                SERVICES_IT | GENERAL
    """
    digits = normalize_nic(raw_nic)
    if not digits:
        return GENERAL

    # Zero-pad to get 2-digit division
    division = digits[:2].zfill(2)

    # Special case: Division 29 requires 3-digit sub-class split
    if division == "29":
        return _resolve_div29(digits)

    return _DIVISION_MAP.get(division, GENERAL)


def describe_sector(sector_group: str) -> str:
    """Human-readable label for a sector group (for report/UI output)."""
    _LABELS = {
        AUTO_COMPONENTS:        "Auto Components & OEM Supply",
        CAPITAL_GOODS:          "Capital Goods & Manufacturing",
        ELECTRONICS_ELECTRICAL: "Electronics & Electrical Equipment",
        FMCG_CONSUMER:          "FMCG & Consumer Goods",
        PHARMA_CHEM:            "Pharmaceuticals & Chemicals",
        RUBBER_PLASTIC_PAPER:   "Rubber, Plastics & Paper",
        TEXTILE_APPAREL:        "Textiles & Apparel",
        INFRA_CONSTRUCTION:     "Infrastructure & Construction",
        TRADE_WHOLESALE:        "Wholesale & Retail Trade",
        SERVICES_IT:            "IT & Services",
        GENERAL:                "General / Diversified",
    }
    return _LABELS.get(sector_group, "General / Diversified")
