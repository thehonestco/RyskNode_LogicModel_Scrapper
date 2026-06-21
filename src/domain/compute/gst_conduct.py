"""GST conduct signal extraction.

Patch L — new module.

Derives filing-consistency conduct signal from the GST API response.
Separate from the existing GST revenue / sector extraction so that
concerns stay isolated.
"""

from __future__ import annotations

from typing import Any

# Valid filing consistency values expected from aggregator
_FILING_REGULAR = "regular"
_FILING_IRREGULAR = "irregular"
_FILING_NON_FILER = "non-filer"


def derive_gst_conduct_signals(gst_raw: dict[str, Any] | None) -> dict[str, Any]:
    """
    Derive GST filing conduct signals.

    Returns:
      gst_filing_consistency   str   — 'regular' | 'irregular' | 'non-filer' | None
      source_notes             list  — reason codes for pipeline log
    """
    if not gst_raw:
        return {
            "gst_filing_consistency": None,
            "source_notes": ["GST_DATA_UNAVAILABLE"],
        }

    filing = (gst_raw.get("gst_filing_consistency") or "").lower().strip()
    notes: list[str] = []

    if filing == _FILING_IRREGULAR:
        notes.append("GST_FILING_IRREGULAR")
    elif filing == _FILING_NON_FILER:
        notes.append("GST_FILING_NON_FILER")

    return {
        "gst_filing_consistency": filing or None,
        "source_notes": notes,
    }
