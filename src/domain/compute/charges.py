from __future__ import annotations

from collections import Counter
from datetime import date, datetime, timezone
from typing import Any, Iterable

PSU_KEYWORDS = {
    "state bank",
    "bank of baroda",
    "punjab national",
    "canara",
    "union bank",
    "bank of india",
    "indian bank",
    "ucobank",
    "central bank of india",
    "indian overseas",
    "punjab & sind",
    "idbi",
    "syndicate bank",
    "corporation bank",
    "allahabad bank",
    "vijaya bank",
    "oriental bank",
    "bank of maharashtra",
}
PRIVATE_BANK_KEYWORDS = {
    "hdfc",
    "icici",
    "axis",
    "kotak",
    "indusind",
    "yes bank",
    "idfc",
    "federal bank",
    "rbl",
    "south indian bank",
    "karur vysya",
    "bandhan",
    "city union",
    "hsbc",
    "hongkong",
    "citi",
    "standard chartered",
    "dbs bank",
    "deutsche bank",
    "barclays",
    "jp morgan",
}
NBFC_KEYWORDS = {"finance", "capital", "finserv", "credit", "leasing", "housing finance", "investment"}


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except Exception:
        return None


def _classify_lender(name: str) -> str:
    n = (name or "").strip().lower()
    if any(k in n for k in PSU_KEYWORDS):
        return "PSU_BANK"
    if any(k in n for k in PRIVATE_BANK_KEYWORDS):
        return "PRIVATE_BANK"
    if any(k in n for k in NBFC_KEYWORDS):
        return "NBFC_ONLY"
    return "UNKNOWN"


def derive_charge_conduct_signals(charges: Iterable[dict[str, Any]], today: date | None = None) -> dict[str, Any]:
    today = today or datetime.now(timezone.utc).date()
    
    # Support multiple status keys (chargeStatus, status, STATUS)
    active = []
    for c in charges:
        status_val = str(
            c.get("chargeStatus") 
            or c.get("status") 
            or c.get("STATUS") 
            or ""
        ).strip().lower()
        if status_val in {"open", "active", "modified"}:
            active.append(c)

    lender_types = []
    recent = 0
    old_unsatisfied = 0
    lenders = set()

    for c in active:
        # Support multiple holder keys (chargeHolder, chName, holder_name, charge_holder, LENDER_NAME)
        holder = str(
            c.get("chargeHolder")
            or c.get("chName")
            or c.get("holder_name")
            or c.get("charge_holder")
            or c.get("bankName")
            or c.get("LENDER_NAME")
            or ""
        )
        if holder:
            lenders.add(holder.strip())
            lender_types.append(_classify_lender(holder))
            
        # Support multiple creation date keys (dateOfCreation, creationDate, created_date, date_of_creation, CREATION_DATE)
        created = _parse_date(
            c.get("dateOfCreation")
            or c.get("creationDate")
            or c.get("created_date")
            or c.get("date_of_creation")
            or c.get("CREATION_DATE")
        )
        if created:
            age_days = (today - created).days
            if age_days <= 90:
                recent += 1
            if age_days >= 365 * 7:
                old_unsatisfied += 1

    if not active:
        flag = "NO_LENDER"
    else:
        counts = Counter([t for t in lender_types if t != "UNKNOWN"])
        if counts and len(counts) == 1:
            flag = next(iter(counts.keys()))
        elif counts:
            flag = "MIXED"
        else:
            flag = "MIXED"

    return {
        "charge_count_active": len(active),
        "has_any_active_charge": len(active) > 0,
        "has_recent_charge_90d": recent > 0,
        "old_unsatisfied_charge_count": old_unsatisfied,
        "lender_quality_flag": flag,
        "distinct_lender_count": len(lenders),
    }
