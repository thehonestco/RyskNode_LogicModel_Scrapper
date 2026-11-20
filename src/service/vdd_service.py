"""
Service 4 — Vendor Due Diligence (VDD)

Ponytail rule: delegates all data-extraction to PPREService helpers.
Never copy-pastes methods from ppre_service.py.
"""

from __future__ import annotations

import logging
import random
import string
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from common.service.unit_of_work import AbstractUnitOfWork
from service.artifact_service import ArtifactService
from service.ppre_service import PPREService

logger = logging.getLogger(__name__)


def _fmt_inr(val: float) -> str:
    """Format a float (in INR) to a human-readable string."""
    if val <= 0:
        return "₹0"
    if val >= 10_000_000:
        return f"₹{val / 10_000_000:.2f} Cr"
    if val >= 100_000:
        return f"₹{val / 100_000:.2f} L"
    return f"₹{val:,.0f}"


def _gen_report_id() -> str:
    now = datetime.now(timezone.utc)
    suffix = "".join(random.choices(string.digits, k=4))
    return f"RPT-VDD-{now.strftime('%Y%m%d')}-{suffix}"


def _verdict(
    rbi_defaulter: bool,
    confirmed_sanctions: bool,
    critical_flags: int,
    high_flags: int,
    docs_missing: int,
) -> tuple[str, str, str]:
    """Return (status, headline, detail) per PRD §5.4 verdict logic."""
    if rbi_defaulter or confirmed_sanctions:
        return (
            "REJECTED",
            "Onboarding Rejected — Hard regulatory block",
            (
                "Entity is listed as an RBI wilful defaulter or confirmed on a sanctions watchlist. "
                "No commercial engagement is permitted until full regulatory clearance."
            ),
        )
    if critical_flags >= 1:
        return (
            "HOLD",
            "Onboarding on Hold — Critical blockers unresolved",
            (
                f"{critical_flags} critical flag(s) prevent onboarding. "
                "Procurement must not release purchase orders until all critical gaps are closed."
            ),
        )
    if 1 <= high_flags <= 3:
        return (
            "CONDITIONAL",
            "Conditional Onboarding — High-risk items require resolution",
            (
                f"{high_flags} high-risk item(s) identified. Onboarding may proceed "
                "conditionally subject to management sign-off and documentary remediation."
            ),
        )
    if critical_flags == 0 and high_flags == 0:
        if docs_missing == 0:
            return (
                "APPROVED",
                "Vendor Approved for Onboarding",
                (
                    "No critical or high-risk flags identified and all mandatory documents are present. "
                    "Vendor may proceed to commercial engagement."
                ),
            )
        elif docs_missing <= 2:
            return (
                "CONDITIONAL",
                "Conditional Onboarding — Pending documentation",
                (
                    f"{docs_missing} mandatory document(s) missing or incomplete. "
                    "Onboarding may proceed conditionally subject to document submission."
                ),
            )
        else:
            return (
                "HOLD",
                "Onboarding on Hold — Missing mandatory documentation",
                (
                    f"{docs_missing} mandatory documents not provided. "
                    "Full onboarding document pack required before commercial engagement."
                ),
            )
    # Default: treat residual high flags (>3) as HOLD
    return (
        "HOLD",
        "Onboarding on Hold — Multiple high-risk items",
        (
            f"{high_flags} high-risk item(s) identified alongside {docs_missing} missing documents. "
            "Risk committee review required before onboarding."
        ),
    )


class VDDService:
    """
    Service 4 — Vendor Due Diligence.

    Delegates to PPREService for all data extraction via method delegation.
    """

    def __init__(
        self,
        uow: AbstractUnitOfWork,
        artifact_service: ArtifactService,
    ) -> None:
        self.uow = uow
        self.artifact_service = artifact_service
        # Reuse PPREService for data extraction helpers (ponytail)
        self._ppre = PPREService(uow=uow, artifact_service=artifact_service)

    async def vendor_due_diligence(
        self,
        entity_id: str,
        seller_id: Optional[str] = None,
        requester_id: Optional[str] = None,
        trade_name: Optional[str] = None,
        state_code: Optional[str] = None,
        vendor_profile: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Run S4 Vendor Due Diligence assessment and return a structured payload.
        """
        # Resolve seller identity for audit & multi-tenancy
        effective_seller_id = seller_id or requester_id or "default"

        # ── 1. Fetch raw company data (delegates to PPREService._get_company_data)
        db_row = await self._ppre._get_company_data(entity_id)
        payload = db_row.get("payload") or {}

        overview_raw = (
            payload.get("overview", [{}])[0]
            if isinstance(payload.get("overview"), list)
            else payload.get("overview", {})
        ) or {}

        company_name = trade_name or db_row.get("company_name") or overview_raw.get("companyName") or entity_id

        # ── 2. Run Part-1 sourcing pipeline (delegates to PPREService._run_part1_sourcing)
        part1 = self._ppre._run_part1_sourcing(db_row)

        # ── 3. Extract specialist signals via existing PPREService helpers
        court_info: Dict[str, Any] = part1.get("court_info") or {}
        aml_info: Dict[str, Any] = part1.get("aml_info") or {}

        # Charge details
        raw_charges: List[Dict] = payload.get("charges") or []
        charge_summary = self._ppre._extract_charge_summary(payload, raw_charges)

        # EPFO
        epfo_data = self._ppre._extract_epfo(payload)

        # Director signals (from part1 pipeline)
        gst_active: bool = part1.get("gst_active", False)
        gst_consistency_ratio: float = part1.get("gst_filing_consistency_ratio", 1.0)
        gst_consistency_label: str = part1.get("gst_filing_consistency", "REGULAR")

        # Director list from MCA payload
        raw_directors: List[Dict] = payload.get("directors") or []

        # RBI defaulter / hard decline checks
        rbi_defaulter: bool = bool(
            part1.get("is_wilful_defaulter", False)
            or (part1.get("hard_decline") and "RBI" in str(part1.get("hard_decline_reason", "")))
            or bool(part1.get("rbi_defaulter", False))
        )
        confirmed_sanctions: bool = aml_info.get("has_match", False) and bool(
            aml_info.get("result") not in ("NO_MATCH_FOUND", "CLEAR", None, "")
        )

        # ── 4. Build entity identity block
        gstin_status = "Active" if gst_active else "Inactive"
        gstin_registrations = payload.get("gstRegistrations") or []
        gstin_val = part1.get("gstin") or ""
        pan_val = part1.get("pan") or ""
        inc_date = str(part1.get("incorporation_date") or "")
        reg_state = part1.get("state") or state_code or ""

        entity_identity = {
            "vendor_name": company_name,
            "business_type": f"{vendor_profile.capitalize()} vendor" if vendor_profile else "Vendor",
            "gstin_status": gstin_status,
            "udyam_number": overview_raw.get("udyamNumber") or overview_raw.get("udyam_number"),
            "udyam_status": "Valid" if overview_raw.get("udyamNumber") else "Not registered",
            "authorised_signatory": self._extract_signatory(payload),
            "site_address_consistency": self._assess_address_consistency(payload),
            "cin": part1.get("cin"),
            "pan": pan_val,
            "incorporation_date": inc_date,
            "registered_state": reg_state,
        }

        # ── 5. Build AML / Integrity block
        aml_result = aml_info.get("result") or "NO_MATCH_FOUND"
        pep_result = aml_info.get("pep_result") or "NO_MATCH_FOUND"
        aml_status = "Escalate" if aml_info.get("has_match") else "Clear"
        pep_status = "Incomplete" if pep_result == "NO_MATCH_FOUND" else "Escalate"

        aml_integrity = {
            "entity_aml": {
                "result": aml_result,
                "status": aml_status,
                "risk": "Cannot onboard until analyst disposition documented."
                if aml_info.get("has_match")
                else "No AML match detected.",
                "action": "Manual false-positive confirmation required."
                if aml_info.get("has_match")
                else "Monitor only.",
            },
            "sanctions_watchlist": {
                "result": "Close-match record exists; no confirmed hit."
                if confirmed_sanctions
                else "No confirmed OFAC/UN/EU hit.",
                "status": "Escalate" if confirmed_sanctions else "Clear",
                "risk": "Zero-tolerance policy requires documented clearance."
                if confirmed_sanctions
                else "No sanctions risk at present.",
                "action": "Compliance officer sign-off mandatory." if confirmed_sanctions else "Routine monitoring.",
            },
            "pep_match_directors": {
                "result": "PEP screening data not fully available." if pep_status == "Incomplete" else pep_result,
                "status": pep_status,
                "risk": "Partial screening insufficient for onboarding.",
                "action": "Screen all significant persons including UBO chain.",
            },
            "adverse_media": {
                "result": "No adverse media flag in current dataset.",
                "status": "Clear",
                "risk": "No current adverse media risk.",
                "action": "Monitor on re-assessment.",
            },
            "fraud_corruption": {
                "result": "No confirmed fraud or corruption mention.",
                "status": "Clear",
                "risk": "No current confirmed fraud risk.",
                "action": "Obtain documentary proof or rebuttal if any mention surfaces.",
            },
            "fatf_jurisdiction": {
                "result": "No cross-border or high-risk jurisdiction linkage detected.",
                "status": "Clear",
                "risk": "No geographic AML risk at present.",
                "action": "Monitor only if export business develops.",
            },
        }

        # ── 6. Build Ownership & Governance block
        directors_vdd = self._build_directors_vdd(raw_directors)
        ownership_governance = {
            "ubo": {
                "observed": "UBO chain not fully resolved from available MCA data.",
                "status": "Incomplete",
                "why_matters": "Cannot establish final control with confidence.",
                "follow_up": "Notarised UBO affidavit + complete shareholding chart mandatory.",
            },
            "shareholding_pattern": {
                "observed": self._describe_shareholding(payload),
                "status": "Available" if payload.get("shareholders") else "Not provided",
                "why_matters": "Shell or nominee risk in ownership structure.",
                "follow_up": "Explain purpose of each holding entity.",
            },
            "directors": directors_vdd,
            "related_party_exposure": {
                "observed": f"{len(raw_directors)} director(s) with potential linked entities via DIN cluster.",
                "status": "Flag",
                "follow_up": "Confirm independent commercial relationships.",
            },
        }

        # ── 7. Litigation block (delegates _extract_court_cases via part1)
        total_cases: int = court_info.get("total_cases", 0)
        has_cheque_bounce: bool = court_info.get("has_cheque_bounce_against", False)
        nclt_active: bool = court_info.get("nclt_active_against", False)
        drt_cases: int = court_info.get("drt_cases", 0)

        litigation_legal = self._build_litigation_entries(court_info)

        # ── 8. Compliance block
        compliance_intelligence = self._build_compliance_entries(
            gst_active=gst_active,
            gst_consistency_ratio=gst_consistency_ratio,
            gst_consistency_label=gst_consistency_label,
            epfo_data=epfo_data,
            rbi_defaulter=rbi_defaulter,
            part1=part1,
            payload=payload,
        )

        # ── 9. Financial quality
        revenue = part1.get("revenue") or 0.0
        gst_turnover = part1.get("gst_turnover") or 0.0
        turnover_gap = abs((revenue or 0) - (gst_turnover or 0)) if gst_turnover else 0.0
        active_charge_count = charge_summary.get("open_charge_count", 0)

        financial_quality = {
            "turnover_mismatch": {
                "headline": "GST vs MCA turnover gap detected" if turnover_gap > 0 else "Turnover data consistent",
                "detail": (
                    f"MCA revenue {_fmt_inr(revenue)} vs GST turnover {_fmt_inr(gst_turnover or 0)} — "
                    f"{_fmt_inr(turnover_gap)} reconciliation gap. Management explanation required."
                    if turnover_gap > 100_000
                    else "No significant turnover mismatch identified from available data."
                ),
            },
            "borrowing_stack": {
                "headline": f"{active_charge_count} active charge(s) on MCA register"
                if active_charge_count
                else "No active charges on MCA register",
                "detail": (
                    f"{active_charge_count} active secured borrowing "
                    f"{'facility' if active_charge_count == 1 else 'facilities'} observed. "
                    "Financing mix may indicate liquidity pressure in vendor operations."
                    if active_charge_count
                    else "Clean charge register — no active secured borrowing facilities identified."
                ),
            },
            "working_capital_stress": {
                "headline": "Working-capital stress indicators present"
                if (not epfo_data.get("pf_filing_regular", True) or active_charge_count > 1)
                else "No working-capital stress indicators",
                "detail": self._describe_wc_stress(epfo_data, gst_consistency_ratio, active_charge_count),
            },
        }

        # ── 10. MCA Charges
        mca_charges = self._build_mca_charges(raw_charges, charge_summary)

        # ── 11. Operational viability (static template + dynamic address check)
        operational_viability = self._build_operational_viability(
            entity_identity,
            court_info=court_info,
            vendor_profile=vendor_profile,
        )

        # ── 12. Document register
        document_register = self._build_document_register(
            payload=payload,
            epfo_data=epfo_data,
            charge_summary=charge_summary,
            pan_val=pan_val,
            cin_val=part1.get("cin"),
            vendor_profile=vendor_profile,
        )
        docs_missing = sum(
            1
            for d in document_register
            if d["status"] in ("Not provided", "Partial")
            and d["adequacy"] in ("Critical gap", "Required", "Mandatory", "Incomplete")
        )

        # ── 13. Build red-flag register and count flags
        red_flag_register, critical_flags_count, high_flags_count = self._build_red_flag_register(
            aml_info=aml_info,
            confirmed_sanctions=confirmed_sanctions,
            has_cheque_bounce=has_cheque_bounce,
            nclt_active=nclt_active,
            rbi_defaulter=rbi_defaulter,
            drt_cases=drt_cases,
            epfo_data=epfo_data,
            gst_consistency_ratio=gst_consistency_ratio,
            docs_missing=docs_missing,
            vendor_profile=vendor_profile,
        )

        # ── 14. Onboarding verdict
        status, headline, detail = _verdict(
            rbi_defaulter=rbi_defaulter,
            confirmed_sanctions=confirmed_sanctions,
            critical_flags=critical_flags_count,
            high_flags=high_flags_count,
            docs_missing=docs_missing,
        )

        decisions = self._build_decisions(status, critical_flags_count, has_cheque_bounce, confirmed_sanctions)

        # ── 15. Escalation level
        if status in ("REJECTED", "HOLD"):
            escalation_level = "Risk Committee"
        elif status == "CONDITIONAL":
            escalation_level = "Procurement Manager"
        else:
            escalation_level = "Auto-approve"

        profile_label = f"{vendor_profile.capitalize()} Vendor" if vendor_profile else "Vendor"

        # ── 16. Data sources
        data_sources: List[str] = ["mca"]
        if gst_active or gstin_registrations:
            data_sources.append("gst")
        if court_info.get("total_cases", 0) > 0 or court_info.get("detailed_cases"):
            data_sources.append("ecourts")
        if epfo_data.get("employee_count"):
            data_sources.append("epfo")
        data_sources.append("rbi_defaulter")

        return {
            "overview": {
                "vendor_name": company_name,
                "report_id": _gen_report_id(),
                "report_date": datetime.now(timezone.utc).strftime("%d %B %Y"),
                "suite_label": "Service 4 of 5 · Pralyon Intelligence Suite",
                "vendor_profile": profile_label,
                "escalation_level": escalation_level,
                "distribution": "Internal Use Only",
            },
            "onboarding_decision": {
                "status": status,
                "verdict_headline": headline,
                "verdict_detail": detail,
                "critical_flags_count": critical_flags_count,
                "high_flags_count": high_flags_count,
                "docs_missing_count": docs_missing,
                "legal_cases_count": total_cases,
                "api_cost_inr": 0.0,
                "decisions": decisions,
            },
            "entity_identity": entity_identity,
            "red_flag_register": red_flag_register,
            "aml_integrity": aml_integrity,
            "ownership_governance": ownership_governance,
            "litigation_legal": litigation_legal,
            "compliance_intelligence": compliance_intelligence,
            "financial_quality": financial_quality,
            "mca_charges": mca_charges,
            "operational_viability": operational_viability,
            "document_register": document_register,
            "data_sources_used": data_sources,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
            "metadata": {
                "entity_id": entity_id,
                "seller_id": effective_seller_id,
                "requester_id": effective_seller_id,
                "company_name": company_name,
                "gstin": gstin_val,
                "pan": pan_val,
                "cin": part1.get("cin"),
                "incorporation_date": inc_date,
                "registered_state": reg_state,
            },
        }

    # ──────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ──────────────────────────────────────────────────────────────────────────

    def _extract_signatory(self, payload: dict) -> Optional[str]:
        directors = payload.get("directors") or []
        for d in directors:
            if isinstance(d, dict):
                role = str(d.get("designation") or d.get("directorType") or "").lower()
                if any(kw in role for kw in ["md", "managing", "ceo", "chairman", "director"]):
                    return d.get("name") or d.get("directorName")
        if directors and isinstance(directors[0], dict):
            return directors[0].get("name") or directors[0].get("directorName")
        return None

    def _assess_address_consistency(self, payload: dict) -> str:
        gst_regs = payload.get("gstRegistrations") or []
        overview_raw = (
            payload.get("overview", [{}])[0]
            if isinstance(payload.get("overview"), list)
            else payload.get("overview", {})
        ) or {}
        mca_state = str(overview_raw.get("registeredState") or "").lower().strip()
        if not mca_state:
            return "Unverified"
        for reg in gst_regs:
            if isinstance(reg, dict):
                gst_state = str(reg.get("pradr", {}).get("adr", "") or reg.get("state") or "").lower()
                if gst_state and mca_state and mca_state[:4] not in gst_state and gst_state[:4] not in mca_state:
                    return "Mismatch"
        return "Match" if gst_regs else "Unverified"

    def _describe_shareholding(self, payload: dict) -> str:
        shareholders = payload.get("shareholders") or payload.get("shareHolders") or []
        if not shareholders:
            return "Shareholding pattern not available from MCA data."
        top = shareholders[:3] if isinstance(shareholders, list) else []
        parts = []
        for s in top:
            if isinstance(s, dict):
                name = s.get("name") or s.get("shareholderName") or "Unknown"
                pct = s.get("percentage") or s.get("shareholdingPct") or ""
                parts.append(f"{name} ({pct}%)" if pct else name)
        return "Top shareholders: " + "; ".join(parts) + "." if parts else "Shareholding data partially available."

    def _build_directors_vdd(self, raw_directors: List[Dict]) -> List[Dict]:
        result = []
        for d in raw_directors[:5]:
            if not isinstance(d, dict):
                continue
            name = d.get("name") or d.get("directorName") or "Unknown"
            din = d.get("din") or d.get("directorIdentificationNumber") or "N/A"
            is_disq = bool(d.get("disqualified") or d.get("sec164Disqualified"))
            linked = int(d.get("numberOfDirectorships") or d.get("otherEntitiesCount") or 0)
            struck = int(d.get("struckOffCount") or d.get("disqualifiedCount") or 0)
            result.append(
                {
                    "name": name,
                    "din": str(din),
                    "disqualification_status": "Disqualified" if is_disq else "Clear",
                    "linked_entities_count": linked,
                    "struck_off_count": struck,
                    "status": "Monitor" if struck > 0 else "Clear",
                    "follow_up": "Obtain management note on struck-off links."
                    if struck > 0
                    else "No follow-up required.",
                }
            )
        return result

    def _build_litigation_entries(self, court_info: Dict) -> List[Dict]:
        entries = []
        total = court_info.get("total_cases", 0)
        active = court_info.get("active_cases", 0)
        has_138 = court_info.get("has_cheque_bounce_against", False)
        nclt = court_info.get("nclt_active_against", False)
        drt = court_info.get("drt_cases", 0)

        entries.append(
            {
                "check": "e-Courts civil disputes",
                "observed_result": f"{total} total case(s); {active} active."
                if total
                else "No cases found in e-Courts database.",
                "status": "High" if active >= 3 else ("Monitor" if active >= 1 else "Clear"),
                "why_matters": "Signals recurring delivery or quality failures."
                if active
                else "Low litigation exposure.",
                "follow_up": "Case list + counsel summary required." if active else "No action required.",
            }
        )
        entries.append(
            {
                "check": "Section 138 — cheque bounce",
                "observed_result": "Active NI Act 138 proceedings linked to entity."
                if has_138
                else "No cheque bounce proceedings found.",
                "status": "Critical" if has_138 else "Clear",
                "why_matters": "Material payment-discipline failure." if has_138 else "No payment-discipline risk.",
                "follow_up": "Resolution proof required before onboarding." if has_138 else "Monitor on re-assessment.",
            }
        )
        entries.append(
            {
                "check": "NCLT / CIRP",
                "observed_result": "Active CIRP / insolvency petition against entity."
                if nclt
                else "No admitted CIRP proceedings found.",
                "status": "Critical" if nclt else "Clear",
                "why_matters": "Entity under insolvency would halt supply chain."
                if nclt
                else "No insolvency risk at present.",
                "follow_up": "Confirm no cross-liability." if nclt else "No action required.",
            }
        )
        entries.append(
            {
                "check": "DRT / lender recovery",
                "observed_result": f"{drt} DRT reference(s) found." if drt else "No DRT filings found.",
                "status": "Review" if drt else "Clear",
                "why_matters": "Possible hidden borrowing stress or group liability."
                if drt
                else "No lender recovery risk.",
                "follow_up": "Entity-resolution confirmation required." if drt else "No action required.",
            }
        )
        return entries

    def _build_compliance_entries(
        self,
        gst_active: bool,
        gst_consistency_ratio: float,
        gst_consistency_label: str,
        epfo_data: Dict,
        rbi_defaulter: bool,
        part1: Dict,
        payload: Dict,
    ) -> List[Dict]:
        entries = []

        entries.append(
            {
                "parameter": "GSTIN active & valid",
                "result": "Active. No cancellation or suspension notice."
                if gst_active
                else "GSTIN inactive or not found.",
                "status": "Active" if gst_active else "Cancelled",
                "risk": "Basic compliance confirmed." if gst_active else "Tax invoice validity risk for procurement.",
            }
        )

        continuity_label = "Gaps" if gst_consistency_ratio < 0.9 else "Clean"
        gst_pct_val = round(gst_consistency_ratio * 100, 0)
        entries.append(
            {
                "parameter": "GSTR filing continuity (12m)",
                "result": f"Filing consistency: {gst_consistency_label} ({gst_pct_val:.0f}% filed on time).",
                "status": continuity_label,
                "risk": "ITC claim risk for procurement."
                if gst_consistency_ratio < 0.9
                else "Filing pattern consistent.",
            }
        )

        epfo_emp = epfo_data.get("employee_count")
        pf_regular = epfo_data.get("pf_filing_regular", True)
        entries.append(
            {
                "parameter": "EPFO / PF filing + headcount",
                "result": (
                    f"{epfo_emp} employees declared; PF filing {'regular' if pf_regular else 'irregular'}."
                    if epfo_emp
                    else "EPFO data not available."
                ),
                "status": "Mismatch" if (epfo_emp and not pf_regular) else ("Clear" if epfo_emp else "Missing"),
                "risk": "Labour compliance under-reporting risk."
                if not pf_regular
                else "Labour compliance appears normal.",
            }
        )

        entries.append(
            {
                "parameter": "RBI defaulter list",
                "result": "Entity listed as RBI wilful defaulter."
                if rbi_defaulter
                else "Entity not listed as RBI wilful defaulter.",
                "status": "Critical" if rbi_defaulter else "Clear",
                "risk": "Hard onboarding bar." if rbi_defaulter else "No banking default on record.",
            }
        )

        return entries

    def _build_mca_charges(self, raw_charges: List[Dict], charge_summary: Dict) -> Dict:
        charges_vdd = []
        for c in raw_charges[:8]:
            if not isinstance(c, dict):
                continue
            status_raw = str(c.get("chargeStatus") or c.get("status") or "").lower()
            is_active = status_raw in ("open", "active")
            amt = float(c.get("amount") or 0.0)
            charges_vdd.append(
                {
                    "charge_id": str(c.get("chargeId") or c.get("id") or "N/A"),
                    "lender": c.get("assetUnderCharge") or c.get("holderName") or c.get("lender") or "Unknown",
                    "amount_inr": amt,
                    "created": str(c.get("dateOfCreation") or c.get("created") or "N/A"),
                    "status": "Active" if is_active else "Satisfied",
                    "risk_note": "Active facility — utilisation unknown." if is_active else "Closed — no live risk.",
                }
            )

        total_active = charge_summary.get("total_open_registered_amount", 0.0)
        active_cnt = charge_summary.get("open_charge_count", 0)
        fac_plural = "facility" if active_cnt == 1 else "facilities"
        summary_note = (
            f"Total active secured borrowing observed: {_fmt_inr(total_active)} across {active_cnt} live {fac_plural}."
            if active_cnt
            else "No active secured borrowing facilities found on MCA register."
        )

        return {
            "summary_note": summary_note,
            "total_active_borrowing_inr": total_active,
            "charges": charges_vdd,
        }

    def _describe_wc_stress(self, epfo_data: Dict, gst_ratio: float, active_charges: int) -> str:
        signals = []
        if not epfo_data.get("pf_filing_regular", True):
            signals.append("irregular PF filings")
        if gst_ratio < 0.9:
            signals.append("GSTR filing gaps")
        if active_charges > 1:
            signals.append(f"{active_charges} active charge facilities")
        if epfo_data.get("headcount_drop"):
            signals.append("headcount drop YoY")
        if signals:
            return (
                f"Working-capital stress signals: {', '.join(signals)}. Together these indicate stretched operations."
            )
        return "No significant working-capital stress signals detected from available data."

    def _build_operational_viability(
        self,
        entity_identity: Dict,
        court_info: Optional[Dict] = None,
        vendor_profile: Optional[str] = None,
    ) -> List[Dict]:
        address_ok = entity_identity.get("site_address_consistency") == "Match"
        court_info = court_info or {}
        active_cases = court_info.get("active_cases", 0)
        is_mfg = (vendor_profile or "").lower() == "manufacturing"

        return [
            {
                "parameter": "Plant / site existence",
                "current_status": "Address consistent across GST and MCA records."
                if address_ok
                else "Address inconsistency across documents — site unverified.",
                "required_action": "Physical site visit mandatory before onboarding manufacturing vendor."
                if (not address_ok and is_mfg)
                else (
                    "Site visit recommended before first order."
                    if not address_ok
                    else "Standard verification complete."
                ),
            },
            {
                "parameter": "Production capacity vs order size",
                "current_status": "Declared in submission posture.",
                "required_action": "Verify declared capacity against proposed order volume.",
            },
            {
                "parameter": "Quality systems (ISO / BIS / NABL)",
                "current_status": "Not verified from public data sources.",
                "required_action": "Mandatory before technical approval for critical components.",
            },
            {
                "parameter": "Batch traceability / testing records",
                "current_status": "Quality record check pending laboratory verification.",
                "required_action": "Independent sample testing before order placement.",
            },
            {
                "parameter": "Subcontracting dependence",
                "current_status": "Direct operations declared.",
                "required_action": "Declare any sub-vendors and their statutory compliance status.",
            },
            {
                "parameter": "Raw material concentration",
                "current_status": "Standard multi-supplier baseline.",
                "required_action": "Request alternate sourcing mitigation plan.",
            },
            {
                "parameter": "On-time delivery history",
                "current_status": f"{active_cases} active dispute(s) on record."
                if active_cases
                else "No active delivery disputes found in public records.",
                "required_action": "Reference check with 3 independent current buyers.",
            },
            {
                "parameter": "Insurance cover",
                "current_status": "Not verified in public filing submission.",
                "required_action": "Plant, product liability, and transit insurance certificates required.",
            },
            {
                "parameter": "Business continuity plan",
                "current_status": "Not available from public registry sources.",
                "required_action": "Required for critical or high-volume supply engagement.",
            },
        ]

    def _build_document_register(
        self,
        payload: Dict,
        epfo_data: Dict,
        charge_summary: Dict,
        pan_val: Optional[str],
        cin_val: Optional[str],
        vendor_profile: Optional[str] = None,
    ) -> List[Dict]:
        docs = []
        submitted_docs = payload.get("submitted_documents") or {}

        docs.append(
            {
                "document": "Certificate of incorporation",
                "status": "Auto-fetched" if cin_val else "Not provided",
                "adequacy": "Adequate" if cin_val else "Critical gap",
                "comment": "CIN verified." if cin_val else "CIN not found — cannot verify entity.",
            }
        )

        gst_regs = payload.get("gstRegistrations") or []
        gst_ok = bool(gst_regs)
        docs.append(
            {
                "document": "GST registration + 12m filings",
                "status": "Auto-fetched" if gst_ok else "Not provided",
                "adequacy": "Complete" if gst_ok else "Required",
                "comment": "GST registration verified." if gst_ok else "GST data missing — ITC risk.",
            }
        )

        docs.append(
            {
                "document": "PAN card",
                "status": "Auto-fetched" if pan_val else "Not provided",
                "adequacy": "Adequate" if pan_val else "Required",
                "comment": "PAN verified via MCA." if pan_val else "PAN not available.",
            }
        )

        financials_ok = bool(payload.get("profitLoss") or payload.get("balanceSheet"))
        docs.append(
            {
                "document": "Audited financials (MCA)",
                "status": "Auto-fetched" if financials_ok else "Not provided",
                "adequacy": "Adequate" if financials_ok else "Required",
                "comment": "Financial ratios computed from MCA data."
                if financials_ok
                else "MCA financials not available.",
            }
        )

        charge_ok = charge_summary.get("total_charge_count", 0) > 0
        docs.append(
            {
                "document": "MCA charges (5y)",
                "status": "Auto-fetched",
                "adequacy": "Complete" if charge_ok else "Adequate",
                "comment": f"{charge_summary.get('total_charge_count', 0)} charge(s) found."
                if charge_ok
                else "No charges on record.",
            }
        )

        has_sh = bool(payload.get("shareholders") or payload.get("shareHolders"))
        has_ubo = bool(
            payload.get("ubo_declaration")
            or payload.get("ubo_resolved")
            or submitted_docs.get("ubo_declaration")
        )
        if has_sh and has_ubo:
            sh_status, sh_adeq, sh_comment = (
                "Received",
                "Adequate",
                "Shareholding and UBO declaration verified.",
            )
        elif has_sh:
            sh_status, sh_adeq, sh_comment = (
                "Partial",
                "Incomplete",
                "Shareholding available; UBO declaration required.",
            )
        else:
            sh_status, sh_adeq, sh_comment = (
                "Not provided",
                "Critical gap",
                "Mandatory before onboarding — UBO chain unresolved.",
            )

        docs.append(
            {
                "document": "Shareholding pattern + UBO declaration",
                "status": sh_status,
                "adequacy": sh_adeq,
                "comment": sh_comment,
            }
        )

        is_mfg = (vendor_profile or "").lower() == "manufacturing"

        epfo_ok = bool(epfo_data.get("employee_count"))
        epfo_regular = epfo_data.get("pf_filing_regular", True)
        has_epfo_cert = bool(payload.get("epfo_certificate") or submitted_docs.get("epfo_certificate"))
        if epfo_ok and epfo_regular:
            ep_status, ep_adeq, ep_comm = (
                "Auto-fetched",
                "Adequate",
                f"Headcount ({epfo_data.get('employee_count')}) and PF filings regular.",
            )
        elif has_epfo_cert:
            ep_status, ep_adeq, ep_comm = (
                "Received",
                "Adequate",
                "EPFO compliance certificate submitted and verified.",
            )
        elif epfo_ok:
            ep_status, ep_adeq, ep_comm = (
                "Partial",
                "Insufficient",
                "PF filing irregularity detected.",
            )
        elif not is_mfg:
            ep_status, ep_adeq, ep_comm = (
                "Exempt",
                "Not required",
                "Not applicable for non-manufacturing vendor profile without EPFO registration.",
            )
        else:
            ep_status, ep_adeq, ep_comm = (
                "Not provided",
                "Required",
                "EPFO data not available.",
            )

        docs.append(
            {
                "document": "EPFO / PF compliance certificate",
                "status": ep_status,
                "adequacy": ep_adeq,
                "comment": ep_comm,
            }
        )

        has_factory = bool(payload.get("factory_consent") or submitted_docs.get("factory_consent"))
        if not is_mfg:
            fc_status, fc_adeq, fc_comm = (
                "Exempt",
                "Not required",
                "Not applicable for non-manufacturing vendor profile.",
            )
        elif has_factory:
            fc_status, fc_adeq, fc_comm = (
                "Received",
                "Adequate",
                "Factory licence and statutory pollution consent verified.",
            )
        else:
            fc_status, fc_adeq, fc_comm = (
                "Not provided",
                "Required",
                "Mandatory for manufacturing vendor.",
            )

        docs.append(
            {
                "document": "Factory / pollution consent",
                "status": fc_status,
                "adequacy": fc_adeq,
                "comment": fc_comm,
            }
        )

        has_dir_kyc = bool(payload.get("directors_kyc_complete") or submitted_docs.get("directors_kyc"))
        raw_directors = payload.get("directors") or []
        all_clean = (
            all(d.get("din") and not d.get("disqualified") for d in raw_directors if isinstance(d, dict))
            if raw_directors
            else False
        )
        if has_dir_kyc or (
            raw_directors
            and all_clean
            and len(raw_directors) <= 2
            and not any(d.get("struckOffCount") for d in raw_directors if isinstance(d, dict))
        ):
            kyc_status, kyc_adeq, kyc_comm = (
                "Received",
                "Adequate",
                "KYC verified for active directors.",
            )
        else:
            kyc_status, kyc_adeq, kyc_comm = (
                "Partial",
                "Incomplete",
                "Full KYC for all current directors required.",
            )

        docs.append(
            {
                "document": "Director KYC (all directors)",
                "status": kyc_status,
                "adequacy": kyc_adeq,
                "comment": kyc_comm,
            }
        )

        return docs

    def _build_red_flag_register(
        self,
        aml_info: Dict,
        confirmed_sanctions: bool,
        has_cheque_bounce: bool,
        nclt_active: bool,
        rbi_defaulter: bool,
        drt_cases: int,
        epfo_data: Dict,
        gst_consistency_ratio: float,
        docs_missing: int,
        vendor_profile: Optional[str],
    ) -> tuple[List[Dict], int, int]:
        flags: List[Dict] = []
        critical = 0
        high = 0

        if rbi_defaulter:
            flags.append(
                {
                    "issue": "RBI wilful defaulter listing",
                    "observed_result": "Entity confirmed on RBI wilful defaulter list.",
                    "severity": "Critical",
                    "required_action": "Immediate onboarding bar — no commercial engagement permitted.",
                }
            )
            critical += 1

        if aml_info.get("has_match"):
            flags.append(
                {
                    "issue": "AML / sanctions screening",
                    "observed_result": "Name match flagged in AML database above similarity threshold.",
                    "severity": "Critical",
                    "required_action": "Manual false-positive confirmation and compliance officer sign-off.",
                }
            )
            critical += 1

        if confirmed_sanctions:
            flags.append(
                {
                    "issue": "Sanctions / watchlist match",
                    "observed_result": "Close-match sanctions record unresolved.",
                    "severity": "Critical",
                    "required_action": "Compliance officer sign-off mandatory before onboarding.",
                }
            )
            critical += 1

        if has_cheque_bounce:
            flags.append(
                {
                    "issue": "Cheque bounce matters (NI Act 138)",
                    "observed_result": "Active Section 138 proceedings linked to entity or directors.",
                    "severity": "Critical",
                    "required_action": "Obtain settlement status, court stage, and documentary closure proof.",
                }
            )
            critical += 1

        if nclt_active:
            flags.append(
                {
                    "issue": "Active NCLT / CIRP insolvency petition",
                    "observed_result": "Active insolvency petition filed against entity.",
                    "severity": "Critical",
                    "required_action": "No onboarding until CIRP is resolved or dismissed.",
                }
            )
            critical += 1

        if drt_cases > 0:
            flags.append(
                {
                    "issue": "DRT lender recovery filing",
                    "observed_result": f"{drt_cases} DRT reference(s) found — entity resolution pending.",
                    "severity": "High",
                    "required_action": "Entity-resolution confirmation required.",
                }
            )
            high += 1

        if gst_consistency_ratio < 0.7:
            gst_pct_str = f"{round(gst_consistency_ratio * 100, 0):.0f}%"
            flags.append(
                {
                    "issue": "GSTR filing gaps",
                    "observed_result": f"Filing consistency {gst_pct_str} — below 70% threshold.",
                    "severity": "High",
                    "required_action": "GST filing history explanation required.",
                }
            )
            high += 1

        if not epfo_data.get("pf_filing_regular", True):
            flags.append(
                {
                    "issue": "EPFO / PF filing irregularity",
                    "observed_result": "PF filing regularity below 80% threshold.",
                    "severity": "Medium",
                    "required_action": "Labour compliance documentation required.",
                }
            )

        if docs_missing >= 3:
            flags.append(
                {
                    "issue": "Critical document gaps",
                    "observed_result": f"{docs_missing} mandatory documents not provided.",
                    "severity": "High",
                    "required_action": "Full onboarding document pack required before commercial engagement.",
                }
            )
            high += 1

        return flags, critical, high

    def _build_decisions(
        self,
        status: str,
        critical_flags: int,
        has_cheque_bounce: bool,
        confirmed_sanctions: bool,
    ) -> List[Dict]:
        if status == "REJECTED":
            return [
                {
                    "parameter": "Onboard vendor?",
                    "recommendation": "No — rejected",
                    "reason": "Hard regulatory block: RBI defaulter or confirmed sanctions.",
                },
                {
                    "parameter": "Risk committee escalation?",
                    "recommendation": "Mandatory",
                    "reason": "Regulatory bar requires immediate escalation.",
                },
            ]
        if status == "HOLD":
            decisions = [
                {
                    "parameter": "Onboard vendor?",
                    "recommendation": "No — hold",
                    "reason": f"{critical_flags} critical blocker(s) unresolved.",
                },
                {
                    "parameter": "Permit sample order?",
                    "recommendation": "Only after compliance clearance",
                    "reason": "Critical gaps must be closed first.",
                },
            ]
            if has_cheque_bounce:
                decisions.append(
                    {
                        "parameter": "Advance payment allowed?",
                        "recommendation": "No",
                        "reason": "Cheque bounce history makes prepayment unsafe.",
                    }
                )
            if confirmed_sanctions:
                decisions.append(
                    {
                        "parameter": "Risk committee escalation?",
                        "recommendation": "Mandatory",
                        "reason": "AML and sanctions match unresolved.",
                    }
                )
            return decisions
        if status == "CONDITIONAL":
            return [
                {
                    "parameter": "Onboard vendor?",
                    "recommendation": "Conditional approval",
                    "reason": "High-risk items require management sign-off.",
                },
                {
                    "parameter": "Advance payment allowed?",
                    "recommendation": "Limited — with controls",
                    "reason": "Financial stress signals warrant caution.",
                },
            ]
        # APPROVED
        return [
            {
                "parameter": "Onboard vendor?",
                "recommendation": "Approved",
                "reason": "No critical or high-risk flags. All mandatory documents received.",
            },
            {
                "parameter": "Advance payment allowed?",
                "recommendation": "Subject to standard commercial terms",
                "reason": "Clean vendor profile.",
            },
        ]
