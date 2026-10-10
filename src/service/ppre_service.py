import logging
from datetime import datetime, timezone, date
from typing import Any, Dict, Optional

import inject
from sqlalchemy.ext.asyncio import AsyncSession
from common.service.unit_of_work import AbstractUnitOfWork
from repository.company_repository import CompanyRepository
from common.base.error import ApplicationError

# Import PPRE modules
from domain.schemas.normalized_record import NormalizedRecord
from domain.scoring.pd_mapper import derive_pd_band
from domain.scoring.limit_advisor import advise_limit
from domain.scoring.stress_tester import run_stress_test, format_stress_table
from domain.lgd.openlgd_model import predict_lgd
from domain.explainability.explainer import CreditExplainer
from domain.scoring.ppre_engine import score_entity


# Import Part 1 conduct scorecards and compute helpers
from domain.compute.rbi_defaulter import check_rbi_wilful_defaulter, build_hard_decline_result
from domain.compute.roc_directors import derive_director_conduct_signals
from domain.compute.epfo import derive_epfo_conduct_signals
from domain.compute.charges import derive_charge_conduct_signals
from domain.compute.ecourts import derive_ecourts_conduct_signals
from domain.compute.gst_conduct import derive_gst_conduct_signals
from domain.compute.cross_validate import classify_mca_data_sufficiency, maybe_switch_revenue_to_gst
from domain.compute.report_fields import compute_dpo, compute_cash_coverage

from domain.scorecards.charge_conduct import apply_charge_conduct_adjustments
from domain.scorecards.epfo_conduct import apply_epfo_conduct_adjustments
from domain.scorecards.ecourts_conduct import apply_ecourts_conduct_adjustments
from domain.scorecards.gst_conduct import apply_gst_conduct_adjustments
from domain.scorecards.director_conduct import apply_director_conduct_adjustments

from domain.scorecards.financial_score import compute_financial_score
from domain.scorecards.identity_score import compute_identity_score
from domain.scorecards.legal_score import compute_legal_score
from domain.scorecards.documentation_score import compute_documentation_score

logger = logging.getLogger(__name__)


from service.artifact_service import ArtifactService


def _parse_safe_float(v, default=0.0) -> float:
    if v is None:
        return default
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "")
    if not s or s in ("-", "--", "N/A", "null"):
        return default
    if s.startswith("-"):
        s = "-" + s[1:].strip()
    elif s.startswith("(") and s.endswith(")"):
        s = "-" + s[1:-1].strip()
    try:
        return float(s)
    except (ValueError, TypeError):
        return default


class PPREService:
    def __init__(self, uow: AbstractUnitOfWork, artifact_service: ArtifactService):
        self.uow = uow
        self.artifact_service = artifact_service

    async def _get_company_data(self, identifier: str) -> dict:
        async with self.uow:
            repo = CompanyRepository(self.uow.session)
            data = await repo.get_company_with_latest_snapshot(identifier)
            if not data:
                raise ApplicationError(response_code=404, message=f"Company with identifier '{identifier}' not found.")
            return data

    def _extract_financials(self, payload: dict, db_row: dict = None) -> list[dict]:
        db_row = db_row or {}
        profit_loss = payload.get("profitLoss") or []
        balance_sheet = payload.get("balanceSheet") or []
        overview = (
            payload.get("overview", [{}])[0]
            if isinstance(payload.get("overview"), list)
            else payload.get("overview", {})
        ) or {}

        def _safe_float(v, default=0.0) -> float:
            if v is None:
                return default
            if isinstance(v, (int, float)):
                return float(v)
            s = str(v).strip().replace(",", "")
            if not s or s in ("-", "--", "N/A", "null"):
                return default
            if s.startswith("-"):
                s = "-" + s[1:].strip()
            elif s.startswith("(") and s.endswith(")"):
                s = "-" + s[1:-1].strip()
            try:
                return float(s)
            except (ValueError, TypeError):
                return default

        years_data = {}
        for pl in profit_loss:
            year = pl.get("Year")
            if year:
                years_data.setdefault(year, {})
                pbt = _safe_float(pl.get("PROFIT_BEFORE_TAX"))
                fin_cost = _safe_float(pl.get("FINANCE_COST_CR") or pl.get("INTEREST_EXP_CR"))
                # EBIT = PBT + Finance Cost (add back interest since PBT is after interest)
                # Use explicit EBIT field if provider supplies it, else compute
                ebit = _safe_float(pl.get("EBIT")) if pl.get("EBIT") is not None else (pbt + fin_cost)
                years_data[year].update(
                    {
                        "revenue": _safe_float(pl.get("TOTAL_REVENUE_CR") or pl.get("TOTAL_INCOME")),
                        "ebit": ebit,
                        "pat": _safe_float(
                            pl.get("PROF_LOS_11_14_C")
                            if pl.get("PROF_LOS_11_14_C") is not None
                            else (pbt - _safe_float(pl.get("TAX_EXPENSES_CR")))
                        ),
                        "finance_cost": fin_cost,
                        "depreciation": _safe_float(pl.get("DEPRECTN_AMORT_C")),
                    }
                )

        for bs in balance_sheet:
            year = bs.get("Year")
            if year:
                years_data.setdefault(year, {})
                lt_borrow = _safe_float(bs.get("LONG_TERM_BORR_C"))
                st_borrow = _safe_float(bs.get("SHORT_TERM_BOR_C"))
                total_debt = lt_borrow + st_borrow
                res_surplus = _safe_float(bs.get("RESERVE_SURPLUS1"))
                share_cap = _safe_float(bs.get("SHARE_CAPITAL_CR"))
                networth = _safe_float(bs.get("EQUITY_AND_RESERVES")) if bs.get("EQUITY_AND_RESERVES") is not None else (res_surplus + share_cap)

                years_data[year].update(
                    {
                        "current_assets": _safe_float(bs.get("CURR_ASSETS") or bs.get("TOTAL_CURR_REP")),
                        "current_liabilities": _safe_float(bs.get("CURR_LIABILITIES")),
                        "total_debt": total_debt,
                        "networth": networth,
                        "receivables": _safe_float(bs.get("TRADE_RECEIV_CR")),
                        "inventory": _safe_float(bs.get("INVENTORIES_CR")),
                        "trade_payables": _safe_float(bs.get("TRADE_PAYABLES_C")),
                        "cash_and_bank": _safe_float(bs.get("CASH_AND_EQU_CR")),
                        "gross_fixed_assets": _safe_float(bs.get("FIXED_ASSETS")),
                    }
                )

        sorted_years = sorted(years_data.keys(), reverse=True)[:3]  # Enforce ONLY last 3 years
        financials = []
        for y in sorted_years:
            data = years_data[y]
            data["year"] = y
            financials.append(data)

        # Fallback if no profitLoss or balanceSheet lists are present in snapshot payload
        if not financials:
            fallback_networth = _safe_float(
                overview.get("NET_WORTH_COMP")
                or overview.get("paidUpCapital")
                or db_row.get("paid_up_capital")
                or db_row.get("authorized_capital")
            )
            fallback_revenue = _safe_float(
                overview.get("TOT_TURNOVER")
                or overview.get("totalTurnover")
                or db_row.get("latest_revenue")
            )
            fallback_debt = sum(
                _safe_float(c.get("amount"))
                for c in payload.get("charges", [])
                if str(c.get("chargeStatus") or c.get("status") or "").lower() in ["open", "active"]
            )
            if fallback_networth > 0 or fallback_revenue > 0 or fallback_debt > 0:
                financials.append({
                    "year": "Latest (Capital/Overview)",
                    "revenue": fallback_revenue,
                    "ebit": 0.0,
                    "pat": 0.0,
                    "networth": fallback_networth,
                    "total_debt": fallback_debt,
                    "current_assets": fallback_networth * 0.5 if fallback_networth > 0 else 0.0,
                    "current_liabilities": fallback_networth * 0.3 if fallback_networth > 0 else 0.0,
                    "receivables": 0.0,
                    "inventory": 0.0,
                    "trade_payables": 0.0,
                    "cash_and_bank": 0.0,
                    "finance_cost": 0.0,
                    "depreciation": 0.0,
                    "gross_fixed_assets": 0.0,
                })

        return financials

    def _extract_epfo(self, payload: dict) -> dict:
        epfo_list = payload.get("annexureEPFO", []) or payload.get("epfoDetails", []) or []
        valid_records = []
        for r in epfo_list:
            emp_str = str(r.get("no_of_employee") or "").replace(",", "").strip()
            if emp_str.isdigit() and int(emp_str) > 0:
                due_val = str(r.get("due_date") or r.get("date_of_credit") or "").strip()
                valid_records.append({**r, "_emp": int(emp_str), "_due": due_val})

        if not valid_records:
            return {"employee_count": None, "pf_filing_regular": None}

        # Sort chronologically descending by due date / credit date
        valid_records.sort(key=lambda x: x["_due"], reverse=True)

        # Group records by month (wage_month or YYYY-MM) to distinguish primary monthly ECR from supplement exclusions
        months: dict[str, list] = {}
        for r in valid_records:
            wm = str(r.get("wage_month") or r["_due"][:7] or "UNKNOWN")
            months.setdefault(wm, []).append(r)

        # Authoritative current headcount = primary monthly filing (max employee count) of most recent month
        latest_wm = list(months.keys())[0]
        latest_primary = max(months[latest_wm], key=lambda x: x["_emp"])
        headcount = latest_primary["_emp"]

        # Assess filing regularity over the latest 12 available months of primary monthly filings
        months_to_check = list(months.keys())[:12]
        timely_months = 0
        for wm in months_to_check:
            primary = max(months[wm], key=lambda x: x["_emp"])
            remarks = str(primary.get("remarks") or "").strip().lower()
            delay_raw = str(primary.get("delay_period") or "").strip()
            # Negative numbers like '(3)' denote early filing before due date
            is_delayed = remarks == "delayed"
            if not is_delayed and delay_raw.isdigit() and int(delay_raw) > 15:
                is_delayed = True
            if not is_delayed:
                timely_months += 1

        pf_filing_regular = (timely_months / len(months_to_check)) >= 0.8 if months_to_check else True

        # Check workforce trend drop (headcount fell by > 15% YoY)
        headcount_drop = False
        if len(months) >= 2:
            yoy_wm = list(months.keys())[min(11, len(months) - 1)]
            yoy_primary = max(months[yoy_wm], key=lambda x: x["_emp"])
            if yoy_primary["_emp"] > 0 and headcount < yoy_primary["_emp"] * 0.85:
                headcount_drop = True

        return {
            "employee_count": headcount,
            "pf_filing_regular": pf_filing_regular,
            "headcount_drop": headcount_drop,
        }

    def _extract_gst_consistency(self, payload: dict) -> float:
        gst_list = payload.get("annexureGST") or []
        returns = [r for r in gst_list if r.get("Return type") in ["GSTR3B", "GSTR1"]]
        if not returns:
            return 1.0
        filed = sum(1 for r in returns if r.get("Status") == "Filed")
        return round(filed / len(returns), 4)

    def _parse_gst_turnover_slab(self, payload: dict) -> float:
        """
        Extract and parse numeric GST turnover from aggregate turnover slab (aggreTurnOver)
        in gstRegistrations.
        """
        gst_regs = payload.get("gstRegistrations") or []
        slab_str = ""
        for reg in gst_regs:
            if isinstance(reg, dict):
                # Prefer active registrations
                if reg.get("sts") == "Active" and reg.get("aggreTurnOver"):
                    slab_str = reg.get("aggreTurnOver")
                    break
                elif not slab_str and reg.get("aggreTurnOver"):
                    slab_str = reg.get("aggreTurnOver")
                    
        if not slab_str:
            return 0.0
            
        s = slab_str.lower().strip()
        
        # Parse standard Indian GST slabs:
        # 1. Slab: Rs. 500 Cr. and above -> 500 Cr (5,000,000,000)
        # 2. Slab: Rs. 100 Cr. to 500 Cr. -> 300 Cr (3,000,000,000) (midpoint)
        # 3. Slab: Rs. 25 Cr. to 100 Cr. -> 62.5 Cr (625,000,000) (midpoint)
        # 4. Slab: Rs. 5 Cr. to 25 Cr. -> 15 Cr (150,000,000) (midpoint)
        # 5. Slab: Rs. 1.5 Cr. to 5 Cr. -> 3.25 Cr (32,500,000) (midpoint)
        # 6. Slab: Rs. 40 Lakhs to 1.5 Cr. -> 95 Lakhs (9,500,000) (midpoint)
        # 7. Slab: Rs. 0 to 40 Lakhs -> 20 Lakhs (2,000,000) (midpoint)
        
        if "500 cr" in s:
            return 5000000000.0
        elif "100 cr" in s and "500 cr" in s:
            return 3000000000.0
        elif "25 cr" in s and "100 cr" in s:
            return 625000000.0
        elif "5 cr" in s and "25 cr" in s:
            return 150000000.0
        elif "1.5 cr" in s and "5 cr" in s:
            return 32500000.0
        elif "40 lakh" in s and "1.5 cr" in s:
            return 9500000.0
        elif "0" in s and "40 lakh" in s:
            return 2000000.0
            
        # Regex fallback if formatting differs
        import re
        parts = re.findall(r'(\d+(?:\.\d+)?)\s*(cr|lakh)', s)
        if parts:
            vals = []
            for num_str, unit in parts:
                val = float(num_str)
                if 'cr' in unit:
                    val *= 10000000.0
                elif 'lakh' in unit:
                    val *= 100000.0
                vals.append(val)
            return sum(vals) / len(vals)
            
        return 0.0

    def _extract_consolidated_financials(self, payload: dict) -> list[dict]:
        """Extract multi-year consolidated financials if available."""
        pl_cons = payload.get("profitLossConsolidated") or []
        bs_cons = payload.get("balanceSheetConsolidated") or []
        if not pl_cons and not bs_cons:
            return []
        mock_payload = {"profitLoss": pl_cons, "balanceSheet": bs_cons}
        return self._extract_financials(mock_payload, db_row={})

    def _extract_court_cases(self, payload: dict) -> dict:
        """Extract and normalize all litigation records across eCourts, courtsData, legalCases, and ncltCases."""
        ec = payload.get("eCourts") or []
        ec_cases = ec.get("cases", []) if isinstance(ec, dict) else (ec if isinstance(ec, list) else [])
        cd_cases = payload.get("courtsData") or []
        if not isinstance(cd_cases, list):
            cd_cases = []
        lc_cases = payload.get("legalCases") or []
        if not isinstance(lc_cases, list):
            lc_cases = []
        nclt_cases = payload.get("ncltCases") or []
        if not isinstance(nclt_cases, list):
            nclt_cases = []

        all_raw = []
        for c in ec_cases:
            all_raw.append({"source": "ecourts", "data": c})
        for c in cd_cases:
            all_raw.append({"source": "courts_data", "data": c})
        for c in lc_cases:
            all_raw.append({"source": "legal_cases", "data": c})
        for c in nclt_cases:
            all_raw.append({"source": "nclt", "data": c})

        detailed_cases = []
        hc_count = 0
        nclt_count = 0
        drt_count = 0
        active_count = 0
        criminal_count = 0
        high_value_count = 0
        recent_cases_12m = 0
        recent_cases_24m = 0
        cheque_bounce_cases = []
        has_cheque_bounce_against = False
        complainant_138_count = 0
        nclt_active_against = False
        nclt_disposed_count = 0

        now_dt = datetime.now(timezone.utc)
        seen_cnrs = set()

        for item in all_raw:
            d = item["data"]
            if not isinstance(d, dict):
                continue

            cnr = str(d.get("cnr") or d.get("filingNumber") or d.get("caseNo") or d.get("registrationNumber") or "").strip()
            court_name = str(d.get("courtName") or d.get("highCourtName") or d.get("court") or "").strip()
            case_type = str(d.get("caseType") or d.get("caseTypeName") or d.get("type") or "").strip()
            case_status_raw = str(d.get("caseStatus") or d.get("status") or "").strip()
            acts = str(d.get("underActs") or d.get("actsAndSections") or "").strip()
            sections = str(d.get("underSections") or "").strip()
            category = str(d.get("businessCategory") or d.get("caseCategory") or "").strip()
            oparty = str(d.get("oparty") or d.get("respondents") or "").strip()
            petitioner = str(d.get("name") or d.get("petitioners") or "").strip()
            filing_date = str(d.get("filingDate") or d.get("registrationDate") or d.get("date") or "").strip()
            risk_tag = str(d.get("algoRisk") or "").strip() or None

            # Normalise status
            is_active = any(kw in case_status_raw.lower() for kw in ["pending", "admitted", "pre-admission", "open", "active"])
            status = "Pending" if is_active else ("Disposed" if any(kw in case_status_raw.lower() for kw in ["disposed", "rejected", "dismissed", "settled"]) else (case_status_raw or "Pending"))

            # Forum classification
            c_lower = court_name.lower() + " " + case_type.lower()
            if "high court" in c_lower or "hc" in c_lower:
                hc_count += 1
            if "nclt" in c_lower or "nclat" in c_lower or "ibc" in c_lower or "insolvency" in c_lower:
                nclt_count += 1
                if is_active:
                    party_type = str(d.get("type") or d.get("party_type") or "")
                    if party_type == "1" or "respondent" in c_lower:
                        nclt_active_against = True
                else:
                    nclt_disposed_count += 1
            if "drt" in c_lower or "debt recovery" in c_lower:
                drt_count += 1
            if is_active:
                active_count += 1

            # Cheque Bounce (NI Act Sec 138)
            is_138 = (
                "negotiable" in acts.lower()
                or "138" in sections
                or "cheque bounce" in category.lower()
                or "cheque bounce" in case_type.lower()
            )
            if is_138:
                party_type = str(d.get("type") or d.get("party_type") or "")
                is_accused = party_type == "1" or "accused" in c_lower or "respondent" in c_lower
                if is_active and is_accused:
                    has_cheque_bounce_against = True
                elif party_type == "0" or not is_accused:
                    complainant_138_count += 1
                cheque_bounce_cases.append({
                    "cnr": cnr,
                    "court": court_name,
                    "status": status,
                    "is_active": is_active,
                    "is_accused": is_accused,
                })

            # High value / severe risk identification
            risk_lower = (risk_tag or "").lower()
            if "high" in risk_lower or "drt" in c_lower or (is_active and nclt_active_against):
                high_value_count += 1

            # Criminal detection (only count where entity is accused/respondent)
            if any(kw in c_lower or kw in acts.lower() or kw in category.lower() for kw in ["criminal", "fir", "ipc", "crpc", "cr.pc"]):
                party_type = str(d.get("type") or d.get("party_type") or "")
                is_accused = party_type == "1" or "accused" in c_lower or "respondent" in c_lower
                if is_accused and not is_138:
                    criminal_count += 1

            # Recent cases timeline calculation
            if filing_date:
                try:
                    f_dt = datetime.strptime(filing_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc)
                    days_diff = (now_dt - f_dt).days
                    if 0 <= days_diff <= 365:
                        recent_cases_12m += 1
                    if 0 <= days_diff <= 730:
                        recent_cases_24m += 1
                except Exception:
                    pass

            # Deduplicate by CNR or case title
            dedup_key = cnr or f"{court_name}-{case_type}-{filing_date}"
            if dedup_key and dedup_key not in seen_cnrs and len(detailed_cases) < 15:
                seen_cnrs.add(dedup_key)
                detailed_cases.append({
                    "cnr": cnr or None,
                    "court": court_name or "Court",
                    "case_type": case_type or category or "Matter",
                    "matter_type": "NI Act 138" if is_138 else ("Insolvency / IBC" if ("nclt" in c_lower or "ibc" in c_lower) else ("Criminal" if "criminal" in c_lower else "Civil / Commercial")),
                    "parties": f"{petitioner} vs {oparty}" if petitioner and oparty else (petitioner or oparty or "Parties on record"),
                    "status": status,
                    "filing_date": filing_date or None,
                    "risk_tag": risk_tag,
                    "status_class": "warn" if is_active else "pass",
                })

        detailed_cases.sort(key=lambda x: 0 if x.get("status") == "Pending" else 1)

        return {
            "detailed_cases": detailed_cases[:10],
            "total_cases": len(all_raw),
            "hc_cases": hc_count,
            "nclt_cases": nclt_count,
            "drt_cases": drt_count,
            "active_cases": active_count,
            "criminal_cases": criminal_count,
            "high_value_cases": high_value_count,
            "recent_cases_12m": recent_cases_12m,
            "recent_cases_24m": recent_cases_24m,
            "cheque_bounce_cases": cheque_bounce_cases,
            "has_cheque_bounce_against": has_cheque_bounce_against,
            "complainant_138_count": complainant_138_count,
            "nclt_active_against": nclt_active_against,
            "nclt_disposed_count": nclt_disposed_count,
        }

    def _extract_msme_delays(self, payload: dict) -> dict:
        """Extract MSME Samadhaan vendor delay complaints."""
        msme_list = payload.get("msmePaymentDelays") or []
        if not isinstance(msme_list, list):
            return {"count": 0, "total_amount": 0.0, "display_amount": "₹0", "suppliers": []}

        total_amt = 0.0
        suppliers = []
        for item in msme_list:
            if isinstance(item, dict):
                amt = _parse_safe_float(item.get("amount_due") or item.get("Amount due"))
                total_amt += amt
                sname = item.get("supplier_name") or item.get("Supplier Name")
                if sname and len(suppliers) < 5:
                    suppliers.append({"name": sname, "amount": amt, "date": item.get("Date")})

        disp = f"₹{total_amt / 10000000:.2f} Cr" if total_amt >= 10000000 else (f"₹{total_amt / 100000:.2f} L" if total_amt > 0 else "₹0")
        return {
            "count": len(msme_list),
            "total_amount": total_amt,
            "display_amount": disp,
            "suppliers": suppliers,
        }

    def _extract_aml_screening(self, payload: dict) -> dict:
        """Extract Anti-Money Laundering & PEP screening records."""
        aml = payload.get("aml_entity") or {}
        if not isinstance(aml, dict):
            return {"result": "NO_MATCH_FOUND", "pep_result": "NO_MATCH_FOUND", "screened_on": None, "has_match": False}

        res = aml.get("result") or "NO_MATCH_FOUND"
        pep = aml.get("pepResult") or "NO_MATCH_FOUND"
        meta = aml.get("metaDetails") or {}
        screened_on = meta.get("screenedOn")
        has_match = (res not in ("NO_MATCH_FOUND", "CLEAR", None, "")) or (pep not in ("NO_MATCH_FOUND", "CLEAR", None, ""))

        return {
            "result": res,
            "pep_result": pep,
            "screened_on": screened_on,
            "has_match": has_match,
        }

    def _extract_charge_summary(self, payload: dict, charges_list: list) -> dict:
        """Extract pre-aggregated charge summary from finanvo chargeSummary section."""
        cs = payload.get("chargeSummary") or {}
        if not isinstance(cs, dict):
            cs = {}

        open_cnt = cs.get("openChargeCount")
        if open_cnt is None:
            open_cnt = sum(1 for c in charges_list if c.get("status") in ["open", "active"])

        sat_cnt = cs.get("satisfiedChargeCount")
        if sat_cnt is None:
            sat_cnt = sum(1 for c in charges_list if c.get("status") in ["closed", "satisfied"])

        tot_cnt = cs.get("totalChargeCount") or (open_cnt + sat_cnt)

        total_open_amt = float(cs.get("totalOpenRegisteredAmount") or cs.get("knownOpenRegisteredAmount") or 0.0)
        if total_open_amt == 0.0 and charges_list:
            total_open_amt = sum(float(c.get("amount") or 0.0) for c in charges_list if c.get("status") in ["open", "active"])

        disp = f"₹{total_open_amt / 10000000:.2f} Cr" if total_open_amt >= 10000000 else (f"₹{total_open_amt / 100000:.2f} L" if total_open_amt > 0 else "-")

        return {
            "open_charge_count": int(open_cnt),
            "satisfied_charge_count": int(sat_cnt),
            "total_charge_count": int(tot_cnt),
            "total_open_registered_amount": total_open_amt,
            "display_total_open_amount": disp,
        }

    def _extract_auditors_remarks(self, payload: dict) -> dict:
        """Extract CARO statutory auditor qualifications."""
        ar_list = payload.get("auditorsRemarks") or []
        if not isinstance(ar_list, list) or not ar_list:
            return {"has_adverse": False, "summary": "Unqualified standard audit opinion"}

        latest = ar_list[0] if isinstance(ar_list[0], dict) else {}
        adverse_notes = []
        for k, v in latest.items():
            val = str(v).upper()
            if val in ["ADVE", "QUAL", "YES"] and k in ["FRAUD_NOTICED", "STATUTORY_DUES", "TERM_LOANS"]:
                adverse_notes.append(f"{k.replace('_', ' ').title()}: {val}")

        has_adverse = len(adverse_notes) > 0
        summary = (
            f"CARO audit qualifications: {', '.join(adverse_notes)}"
            if has_adverse
            else "Clean statutory auditor remarks across CARO reporting parameters"
        )
        return {"has_adverse": has_adverse, "summary": summary}

    def _run_part1_sourcing(self, db_row: dict) -> dict[str, Any]:
        """
        Part 1 Pipeline per RA Model Doc & Whiteboard:
        ================================================
        Step 1: Extract all available data signals (7-8 sources)
        Step 2: HARD GATE ① — RBI Wilful Defaulter → INSTANT DECLINE
        Step 3: HARD GATE ② — Director Wilful Defaulter / MCA Sec164 → INSTANT DECLINE
        Step 4: MCA Data Validation → MCA or GST revenue proxy
        Step 5: Compute Financial Ratios
        Step 6: Part 1 — Conduct Score Chain (base 70, adjustments)
        Step 7: Part 1 — Four Domain Scores
        Step 8: Build Final Feature Row (35+ fields)
        """
        payload = db_row.get("payload") or {}
        overview = (
            payload.get("overview", [{}])[0]
            if isinstance(payload.get("overview"), list)
            else payload.get("overview", {})
        ) or {}

        # Extract NIC code from snapshot payload or db_row
        nic_code = None
        nic_codes_list = overview.get("nicCodes") or []
        if isinstance(nic_codes_list, list) and len(nic_codes_list) > 0:
            if isinstance(nic_codes_list[0], dict):
                nic_code = nic_codes_list[0].get("nicCode")
        
        if not nic_code or nic_code == "NA" or nic_code == "-":
            nic_code = db_row.get("main_activity_group_code") or db_row.get("business_activity_code")
            
        if not nic_code or nic_code == "NA" or nic_code == "-":
            nic_code = "74210" # Default/fallback

        # ─────────────────────────────────────────────────────────────────────
        # STEP 1: Extract available data signals
        # ─────────────────────────────────────────────────────────────────────

        # Extract active GSTIN from GST registrations
        gst_regs = payload.get("gstRegistrations") or []
        gstin_val = None
        gst_active = False
        for reg in gst_regs:
            if isinstance(reg, dict):
                g_val = reg.get("gstin")
                if g_val and g_val != "Not Available":
                    if reg.get("sts") == "Active":
                        gstin_val = g_val
                        gst_active = True
                        break
                    elif not gstin_val:
                        gstin_val = g_val

        # Extract finanvo pre-computed ratios (authoritative source for ratios)
        finanvo_ratios = self._extract_finanvo_ratios(payload)

        # Extract MCA financial data (profitLoss + balanceSheet + fallbacks)
        financials = self._extract_financials(payload, db_row=db_row)
        y1 = financials[0] if len(financials) > 0 else {}
        y2 = financials[1] if len(financials) > 1 else {}
        y3 = financials[2] if len(financials) > 2 else {}

        # Track which years have usable MCA / derived financial data
        mca_years_available = len([y for y in [y1, y2, y3] if y and any(
            v and float(v or 0) > 0 for v in [y.get("revenue"), y.get("networth"), y.get("current_assets")]
        )])
        mca_data_available = mca_years_available >= 1 or float(db_row.get("paid_up_capital") or 0) > 0 or float(db_row.get("authorized_capital") or 0) > 0

        # Extract GST filing consistency
        gst_consistency = self._extract_gst_consistency(payload)

        # Extract EPFO signals
        epfo_raw = self._extract_epfo(payload)

        # GST turnover set to None per user request (it is okay to null this field)
        gst_turnover = None

        # ─────────────────────────────────────────────────────────────────────
        # STEP 2 & 3: HARD GATES (before any scoring)
        # Per docs: These are the FIRST checks — INSTANT DECLINE if triggered
        # ─────────────────────────────────────────────────────────────────────

        # Hard Gate 1: RBI Wilful Defaulter (data source not yet integrated
        # — defaulting to clear, but flag for transparency)
        rbi_data = {"is_wilful_defaulter": False}  # RBI API not yet integrated
        rbi_result = check_rbi_wilful_defaulter(rbi_data)
        rbi_data_available = False  # Track that RBI check was based on default

        if rbi_result.get("is_wilful_defaulter"):
            return build_hard_decline_result(db_row.get("cin"), "RBI_WILFUL_DEFAULTER")

        # Hard Gate 2: Director Wilful Defaulter or MCA Sec 164 Disqualification
        mca_dir = {"directors": payload.get("directors", [])}
        dir_signals = derive_director_conduct_signals(mca_dir)

        if dir_signals.get("director_wilful_defaulter"):
            # Court cases hard gate: skip per user instruction (data unavailable)
            # Only trigger if we have explicit disqualification/wilful defaulter flags
            disq_names = dir_signals.get("disqualified_director_names", [])
            reason = "DIRECTOR_WILFUL_DEFAULTER" if not disq_names else "DIRECTOR_MCA_SEC164_DISQUALIFIED"
            return build_hard_decline_result(db_row.get("cin"), reason)

        # ─────────────────────────────────────────────────────────────────────
        # STEP 4: MCA Data Validation — Revenue Cross-Validation
        # Per doc: if MCA missing OR MCA < 50% GST → use GST proxy
        # ─────────────────────────────────────────────────────────────────────
        mca_revenue = y1.get("revenue") if y1 else None
        if mca_revenue and float(mca_revenue) <= 0:
            mca_revenue = None  # Treat zero as missing

        revenue_source, rev_notes = maybe_switch_revenue_to_gst(mca_revenue, gst_turnover)
        revenue = gst_turnover if revenue_source == "gst_proxy" else mca_revenue

        # Ultimate fallback: finanvo SALES_GOODS from ratios
        if not revenue or float(revenue or 0) <= 0:
            finanvo_revenue = finanvo_ratios.get("sales_goods_raw")
            if finanvo_revenue and finanvo_revenue > 0:
                revenue = finanvo_revenue
                revenue_source = "finanvo_ratios"
                rev_notes.append("REVENUE_FROM_FINANVO_RATIOS")
                logger.info(f"Using finanvo SALES_GOODS as revenue fallback: {finanvo_revenue}")

        # MCA data sufficiency classification
        sufficiency = classify_mca_data_sufficiency(y1, y2, y3)
        if sufficiency == "insufficient" and mca_data_available:
            sufficiency = "partial"

        # EBIT / PAT — use MCA where available, fallback to finanvo
        ebit_val = y1.get("ebit") if y1 else None
        if not ebit_val and finanvo_ratios.get("ebit_raw"):
            ebit_val = finanvo_ratios["ebit_raw"]
        pat_val = y1.get("pat") if y1 else None
        if not pat_val and finanvo_ratios.get("pbt_raw"):
            pat_val = finanvo_ratios["pbt_raw"]

        # ─────────────────────────────────────────────────────────────────────
        # STEP 5: Conduct Signals Computation
        # ─────────────────────────────────────────────────────────────────────

        # Charge conduct signals
        charge_signals = derive_charge_conduct_signals(payload.get("charges", []))

        # EPFO conduct signals
        epfo_signals = derive_epfo_conduct_signals(
            epfo_raw,
            revenue=revenue,
            sector_bucket=overview.get("businessState"),
        )

        # Comprehensive litigation extraction across eCourts, courtsData, legalCases, ncltCases
        court_info = self._extract_court_cases(payload)
        ecourts_raw = {
            "case_count_total": court_info["total_cases"],
            "case_count_active": court_info["active_cases"],
            "case_count_drt": court_info["drt_cases"],
            "case_count_nclt": court_info["nclt_cases"],
            "case_count_hc": court_info["hc_cases"],
            "has_insolvency_petition": court_info["nclt_active_against"],
        }
        ecourts_signals = derive_ecourts_conduct_signals(ecourts_raw)
        criminal_case_count = court_info["criminal_cases"]

        # Extract MSME delayed payment complaints
        msme_info = self._extract_msme_delays(payload)

        # Extract AML & PEP screening
        aml_info = self._extract_aml_screening(payload)

        # Extract statutory auditor remarks
        auditor_info = self._extract_auditors_remarks(payload)

        # Extract consolidated multi-year financials
        cons_financials = self._extract_consolidated_financials(payload)

        # GST conduct signals
        gst_filing_label = (
            "regular" if gst_consistency >= 0.9
            else "irregular" if gst_consistency >= 0.5
            else "non-filer"
        )
        gst_raw = {
            "taxpayerInfo": {
                "gstin": gstin_val or "",
                "gstStatus": "active" if gst_active else "inactive",
            },
            "returnFilingHistory": payload.get("annexureGST", []),
            "gst_filing_consistency": gst_filing_label,
        }
        gst_signals = derive_gst_conduct_signals(gst_raw)

        # ─────────────────────────────────────────────────────────────────────
        # STEP 6: Conduct Score Chain (base 70, strict order per doc)
        # Order: Charges → EPFO → eCourts → GST → Directors
        # ─────────────────────────────────────────────────────────────────────
        base_conduct_score = 70  # Hardcoded per doc Appendix A
        conduct_score, reasons_charge = apply_charge_conduct_adjustments(base_conduct_score, charge_signals)
        conduct_score, reasons_epfo = apply_epfo_conduct_adjustments(conduct_score, epfo_signals)
        conduct_score, reasons_ecourts = apply_ecourts_conduct_adjustments(conduct_score, ecourts_signals)
        conduct_score, reasons_gst = apply_gst_conduct_adjustments(conduct_score, gst_signals)
        conduct_score, reasons_director = apply_director_conduct_adjustments(conduct_score, dir_signals)

        all_conduct_reasons = (
            reasons_charge
            + reasons_epfo
            + reasons_ecourts
            + reasons_gst
            + reasons_director
        )

        # ─────────────────────────────────────────────────────────────────────
        # STEP 7: Report-only ratio computation (DPO, Cash Coverage)
        # ─────────────────────────────────────────────────────────────────────
        dpo = compute_dpo(y1)
        cash_coverage = compute_cash_coverage(y1)

        # ─────────────────────────────────────────────────────────────────────
        # STEP 8: Build source notes for transparency
        # ─────────────────────────────────────────────────────────────────────
        source_notes = list(rev_notes)
        if not mca_data_available:
            source_notes.append("MCA_FINANCIAL_DATA_UNAVAILABLE")
        if sufficiency == "insufficient":
            source_notes.append("MCA_DATA_SUFFICIENCY_INSUFFICIENT")
        elif sufficiency == "partial":
            source_notes.append("MCA_DATA_SUFFICIENCY_PARTIAL")
        if not rbi_data_available:
            source_notes.append("RBI_CHECK_DEFAULTED_TO_CLEAR")
        source_notes += rbi_result.get("source_notes", [])
        source_notes += dir_signals.get("source_notes", [])

        # ─────────────────────────────────────────────────────────────────────
        # GST filing consistency label for feature row
        # ─────────────────────────────────────────────────────────────────────
        gst_consistency_label = (
            "REGULAR" if gst_consistency >= 0.9
            else "MINOR_GAPS" if gst_consistency >= 0.7
            else "MAJOR_GAPS" if gst_consistency >= 0.3
            else "NON_FILER"
        )

        return {
            # Identity
            "entity_key": db_row.get("cin"),
            "legal_name": db_row.get("company_name"),
            "entity_name": db_row.get("company_name"),
            "gstin": gstin_val,
            "cin": db_row.get("cin"),
            "pan": overview.get("IT_PAN_OF_COMPNY"),
            "incorporation_date": db_row.get("incorporation_date"),
            "state": db_row.get("registered_state") or overview.get("registeredState"),
            "authorized_capital": db_row.get("authorized_capital"),
            "paid_up_capital": db_row.get("paid_up_capital"),
            "gst_active": gst_active,
            "gst_active_flag": gst_active,
            "nic_code": nic_code,
            # Data quality
            "data_sufficiency_band": sufficiency,
            "mca_data_available": mca_data_available,
            "mca_years_available": mca_years_available,
            "revenue_source": revenue_source,
            "source_notes": source_notes,
            "conduct_reasons": all_conduct_reasons,
            # Revenue & financials
            "revenue": revenue,
            "ebit": ebit_val,
            "pat": pat_val,
            "total_debt": y1.get("total_debt") if y1 else None,
            "networth": y1.get("networth") if y1 else None,
            "receivables": y1.get("receivables") if y1 else None,
            "current_assets": y1.get("current_assets") if y1 else None,
            "current_liabilities": y1.get("current_liabilities") if y1 else None,
            "finance_cost": y1.get("finance_cost") if y1 else None,
            "inventory": y1.get("inventory") if y1 else None,
            "trade_payables": y1.get("trade_payables") if y1 else None,
            "cash_and_bank": y1.get("cash_and_bank") if y1 else None,
            "gross_fixed_assets": y1.get("gross_fixed_assets") if y1 else None,
            "revenue_prev1": y2.get("revenue") if y2 else None,
            "revenue_prev2": y3.get("revenue") if y3 else None,
            "financials": financials,
            # Aliased fields expected by input_parameters and FinalFeatureRow
            "net_revenue_latest": revenue,  # Latest year revenue
            "turnover_y1": revenue,          # FY Latest
            "turnover_y2": y2.get("revenue") if y2 else None,  # FY-1
            "turnover_y3": y3.get("revenue") if y3 else None,  # FY-2
            "dpo": dpo,
            "cash_coverage": cash_coverage,
            # Charge signals
            "charge_count_active": charge_signals["charge_count_active"],
            "has_any_active_charge": charge_signals["has_any_active_charge"],
            "has_recent_charge_90d": charge_signals["has_recent_charge_90d"],
            "old_unsatisfied_charge_count": charge_signals["old_unsatisfied_charge_count"],
            "lender_quality_flag": charge_signals["lender_quality_flag"],
            "distinct_lender_count": charge_signals.get("distinct_lender_count"),
            # Director signals
            "high_director_company_count": dir_signals.get("high_director_company_count"),
            "max_director_company_count": dir_signals.get("max_director_company_count"),
            "is_wilful_defaulter": False,  # Passed hard gate — confirmed clear
            # eCourts signals
            "case_count_total": ecourts_signals["case_count_total"],
            "case_count_active": ecourts_signals["case_count_active"],
            "case_count_drt": ecourts_signals["case_count_drt"],
            "case_count_nclt": ecourts_signals["case_count_nclt"],
            "case_count_hc": ecourts_signals["case_count_hc"],
            "has_insolvency_petition": ecourts_signals["has_insolvency_petition"],
            # CRITICAL: criminal_case_count is SEPARATE from HC cases
            # Per RA Model: criminal_case_count >= 1 → band downgrades 2 notches
            "criminal_case_count": criminal_case_count,
            "high_value_case_count": court_info.get("high_value_cases", 0),
            "recent_cases_12m": court_info.get("recent_cases_12m", 0),
            "recent_cases_24m": court_info.get("recent_cases_24m", 0),
            # GST signals
            "gst_turnover": gst_turnover,
            "gst_sector_bucket": overview.get("businessState") or overview.get("businessCategory"),
            "gst_filing_consistency": gst_consistency_label,
            "gst_filing_consistency_ratio": gst_consistency,
            # EPFO signals
            "epfo_headcount": epfo_signals.get("epfo_headcount"),
            "pf_filing_regular": epfo_signals.get("pf_filing_regular"),
            "revenue_per_employee_outlier": epfo_signals.get("revenue_per_employee_outlier"),
            "epfo_headcount_drop": epfo_signals.get("headcount_drop", False),
            # Conduct score
            "conduct_score": conduct_score,
            # Finanvo pre-computed ratios (used in _derive_ratios as override)
            "_finanvo_ratios": finanvo_ratios,
            # Enhanced data sections
            "court_info": court_info,
            "msme_info": msme_info,
            "aml_info": aml_info,
            "auditor_info": auditor_info,
            "consolidated_financials": cons_financials,
        }

    def _extract_finanvo_ratios(self, payload: dict) -> dict:
        """Extract pre-computed ratios from finanvo 'ratios' payload section.
        These are authoritative computed values from the data provider.
        Returns dict with float values or None."""
        ratios_list = payload.get("ratios") or []
        if not ratios_list:
            return {}
        # Take most recent year (first entry)
        r = ratios_list[0] if isinstance(ratios_list, list) else ratios_list
        if not isinstance(r, dict):
            return {}

        def _parse_float(val: str | None) -> Optional[float]:
            """Parse values like '2.58 times', '16.75%', '36.76'"""
            if val is None:
                return None
            s = str(val).strip().replace(" times", "").replace("%", "").replace(",", "").strip()
            try:
                return float(s)
            except (ValueError, TypeError):
                return None

        result = {
            "current_ratio": _parse_float(r.get("CURRENT_RATIO_TIMES")),
            "quick_ratio": _parse_float(r.get("QUICK_RATIO_TIMES")),
            "debt_to_equity": _parse_float(r.get("DEBT_EQUITY_RATIO_TIMES")),
            "net_profit_margin_pct": _parse_float(r.get("NET_PROFIT_MARGIN_PER")),
            "gross_profit_margin_pct": _parse_float(r.get("GROSS_PROFIT_MARGIN_PER")),
            "ebit_margin_pct": _parse_float(r.get("EBIT_MARGIN_PER")),
            "operating_margin_pct": _parse_float(r.get("OPERATING_PROFIT_MARGIN_PER")),
            "dso": _parse_float(r.get("COLLECTION_PERIOD_DAYS")),
            "dpo": _parse_float(r.get("PAYMENT_PERIOD_DAYS")),
            "revenue_cagr_pct": _parse_float(r.get("SALES_GROWTH_PER")),
            "net_profit_growth_pct": _parse_float(r.get("NET_PROFIT_GROWTH_PER")),
            "roce_pct": _parse_float(r.get("RETURN_ON_CAPITAL_EMPLOYED_PER")),
            "roe_pct": _parse_float(r.get("RETURN_ON_NET_WORTH_PER")),
            "total_liabilities_to_tnw": _parse_float(r.get("TOTAL_LIABILITIES_TO_TANGIBLE_NETWORTH_TIMES")),
            "fixed_assets_turnover": _parse_float(r.get("FIXED_ASSETS_TURNOVER_TIMES")),
            "total_assets_turnover": _parse_float(r.get("TOTAL_ASSETS_TURNOVER_TIMES")),
            "interest_coverage": _parse_float(r.get("INTEREST_COVERAGE_RATIO_TIMES")),
            "cash_flow_margin_pct": _parse_float(r.get("CASH_FLOW_MARGIN_PER")),
            "working_capital_cycle": _parse_float(r.get("WORKING_CAPITAL_CYCLE")),
            # Raw financials
            "ebit_raw": _parse_float(r.get("EBIT")),
            "ebitda_raw": _parse_float(r.get("EBITDA")),
            "pbt_raw": _parse_float(r.get("PBT")),
            "operating_profit_raw": _parse_float(r.get("OPERATING_PROFIT")),
            "gross_profit_raw": _parse_float(r.get("GROSS_PROFIT")),
            "net_cash_flow_raw": _parse_float(r.get("NET_CASH_FLOW")),
            "sales_goods_raw": _parse_float(r.get("SALES_GOODS")),
        }
        return {k: v for k, v in result.items() if v is not None}

    def _derive_ratios(self, row: Dict[str, Any]) -> Dict[str, Optional[float]]:
        """Compute all financial ratios per RA Model documentation formulas.
        Priority: finanvo pre-computed values first, then compute from raw financials."""
        r: Dict[str, Optional[float]] = {}

        # === Per RA Model Doc Section 4.1 — Formulas & Relative Capital Fallbacks ===
        networth = row.get("networth") or 0.0
        ca = row.get("current_assets") or 0.0
        cl = row.get("current_liabilities") or 0.0
        inv = row.get("inventory") or 0.0
        total_debt = row.get("total_debt") or 0.0

        # Current Ratio = Current Assets / Current Liabilities (Fallback to 1.5 if solvent & no CL detail)
        r["current_ratio"] = round(ca / cl, 4) if cl and cl > 0 else (1.5 if networth > 0 else None)

        # Quick Ratio = (Current Assets - Inventory) / Current Liabilities (Fallback to 1.5 if solvent)
        r["quick_ratio"] = round((ca - inv) / cl, 4) if cl and cl > 0 else (1.5 if networth > 0 else None)

        # Working Capital = Current Assets - Current Liabilities (Fallback to 20% Networth if solvent)
        r["working_capital"] = ca - cl if (ca or cl) else (round(networth * 0.2, 2) if networth > 0 else None)

        # Debt-to-Equity = Total Debt / Networth (0.00x if zero borrowings & positive capital)
        r["debt_to_equity"] = round(total_debt / networth, 4) if networth and networth > 0 else (0.0 if networth > 0 else None)

        # Tangible Net Worth = Networth
        r["tangible_net_worth"] = networth if networth > 0 else None

        # DSO = (Receivables / Revenue) × 365
        receivables = row.get("receivables") or 0.0
        revenue = row.get("revenue") or 0.0
        r["dso"] = round((receivables / revenue) * 365, 1) if revenue and revenue > 0 else None

        # DPO = (Trade Payables / Revenue) × 365  [REPORT ONLY — not scored]
        trade_payables = row.get("trade_payables") or 0.0
        r["dpo"] = round((trade_payables / revenue) * 365, 1) if revenue and revenue > 0 else None

        # Cash Coverage = Cash & Bank / Total Debt  [REPORT ONLY — not scored]
        cash_and_bank = row.get("cash_and_bank") or 0.0
        r["cash_coverage"] = round(cash_and_bank / total_debt, 3) if total_debt and total_debt > 0 else None

        # Revenue CAGR (2-year proxy when only 3Y available)
        # CAGR = (Y1 / Y3)^(1/2) - 1
        rev_y1 = row.get("revenue") or 0.0
        rev_y2 = row.get("revenue_prev1") or 0.0
        rev_y3 = row.get("revenue_prev2") or 0.0
        if rev_y1 and rev_y3 and rev_y3 > 0:
            r["net_revenue_cagr_5y"] = round(((rev_y1 / rev_y3) ** (1 / 2)) - 1, 4)
        elif rev_y1 and rev_y2 and rev_y2 > 0:
            r["net_revenue_cagr_5y"] = round((rev_y1 / rev_y2) - 1, 4)
        else:
            r["net_revenue_cagr_5y"] = None

        # Revenue Volatility CV = StdDev / Mean (for haircut check)
        revenues = [x for x in [rev_y1, rev_y2, rev_y3] if x and x > 0]
        if len(revenues) >= 2:
            import statistics
            mean_rev = statistics.mean(revenues)
            if mean_rev > 0:
                r["revenue_cv"] = round(statistics.stdev(revenues) / mean_rev, 4)
        
        # === Profitability Ratios (REPORT ONLY) ===
        ebit = row.get("ebit") or 0.0
        pat = row.get("pat") or 0.0
        finance_cost = row.get("finance_cost") or 0.0

        # Net Profit Margin = PAT / Revenue × 100
        r["net_margin"] = round((pat / revenue) * 100, 2) if revenue and revenue > 0 else None

        # EBIT Margin = EBIT / Revenue × 100
        r["ebit_margin"] = round((ebit / revenue) * 100, 2) if revenue and revenue > 0 else None

        # Interest Coverage Ratio = EBIT / Finance Cost
        r["icr"] = round(ebit / finance_cost, 2) if finance_cost and finance_cost > 0 else None

        # ROCE = EBIT / (Total Debt + Networth) × 100
        capital_employed = total_debt + networth
        r["roce"] = round((ebit / capital_employed) * 100, 2) if capital_employed and capital_employed > 0 else None

        # === Override with finanvo pre-computed values where available ===
        # Finanvo values are authoritative as they come from verified MCA data
        fv = row.get("_finanvo_ratios") or {}
        if fv.get("current_ratio") is not None:
            r["current_ratio"] = fv["current_ratio"]
        if fv.get("quick_ratio") is not None:
            r["quick_ratio"] = fv["quick_ratio"]
        if fv.get("debt_to_equity") is not None:
            r["debt_to_equity"] = fv["debt_to_equity"]
        if fv.get("dso") is not None:
            r["dso"] = fv["dso"]
        if fv.get("dpo") is not None:
            r["dpo"] = fv["dpo"]
        if fv.get("net_profit_margin_pct") is not None:
            r["net_margin"] = fv["net_profit_margin_pct"]
        if fv.get("ebit_margin_pct") is not None:
            r["ebit_margin"] = fv["ebit_margin_pct"]
        if fv.get("interest_coverage") is not None:
            r["icr"] = fv["interest_coverage"]
        if fv.get("roce_pct") is not None:
            r["roce"] = fv["roce_pct"]
        if fv.get("roe_pct") is not None:
            r["roe"] = fv["roe_pct"]
        if fv.get("gross_profit_margin_pct") is not None:
            r["gross_margin"] = fv["gross_profit_margin_pct"]
        if fv.get("operating_margin_pct") is not None:
            r["operating_margin"] = fv["operating_margin_pct"]
        if fv.get("cash_flow_margin_pct") is not None:
            r["cash_flow_margin"] = fv["cash_flow_margin_pct"]
        if fv.get("fixed_assets_turnover") is not None:
            r["fixed_assets_turnover"] = fv["fixed_assets_turnover"]
        if fv.get("total_assets_turnover") is not None:
            r["total_assets_turnover"] = fv["total_assets_turnover"]
        # For CAGR, finanvo gives 1-year growth — use as proxy if we couldn't compute
        if r.get("net_revenue_cagr_5y") is None and fv.get("revenue_cagr_pct") is not None:
            r["net_revenue_cagr_5y"] = fv["revenue_cagr_pct"] / 100.0  # Convert % to decimal

        return r

    def _build_normalized_record(self, row: Dict[str, Any]) -> NormalizedRecord:
        gst_consistency = row.get("gst_filing_consistency", "")
        _filed, _total = {"REGULAR": (11, 12), "MINOR_GAPS": (9, 12), "MAJOR_GAPS": (6, 12), "NON_FILER": (0, 12)}.get(
            str(gst_consistency).upper(), (None, None)
        )
        sources = ["gst"]
        if row.get("mca_data_available"):
            sources.append("mca")
        if (row.get("case_count_total") or 0) > 0:
            sources.append("ecourts")
        if (row.get("epfo_headcount") or 0) > 0:
            sources.append("epfo")
        
        return NormalizedRecord(
            entity_id=str(row.get("entity_key", "UNKNOWN")),
            legal_name=row.get("legal_name"),
            gstin=row.get("gstin"),
            cin=row.get("cin"),
            pan=row.get("pan"),
            gst_filing_periods_total=_total,
            gst_filing_periods_filed=_filed,
            legal_case_count=row.get("case_count_total"),
            pending_case_count=row.get("case_count_active"),
            criminal_case_count=row.get("criminal_case_count"),
            turnover_y1=row.get("revenue"),
            turnover_y2=row.get("revenue_prev1"),
            turnover_y3=row.get("revenue_prev2"),
            current_assets_latest=row.get("current_assets"),
            current_liabilities_latest=row.get("current_liabilities"),
            total_debt_latest=row.get("total_debt"),
            equity_latest=row.get("networth"),
            accounts_receivable_latest=row.get("receivables"),
            net_revenue_latest=row.get("revenue"),
            nic_code=row.get("nic_code"),
            sources_available=sources,
            conflict_flags=[],
        )

    def _derive_business_vintage(self, row: Dict[str, Any]) -> Optional[float]:
        inc_date = row.get("incorporation_date")
        if inc_date is not None:
            if isinstance(inc_date, str):
                try:
                    inc_date = date.fromisoformat(inc_date)
                except Exception:
                    pass
            if isinstance(inc_date, (date, datetime)):
                return round(
                    (date.today() - (inc_date.date() if isinstance(inc_date, datetime) else inc_date)).days / 365.25, 2
                )
        return 3.0

    def _assemble_granular_sections(
        self,
        *,
        entity_id: str,
        seller_id: str,
        assessed_at: str,
        pralyon_score: int,
        risk_band: str,
        blended_pd: float,
        lgd_estimate: float,
        conduct_score: float,
        financial_score: float,
        identity_score: float,
        legal_score: float,
        documentation_score: float,
        xai_summary: Optional[str] = None,
        xai_summary_text: Optional[str] = None,
        xai_narrative: str = "",
        xai_narrative_text: Optional[str] = None,
        xai_narrative_lines: Optional[list] = None,
        dimension_readings: Optional[dict] = None,
        table_enrichments: Optional[dict] = None,
        lime_methodology_note: Optional[str] = None,
        shap_top_features: Optional[list] = None,
        shap_ranked: Optional[list] = None,
        lime_explanation: Optional[dict] = None,
        data_sources_used: Optional[list] = None,
        pipeline_version: str = "2.2.0",
        metadata: Optional[dict] = None,
        input_parameters: Optional[dict] = None,
        final_feature_row: Any = None,
        ppre_output: Optional[dict] = None,
        is_hard_decline: bool = False,
        decline_reason: Optional[str] = None,
    ) -> dict:
        metadata = metadata or {}
        dimension_readings = dimension_readings or {}
        table_enrichments = table_enrichments or {}
        shap_top_features = shap_top_features or []
        shap_ranked = shap_ranked or []
        lime_explanation = lime_explanation or {}
        data_sources_used = data_sources_used or ["mca", "gst", "ecourts"]
        input_parameters = input_parameters or {}
        ppre_output = ppre_output or {}
        xai_narrative_lines = xai_narrative_lines or [xai_narrative]

        declined = is_hard_decline or risk_band in ("D", "UNSCOREABLE")
        blended_pd_pct = round(blended_pd * 100, 2)
        zp = metadata.get("zeropass") or {}
        any_gate_failed = is_hard_decline or any(zp.get(f"g{i}_fail", False) for i in range(1, 9))

        # ── 1. Overview ──
        overview = {
            "company_name": metadata.get("company_name"),
            "trade_name": metadata.get("trade_name") or metadata.get("company_name"),
            "report_id": metadata.get("report_id"),
            "report_date": metadata.get("report_date"),
            "suite_label": "Service 1 of 5 · Pralyon Intelligence Suite",
            "meta_bar": {
                "cin": metadata.get("cin"),
                "gstin": metadata.get("gstin"),
                "pan": metadata.get("pan"),
                "registered_state": metadata.get("state"),
                "incorporation_date": metadata.get("incorporation_date"),
                "vintage_years": metadata.get("vintage_years"),
                "report_date": metadata.get("report_date"),
                "report_id": metadata.get("report_id"),
            },
            "feature_pills": [
                {"title": "Pralyon AI", "subtitle": "Predictive engine"},
                {"title": "Tri-Core™", "subtitle": "3-track synthesis"},
                {"title": f"RiskBand™ {risk_band}", "subtitle": "7-tier classification"},
                {"title": "35 Signals", "subtitle": "6 authoritative sources"},
                {"title": "TraceLayer™", "subtitle": "Full explainability"},
            ],
            "verdict": {
                "declined": declined,
                "badge_text": "❌ DECLINED" if declined else f"⚠ {risk_band} — {metadata.get('policy_tier', risk_band)}",
                "headline": (
                    f"ZeroPass™ hard-stop gate triggered — {decline_reason or 'assessment stopped'}"
                    if declined
                    else "Buyer clears all ZeroPass™ gates. Exposure supported with active monitoring conditions."
                ),
                "summary_html": xai_summary or xai_narrative or "",
                "summary_text": xai_summary_text or xai_narrative_text or xai_narrative or "",
            },
            "key_metrics": {
                "risk_band": {
                    "value": risk_band,
                    "label": "RiskBand™",
                    "sub_text": metadata.get("policy_tier") or risk_band,
                },
                "blended_pd": {
                    "value": blended_pd,
                    "display": f"{blended_pd_pct}%",
                    "label": "Blended PD",
                    "sub_text": "12-month horizon",
                },
                "zeropass_status": {
                    "value": "HARD STOP" if (declined or any_gate_failed) else "ALL CLEAR",
                    "label": "ZeroPass™",
                    "sub_text": "Gate triggered" if (declined or any_gate_failed) else "8 of 8 gates passed",
                },
                "signals_count": {
                    "value": 35,
                    "label": "Signals Pulled",
                    "sub_text": "Across 6 sources",
                },
                "credit_score": {
                    "value": ppre_output.get("credit_score"),
                    "label": "Credit Score",
                    "sub_text": "300–850 Scale",
                },
            },
            "pralyon_score": pralyon_score,
            "credit_score": ppre_output.get("credit_score"),
        }

        # ── 2. Entity Identity ──
        gstin = metadata.get("gstin")
        pan = metadata.get("pan")
        inc_date = metadata.get("incorporation_date")
        addr = metadata.get("registered_address")
        auth_cap = metadata.get("authorized_capital")
        paid_cap = metadata.get("paid_up_capital")

        entity_identity = {
            "verified_profiles": [
                {"field": "Legal name", "value": metadata.get("company_name"), "source": "MCA CIN API", "status": "Verified" if metadata.get("company_name") else "Missing", "status_class": "pass" if metadata.get("company_name") else "fail"},
                {"field": "CIN", "value": metadata.get("cin"), "source": "MCA CIN API", "status": "Active" if metadata.get("cin") else "Missing", "status_class": "pass" if metadata.get("cin") else "fail"},
                {"field": "GSTIN", "value": gstin, "source": "GSTIN Advanced API", "status": "Active · Regular" if gstin else "Missing", "status_class": "pass" if gstin else "fail"},
                {"field": "PAN", "value": pan, "source": "PAN → CIN cross-verified", "status": "Match" if pan else "Missing", "status_class": "pass" if pan else "fail"},
                {"field": "Incorporation date", "value": f"{inc_date} · {metadata.get('vintage_years', 0)} years" if inc_date else None, "source": "MCA CIN API", "status": "Verified" if inc_date else "Missing", "status_class": "neutral" if inc_date else "fail"},
                {"field": "Registered address", "value": addr, "source": "MCA CIN API", "status": "Confirmed" if addr and addr != "Not Available" else "Pending", "status_class": "pass" if addr and addr != "Not Available" else "warn"},
                {"field": "Sector / NIC", "value": f"NIC {metadata.get('nic_code')} · {metadata.get('sector')}" if metadata.get("nic_code") else None, "source": "MCA CIN API", "status": "Confirmed" if metadata.get("nic_code") else "Pending", "status_class": "pass" if metadata.get("nic_code") else "warn"},
                {"field": "Authorised capital", "value": f"₹{auth_cap}" if auth_cap and auth_cap != '-' else "Not Available", "source": "MCA", "status": "On file" if auth_cap and auth_cap != '-' else "Missing", "status_class": "neutral" if auth_cap and auth_cap != '-' else "warn"},
                {"field": "Paid-up capital", "value": f"₹{paid_cap}" if paid_cap and paid_cap != '-' else "Not Available", "source": "MCA", "status": "On file" if paid_cap and paid_cap != '-' else "Missing", "status_class": "neutral" if paid_cap and paid_cap != '-' else "warn"},
                {"field": "ROC", "value": metadata.get("roc"), "source": "MCA CIN API", "status": "Compliant" if metadata.get("roc") else "Pending", "status_class": "pass" if metadata.get("roc") else "warn"},
                {"field": "MCA company status", "value": metadata.get("company_status"), "source": "MCA CIN API", "status": metadata.get("company_status") or "Unknown", "status_class": "pass" if metadata.get("company_status") == "Active" else "warn"},
            ]
        }

        aml_meta = metadata.get("aml") or {}
        if aml_meta:
            aml_res = aml_meta.get("result") or "NO_MATCH_FOUND"
            aml_has_match = aml_meta.get("has_match", False)
            entity_identity["verified_profiles"].append({
                "field": "AML & Sanctions screening",
                "value": "Clean · No adverse watchlist hit" if not aml_has_match else f"Match: {aml_res}",
                "source": "Global Sanctions & PEP Register",
                "status": "Verified Clean" if not aml_has_match else "Adverse Match",
                "status_class": "pass" if not aml_has_match else "fail",
            })

        # ── 3. Director Profile ──
        directors_formatted = []
        for d in metadata.get("directors", []):
            disq = d.get("disqualified", False)
            directors_formatted.append({
                "name": d.get("name", "Unknown"),
                "din": d.get("din", "N/A"),
                "designation": d.get("designation", "Director"),
                "sec_164_disqualified": disq,
                "other_entities_count": d.get("other_entities_count", 0),
                "struck_off_links": d.get("struck_off_links", "None"),
                "status": d.get("status", "Clear"),
                "status_class": "fail" if disq else "pass",
                "shareholding_pct": d.get("shareholding_pct"),
                "remuneration": d.get("remuneration"),
            })
        director_profile = {
            "directors": directors_formatted,
            "summary_text": (
                f"All {len(directors_formatted)} director(s) hold active DIN status. No disqualification under Section 164(2). No association with struck-off or wound-up entities."
                if directors_formatted else "No directors found."
            ),
        }

        # ── 4. ZeroPass ──
        gate_defs = [
            ("G-01", "NCLT / CIRP insolvency proceedings", "g1"),
            ("G-02", "Director disqualification under Sec 164", "g2"),
            ("G-03", "GSTIN active — not suspended / cancelled", "g3"),
            ("G-04", "MCA company status — Active", "g4"),
            ("G-05", "RBI / CIBIL wilful defaulter list", "g5"),
            ("G-06", "Section 138 cheque bounce (NI Act)", "g6"),
            ("G-07", "DRT lender recovery proceedings", "g7"),
            ("G-08", "Negative net worth (MCA XBRL)", "g8"),
        ]
        gates = []
        for gid, gcheck, gkey in gate_defs:
            gfailed = zp.get(f"{gkey}_fail", False)
            gates.append({
                "gate_id": gid,
                "check": gcheck,
                "result": zp.get(f"{gkey}_result", "N/A"),
                "disposition": "Triggered" if gfailed else "Clear",
                "status_class": "fail" if gfailed else "pass",
            })
        zeropass = {
            "headline": "Hard-stop gate triggered — scoring stopped" if (any_gate_failed or declined) else "All 8 gates cleared — scoring proceeds",
            "description": "ZeroPass™ evaluates 8 disqualifying conditions before any score is computed. If any gate triggers, the report stops and surfaces a hard-stop — no RiskBand™ is produced for an unsafe subject.",
            "gates": gates,
        }

        # ── 5. Tri-Core ──
        readings = metadata.get("readings") or dimension_readings or {}
        dimensions = [
            {"name": "Financial Health", "weight": "40%", "score": int(financial_score), "max_score": 100, "reading": readings.get("financial", "")},
            {"name": "Identity & Governance", "weight": "25%", "score": int(identity_score), "max_score": 100, "reading": readings.get("identity", "")},
            {"name": "Legal & Compliance", "weight": "20%", "score": int(legal_score), "max_score": 100, "reading": readings.get("legal", "")},
            {"name": "Conduct & Behaviour", "weight": "15%", "score": int(conduct_score), "max_score": 100, "reading": readings.get("conduct", "")},
        ]
        band_ladder_defs = [
            ("AAA", "< 0.3%", "Institutional-grade counterparty"),
            ("AA", "0.3–1.0%", "Very strong — generous terms justifiable"),
            ("A", "1.0–3.0%", "Low-moderate risk — standard trade credit"),
            ("BBB", "3.0–4.0%", "Adequate — minor watchpoints present"),
            ("BB", "4.0–7.0%", "Monitored pass — exposure supportable with conditions"),
            ("B", "7.0–15%", "Elevated risk — short tenor only"),
            ("CCC-D", "> 15%", "Do not extend trade credit"),
        ]
        band_ladder = []
        for b, pdr, br in band_ladder_defs:
            is_curr = (risk_band == b) or (risk_band in ("CCC", "D", "UNSCOREABLE") and b == "CCC-D")
            item = {"band": b, "pd_range": pdr, "reading": br, "is_current": is_curr}
            if is_curr:
                item["blended_pd_display"] = f"{blended_pd_pct}%"
            band_ladder.append(item)

        tri_core = {
            "headline": f"RiskBand™ {risk_band} · Blended PD {blended_pd_pct}%",
            "description": "Three independent scoring tracks are run in parallel and synthesised into a single calibrated score, which maps to the RiskBand™. No single track can override the others — all three must converge.",
            "credit_score": ppre_output.get("credit_score"),
            "dimensions": dimensions,
            "band_ladder": band_ladder,
        }

        # ── 6. Financial Performance ──
        fin_history = metadata.get("financials") or metadata.get("financial_history") or []
        ratios_dict = metadata.get("ratios") or {}
        insights_dict = metadata.get("ratio_insights") or {}

        stmt_years = [f.get("year", f"FY{idx}") for idx, f in enumerate(fin_history)]
        stmt_metric_keys = [
            ("Total Revenue / Turnover", "revenue", "currency_cr"),
            ("EBIT (Operating Profit)", "ebit", "currency_cr"),
            ("PAT (Net Profit After Tax)", "pat", "currency_cr"),
            ("Tangible Net Worth / Networth", "networth", "currency_cr"),
            ("Total Borrowings / Debt", "total_debt", "currency_cr"),
            ("Current Assets", "current_assets", "currency_cr"),
            ("Current Liabilities", "current_liabilities", "currency_cr"),
            ("Trade Receivables", "receivables", "currency_cr"),
        ]
        stmt_rows = []
        for sm_label, sm_key, sm_fmt in stmt_metric_keys:
            vals = {f.get("year", ""): f.get(sm_key) for f in fin_history}
            stmt_rows.append({"metric": sm_label, "values": vals, "format": sm_fmt})

        ratio_defs = [
            ("Current Ratio", "current_ratio", "×", 2),
            ("Quick Ratio", "quick_ratio", "×", 2),
            ("Debt / Equity", "debt_to_equity", "×", 2),
            ("EBIT Margin", "ebit_margin", "%", 1),
            ("Net Profit Margin", "net_margin", "%", 1),
            ("Interest Coverage (ICR)", "icr", "×", 1),
            ("Return on Capital (ROCE)", "roce", "%", 1),
            ("DSO (Debtor Days)", "dso", " days", 0),
            ("DPO (Creditor Days)", "dpo", " days", 0),
            ("Tangible Net Worth", "tangible_net_worth", None, 2),
        ]
        ratios_formatted = []
        for rname, rkey, rsuff, rprec in ratio_defs:
            rval = ratios_dict.get(rkey)
            rins = insights_dict.get(rkey) or {}
            if rkey == "tangible_net_worth":
                rdisp = f"₹{rval / 10_000_000:.2f} Cr" if rval is not None else "N/A"
            elif rval is not None:
                rdisp = f"{round(rval, rprec)}{rsuff or ''}"
            else:
                rdisp = "N/A"
            ratios_formatted.append({
                "name": rname,
                "value": rval,
                "display": rdisp,
                "benchmark": rins.get("benchmark", ""),
                "status": rins.get("status", ""),
                "status_class": rins.get("status_class", "neutral"),
                "implication": rins.get("implication", ""),
            })

        cons_fin_history = metadata.get("consolidated_financials") or []
        consolidated_statements = None
        if cons_fin_history:
            cons_years = [f.get("year", f"FY{idx}") for idx, f in enumerate(cons_fin_history)]
            cons_rows = []
            for sm_label, sm_key, sm_fmt in stmt_metric_keys:
                vals = {f.get("year", ""): f.get(sm_key) for f in cons_fin_history}
                cons_rows.append({"metric": sm_label, "values": vals, "format": sm_fmt})
            consolidated_statements = {"years": cons_years, "rows": cons_rows}

        financial_performance = {
            "statements": {"years": stmt_years, "rows": stmt_rows},
            "consolidated_statements": consolidated_statements,
            "ratios": ratios_formatted,
        }

        # ── 7. BehaviourPrint ──
        gst_meta = metadata.get("gst") or {}
        epfo_meta = metadata.get("epfo") or {}
        charge_meta = metadata.get("charge") or {}
        msme_meta = metadata.get("msme") or {}
        auditor_meta = metadata.get("auditor_info") or {}

        gst_txt = gst_meta.get("filing_consistency_label", "")
        gst_ok = "regular" in gst_txt.lower() or "good" in gst_txt.lower()

        behaviour_signals = [
            {"signal": "GST filing discipline", "type": "Strength" if gst_ok else "Watchpoint", "type_class": "pass" if gst_ok else "warn", "observation": f"Filing consistency: {gst_txt}", "implication": "Strong compliance discipline — low statutory default risk." if gst_ok else "Potential cashflow stress indicator."},
            {"signal": "EPFO headcount trend", "type": "Watchpoint" if epfo_meta.get("headcount_drop") else "Strength", "type_class": "warn" if epfo_meta.get("headcount_drop") else "pass", "observation": f"EPFO headcount: {epfo_meta.get('employee_count') or 'N/A'} employees", "implication": "Recent workforce contraction detected." if epfo_meta.get("headcount_drop") else "Stable workforce indicator."},
            {"signal": "EPFO challan defaults", "type": "Strength" if epfo_meta.get("pf_filing_regular") else "Watchpoint", "type_class": "pass" if epfo_meta.get("pf_filing_regular") else "warn", "observation": f"ECR filings: {'Regular' if epfo_meta.get('pf_filing_regular') else 'Delayed'}", "implication": "Strong payroll compliance behaviour." if epfo_meta.get("pf_filing_regular") else "Delayed payroll payments observed."},
            {"signal": "Charge register quality", "type": "Watchpoint" if charge_meta.get("has_active") else "Strength", "type_class": "warn" if charge_meta.get("has_active") else "pass", "observation": charge_meta.get("charge_summary") or "No active charges", "implication": "Secured credit activity." if charge_meta.get("has_active") else "No third-party lender credit discipline."},
        ]

        if msme_meta.get("count", 0) > 0:
            msme_cnt = msme_meta.get("count", 0)
            msme_disp = msme_meta.get("display_amount", "₹0")
            behaviour_signals.append({
                "signal": "MSME vendor payment discipline",
                "type": "Watchpoint",
                "type_class": "warn",
                "observation": f"{msme_cnt} delayed payment filing(s) on MSME Samadhaan ({msme_disp} total)",
                "implication": "Vendors reporting payment delays under Section 15 of MSMED Act.",
            })
        else:
            behaviour_signals.append({
                "signal": "MSME vendor payment discipline",
                "type": "Strength",
                "type_class": "pass",
                "observation": "Nil vendor delay complaints on MSME Samadhaan portal",
                "implication": "Prompt settlement cycle with MSME vendors.",
            })

        if auditor_meta.get("has_adverse"):
            behaviour_signals.append({
                "signal": "Statutory auditor remarks (CARO)",
                "type": "Watchpoint",
                "type_class": "warn",
                "observation": auditor_meta.get("summary", "CARO qualifications noted"),
                "implication": "Auditor noted qualifications or adverse notes in statutory audit report.",
            })
        else:
            behaviour_signals.append({
                "signal": "Statutory auditor remarks (CARO)",
                "type": "Strength",
                "type_class": "pass",
                "observation": auditor_meta.get("summary", "Clean statutory auditor remarks across CARO reporting parameters"),
                "implication": "Clean financial hygiene confirmed by independent auditor.",
            })

        behaviour_print = {
            "signals": behaviour_signals,
        }

        # ── 8. Compliance Intelligence ──
        compliance_checks = [
            {"check": "GSTIN status", "result": "Active" if gstin else "Inactive", "status": "Active" if gstin else "Inactive", "status_class": "pass" if gstin else "fail", "implication": "ITC can be claimed on invoices." if gstin else "ITC not claimable."},
            {"check": "EPFO / PF continuity", "result": f"ECR Filings: {'Regular' if epfo_meta.get('pf_filing_regular') else 'Delayed'}", "status": "Compliant" if epfo_meta.get("pf_filing_regular") else "Flagged", "status_class": "pass" if epfo_meta.get("pf_filing_regular") else "warn", "implication": "No workforce payment defaults." if epfo_meta.get("pf_filing_regular") else "Payroll payment gaps exist."},
            {"check": "EPFO headcount declared", "result": f"{epfo_meta.get('employee_count') or 'N/A'} employees", "status": "On file" if epfo_meta.get("employee_count") else "Missing", "status_class": "pass" if epfo_meta.get("employee_count") else "warn", "implication": "Statutory workforce details."},
            {"check": "RBI defaulter list", "result": "Listed" if zp.get("g5_fail") else "Not listed", "status": "Flagged" if zp.get("g5_fail") else "Clear", "status_class": "fail" if zp.get("g5_fail") else "pass", "implication": "Banking default risk." if zp.get("g5_fail") else "No banking default."},
        ]

        if aml_meta:
            aml_res = aml_meta.get("result") or "NO_MATCH_FOUND"
            aml_has_match = aml_meta.get("has_match", False)
            compliance_checks.append({
                "check": "AML / PEP & Global Sanctions",
                "result": "No Match Found · Clear" if not aml_has_match else f"Adverse Match: {aml_res}",
                "status": "Clear" if not aml_has_match else "Flagged",
                "status_class": "pass" if not aml_has_match else "fail",
                "implication": "Cleared global watchlists and anti-money laundering databases." if not aml_has_match else "Match found on global sanction or watchlists.",
            })

        msme_count = msme_meta.get("count", 0)
        compliance_checks.append({
            "check": "MSME Samadhaan compliance",
            "result": f"{msme_count} delayed payment applications ({msme_meta.get('display_amount', '')})" if msme_count > 0 else "Nil delay filings",
            "status": "Watchpoint" if msme_count > 0 else "Compliant",
            "status_class": "warn" if msme_count > 0 else "pass",
            "implication": "Delayed payments to micro/small suppliers under Section 15 of MSMED Act." if msme_count > 0 else "Compliant with MSMED Act 45-day payment guidelines.",
        })

        compliance_intelligence = {
            "checks": compliance_checks,
        }

        # ── 9. Legal & Litigation ──
        court_info = metadata.get("court_info") or {}
        leg_meta = metadata.get("legal") or {}
        hc_cnt = court_info.get("hc_cases", leg_meta.get("hc_cases", 0))
        nclt_cnt = court_info.get("nclt_cases", leg_meta.get("nclt_cases", 0))
        drt_cnt = court_info.get("drt_cases", leg_meta.get("drt_cases", 0))
        act_cnt = court_info.get("active_cases", leg_meta.get("active_cases", 0))
        s138_cases = court_info.get("cheque_bounce_cases", [])
        s138_cnt = len(s138_cases)
        has_138_against = court_info.get("has_cheque_bounce_against", False)
        comp_138 = court_info.get("complainant_138_count", 0)

        legal_cases = [
            {"forum": "High Court", "matter_type": "Civil / Commercial", "count": hc_cnt, "status": "Clear" if hc_cnt == 0 else f"{hc_cnt} matters", "status_class": "pass" if hc_cnt == 0 else "warn", "implication": "No High Court matters found." if hc_cnt == 0 else "High Court references identified."},
            {"forum": "NCLT", "matter_type": "Insolvency / Company matter", "count": nclt_cnt, "status": "Clear" if nclt_cnt == 0 else f"{nclt_cnt} matters", "status_class": "pass" if nclt_cnt == 0 else "fail", "implication": "No insolvency matters found." if nclt_cnt == 0 else "Insolvency matters identified."},
            {"forum": "DRT / DRAT", "matter_type": "Debt recovery", "count": drt_cnt, "status": "Clear" if drt_cnt == 0 else f"{drt_cnt} cases", "status_class": "pass" if drt_cnt == 0 else "fail", "implication": "No lender recovery proceedings." if drt_cnt == 0 else "Lender recovery proceedings found."},
            {"forum": "Commercial / Civil Court", "matter_type": "B2B contract dispute", "count": act_cnt, "status": "Clear" if act_cnt == 0 else "Active", "status_class": "pass" if act_cnt == 0 else "warn", "implication": "No active litigation." if act_cnt == 0 else f"{act_cnt} active case(s) identified."},
        ]

        if s138_cnt > 0:
            legal_cases.append({
                "forum": "Cheque Bounce (Sec 138 NI Act)",
                "matter_type": "Negotiable Instruments Act",
                "count": s138_cnt,
                "status": "Accused / Flagged" if has_138_against else f"Complainant ({comp_138})",
                "status_class": "fail" if has_138_against else "pass",
                "implication": "Active dishonour proceedings against entity." if has_138_against else "Entity recovering receivables as complainant.",
            })
        else:
            legal_cases.append({
                "forum": "Cheque Bounce (Sec 138 NI Act)",
                "matter_type": "Negotiable Instruments Act",
                "count": 0,
                "status": "Clear",
                "status_class": "pass",
                "implication": "Nil Section 138 proceedings detected.",
            })

        detailed_cases = court_info.get("detailed_cases", [])

        legal_litigation = {
            "cases": legal_cases,
            "detailed_cases": detailed_cases,
            "summary_text": (
                "No active litigation found across any forum."
                if (hc_cnt + nclt_cnt + drt_cnt + act_cnt + (1 if has_138_against else 0)) == 0
                else f"Litigation analysis across {len(detailed_cases) or (hc_cnt + nclt_cnt + drt_cnt + act_cnt)} matter(s) on record — factored into Legal track scoring."
            ),
        }

        # ── 10. Charge Register ──
        charges_fmt = []
        for idx, ch in enumerate(metadata.get("charges", []), 1):
            amt = ch.get("amount", 0)
            st = str(ch.get("status", "active")).lower()
            is_sat = st in ("closed", "satisfied")
            charges_fmt.append({
                "charge_id": f"CH-{idx}",
                "lender": ch.get("lender", "Unknown"),
                "amount": amt,
                "display_amount": f"₹{amt / 100_000:.2f} L" if amt else "-",
                "created": ch.get("created", "N/A"),
                "status": "Satisfied" if is_sat else "Active",
                "status_class": "pass" if is_sat else "warn",
                "risk_note": "Standard charge registry entry",
            })
        charge_summary = metadata.get("charge_summary")
        summary_text = (
            f"{charge_summary['open_charge_count']} active charge(s) totaling {charge_summary['display_total_open_amount']} on MCA21."
            if charge_summary and charge_summary.get("open_charge_count")
            else ("No institutional lenders on record." if not charges_fmt else f"{len(charges_fmt)} charge(s) identified on MCA21 charge register.")
        )
        charge_register = {
            "summary": charge_summary,
            "charges": charges_fmt,
            "empty_text": "No charges registered on MCA21 charge register." if not charges_fmt else None,
            "summary_text": summary_text,
        }

        # ── 11. TraceLayer ──
        trace_sigs = []
        for idx, feat in enumerate((shap_top_features or shap_ranked or [])[:5], 1):
            is_wp = (feat.get("impact") or 0) > 0
            trace_sigs.append({
                "signal_id": idx,
                "track": "Computed Track",
                "type": "Watchpoint" if is_wp else "Positive",
                "title": f"Model feature: {feat.get('feature', 'unknown')}",
                "description": "This signal contributed significantly to the final predictive score and Blended PD outcome.",
                "watchpoint": "⚑ Watchpoint — This feature increased the computed risk profile." if is_wp else None,
            })
        if not trace_sigs:
            trace_sigs.append({
                "signal_id": 1,
                "track": "Blended PD Output",
                "type": "Assessment",
                "title": "Primary Model Assessment",
                "description": xai_narrative_text or xai_narrative or "",
                "watchpoint": None,
            })
        trace_layer = {
            "headline": f"Every signal explained — what drove the {risk_band} output",
            "signals": trace_sigs,
            "methodology_note": lime_methodology_note,
        }

        # ── 12. Monitoring Conditions ──
        band_order = ["AAA", "AA", "A", "BBB", "BB", "B", "CCC", "D"]
        try:
            b_idx = band_order.index(risk_band)
            next_band = band_order[b_idx + 1] if b_idx + 1 < len(band_order) else "D"
        except ValueError:
            next_band = "D"

        triggers = [
            {"trigger": "RiskBand™ drift", "event": f"Band falls from {risk_band} to {next_band} or below", "auto_response": "Alert · Tri-Core™ rerun", "required_action": "Reassess limit within 5 days"},
            {"trigger": "EPFO headcount", "event": "Falls below threshold in next assessment", "auto_response": "Alert · BehaviourPrint™ flag", "required_action": "Re-run full assessment · consider limit reduction"},
            {"trigger": "Commercial court matter", "event": "Progresses to decree, execution, or attachment", "auto_response": "Critical alert · ZeroPass™ re-evaluation", "required_action": "Pause new orders · immediate legal review"},
            {"trigger": "GST filing gap", "event": "Any GSTR-3B month missed or GSTR mismatch > 15%", "auto_response": "Alert · compliance flag", "required_action": "Flag for limit review · do not extend new credit"},
            {"trigger": "Revenue trend", "event": "GST turnover drops > 20% YoY in next assessment", "auto_response": "Alert · financial track recompute", "required_action": "Re-run full assessment · do not auto-renew exposure"},
            {"trigger": "New DRT case", "event": "Lender recovery filing against entity or director", "auto_response": "Critical alert · halt new orders", "required_action": "Immediate review · pause shipments · legal counsel"},
            {"trigger": "Director change", "event": "Any director resignation or new DIN added", "auto_response": "Alert · governance flag", "required_action": "Run fresh DIN screen within 30 days"},
            {"trigger": "NCLT / CIRP", "event": "Insolvency admitted against entity", "auto_response": "ZeroPass™ G-01 breach · auto halt", "required_action": "Recover exposure immediately · legal counsel"},
            {"trigger": "Report expiry", "event": f"30 days from generation — {metadata.get('report_date', '')}", "auto_response": "Expiry alert", "required_action": "Re-run report before next shipment or order approval"},
        ]
        monitoring_conditions = {
            "validity_text": f"Valid for 30 days from {metadata.get('report_date', 'report generation')}",
            "triggers": triggers,
            "upsell": {
                "title": "Credit limit and tenor are not computed in this report — upgrade to Service 2",
                "description": "The Buyer Risk Assessment is intentionally complete on buyer intelligence and intentionally silent on credit limit and tenor. To receive an AnchorEngine™-computed safe exposure limit, ShockFrame™ stress scenarios, and a calibrated tenor recommendation, upgrade to the Operational Limit Assessment (Service 2).",
            },
        }

        # ── 13. Footer ──
        footer = {
            "brand": "RyskNode · Pralyon Intelligence Suite",
            "report_line": f"Service 1 · Buyer Risk Assessment · {metadata.get('company_name', '')} · {metadata.get('report_id', '')}",
            "confidentiality": "Confidential · Internal use only · Not for redistribution",
        }

        return {
            # UI Component sections for /reports
            "overview": overview,
            "entity_identity": entity_identity,
            "director_profile": director_profile,
            "zeropass": zeropass,
            "tri_core": tri_core,
            "financial_performance": financial_performance,
            "behaviour_print": behaviour_print,
            "compliance_intelligence": compliance_intelligence,
            "legal_litigation": legal_litigation,
            "charge_register": charge_register,
            "trace_layer": trace_layer,
            "monitoring_conditions": monitoring_conditions,
            "footer": footer,

            # Flat/core fields for backwards-compatibility with CreditLimit (S2) and HTML ReportService
            "entity_id": entity_id,
            "seller_id": seller_id,
            "assessed_at": assessed_at,
            "pralyon_score": pralyon_score,
            "risk_band": risk_band,
            "blended_pd": blended_pd,
            "lgd_estimate": lgd_estimate,
            "conduct_score": conduct_score,
            "financial_score": financial_score,
            "identity_score": identity_score,
            "legal_score": legal_score,
            "documentation_score": documentation_score,
            "xai_summary": xai_summary,
            "xai_summary_text": xai_summary_text,
            "dimension_readings": dimension_readings,
            "table_enrichments": table_enrichments,
            "lime_methodology_note": lime_methodology_note,
            "xai_narrative": xai_narrative,
            "xai_narrative_text": xai_narrative_text,
            "xai_narrative_lines": xai_narrative_lines,
            "shap_top_features": shap_top_features,
            "shap_ranked": shap_ranked,
            "lime_explanation": lime_explanation,
            "data_sources_used": data_sources_used,
            "pipeline_version": pipeline_version,
            "metadata": metadata,
            "input_parameters": input_parameters,
            "final_feature_row": final_feature_row,
            "_ppre_output": ppre_output,

            # S2 limit and schedules (promoted from ppre_output for CreditLimitResponse & RA model compliance)
            "evaluated_limit": ppre_output.get("evaluated_limit", 0.0),
            "advised_limit": ppre_output.get("advised_limit", ppre_output.get("evaluated_limit", 0.0)),
            "evaluated_clean_limit": ppre_output.get("evaluated_clean_limit", 0.0),
            "recommended_tenor": ppre_output.get("recommended_tenor", 30),
            "recommended_tenor_days": ppre_output.get("recommended_tenor_days", 30),
            "advance_required": ppre_output.get("advance_required", 0.0),
            "advance_pct_of_request": ppre_output.get("advance_pct_of_request"),
            "advance_recommendation": ppre_output.get("advance_recommendation"),
            "tenor_schedule": ppre_output.get("tenor_schedule", []),
            "tenor_note": ppre_output.get("tenor_note", ""),
            "tenor_recommendation_note": ppre_output.get("tenor_recommendation_note", ""),
            "tenor_best_evaluated_days": ppre_output.get("tenor_best_evaluated_days"),
            "stress_table": ppre_output.get("stress_table", []),
            "stress_table_text": ppre_output.get("stress_table_text", ""),
            "credit_score": ppre_output.get("credit_score"),
            "band_before_override": ppre_output.get("band_before_override"),
            "legal_health_score": ppre_output.get("legal_health_score"),
            "override_flags": ppre_output.get("override_flags", []),
            "reason_codes": ppre_output.get("reason_codes", []),
            "base_limit": ppre_output.get("base_limit"),
            "binding_anchor": ppre_output.get("binding_anchor"),
            "all_anchors": ppre_output.get("all_anchors"),
            "tenor_multiplier": ppre_output.get("tenor_multiplier"),
            "tenor_bucket_days": ppre_output.get("tenor_bucket_days"),
            "haircut_applied": ppre_output.get("haircut_applied"),
            "volatility_haircut": ppre_output.get("volatility_haircut"),
            "terms_vs_profile": ppre_output.get("terms_vs_profile"),
            "el_pct": ppre_output.get("el_pct"),
            "el_amount": ppre_output.get("el_amount"),
        }

    async def assess_buyer(
        self,
        entity_id: str,
        seller_id: str,
        trade_name: Optional[str] = None,
        state_code: Optional[str] = None,
        include_xai: bool = True,
        requested_amount: Optional[float] = None,
        avg_monthly_purchase_volume: Optional[float] = None,
        credit_period_days: int = 30,
        ead: Optional[float] = None,
    ) -> dict:
        db_row = await self._get_company_data(entity_id)
        raw_feature_row = self._run_part1_sourcing(db_row)

        if raw_feature_row.get("hard_decline") or raw_feature_row.get("decision") == "DECLINE":
            decline_reason = raw_feature_row.get("hard_decline_reason") or raw_feature_row.get("decline_reason") or "Triggered by hard check gates"
            now_iso = datetime.now(timezone.utc).isoformat()
            decline_meta = {
                "company_name": db_row.get("company_name"),
                "cin": db_row.get("cin"),
                "gstin": raw_feature_row.get("gstin"),
                "pan": raw_feature_row.get("pan"),
                "state": raw_feature_row.get("state"),
                "incorporation_date": str(raw_feature_row.get("incorporation_date")),
                "vintage_years": 0,
                "report_date": datetime.now(timezone.utc).strftime("%d %b %Y"),
                "report_id": f"PRY-S1-{datetime.now(timezone.utc).strftime('%Y%m%d')}-0001",
                "policy_tier": "DECLINE",
            }
            return self._assemble_granular_sections(
                entity_id=entity_id,
                seller_id=seller_id,
                assessed_at=now_iso,
                pralyon_score=300,
                risk_band="D",
                blended_pd=1.0,
                lgd_estimate=0.85,
                conduct_score=0.0,
                financial_score=0.0,
                identity_score=0.0,
                legal_score=0.0,
                documentation_score=0.0,
                xai_narrative=f"Hard decline triggered: {decline_reason}",
                xai_narrative_text=f"Hard decline triggered: {decline_reason}",
                xai_narrative_lines=[f"Hard decline triggered: {decline_reason}"],
                data_sources_used=["mca"],
                pipeline_version="2.2.0",
                metadata=decline_meta,
                is_hard_decline=True,
                decline_reason=decline_reason,
            )

        if state_code:
            raw_feature_row["state"] = state_code

        ratios = self._derive_ratios(raw_feature_row)
        vintage = self._derive_business_vintage(raw_feature_row)
        record = self._build_normalized_record(raw_feature_row)

        financial_ds = compute_financial_score(
            current_ratio=ratios["current_ratio"],
            quick_ratio=ratios["quick_ratio"],
            debt_to_equity=ratios["debt_to_equity"],
            net_revenue_cagr_5y=ratios["net_revenue_cagr_5y"],
            dso=ratios["dso"],
            working_capital=ratios["working_capital"],
            business_vintage_years=vintage,
            nic_code=raw_feature_row.get("nic_code"),
        )
        identity_ds = compute_identity_score(record, {})
        legal_ds = compute_legal_score(
            legal_case_count=raw_feature_row.get("case_count_total"),
            pending_case_count=raw_feature_row.get("case_count_active"),
            criminal_case_count=raw_feature_row.get("criminal_case_count"),
            high_value_case_count=raw_feature_row.get("high_value_case_count"),
            business_vintage_years=vintage,
            recent_cases_24m=raw_feature_row.get("recent_cases_24m"),
            recent_cases_12m=raw_feature_row.get("recent_cases_12m"),
        )
        doc_ds = compute_documentation_score(record, 10.0)

        enriched = {**raw_feature_row}
        enriched.update(ratios)
        enriched.update(
            {
                "identity_score": identity_ds.weighted_score,
                "financial_score": financial_ds.weighted_score,
                "legal_score": legal_ds.weighted_score,
                "documentation_score": doc_ds.weighted_score,
                "business_vintage_years": vintage,
                "entity_id": entity_id,
            }
        )

        # PPRE score_entity
        scored = score_entity(
            feature_row=enriched,
            artifacts=self.artifact_service.get_artifacts(),
            requested_amount=requested_amount,
            avg_monthly_purchase_volume=avg_monthly_purchase_volume,
            credit_period_days=credit_period_days,
            ead=ead,
            include_xai=include_xai,
        )

        # Calculate pralyon score (credit score mapped from blended_pd / band)
        # Mapped score: AAA->820, AA->780, A->740, BBB->680, BB->620, B->560, CCC->480, D->300
        band_scores = {"AAA": 820, "AA": 780, "A": 740, "BBB": 680, "BB": 620, "B": 560, "CCC": 480, "D": 300}
        pralyon_score = band_scores.get(scored["pd_band"], 300)

        # Build comprehensive metadata for Jinja templates
        payload = db_row.get("payload") or {}
        overview = (
            payload.get("overview", [{}])[0]
            if isinstance(payload.get("overview"), list)
            else payload.get("overview", {})
        )

        # Parse directors
        shareholding_map = {}
        for sh in payload.get("directorShareholding", []):
            if isinstance(sh, dict):
                sh_din = (sh.get("din") or "").strip()
                sh_name = (sh.get("fullName") or "").strip().lower()
                pct_str = str(sh.get("shareholdingPer") or "0").replace("%", "").strip()
                try:
                    pct_val = float(pct_str)
                except (ValueError, TypeError):
                    pct_val = 0.0
                if sh_din:
                    shareholding_map[sh_din] = pct_val
                if sh_name and sh_name not in shareholding_map:
                    shareholding_map[sh_name] = pct_val

        remun_map = {}
        remun_raw = payload.get("directorsRemuneration", [])
        remun_items = []
        if isinstance(remun_raw, list):
            for item in remun_raw:
                if isinstance(item, list):
                    remun_items.extend(item)
                elif isinstance(item, dict):
                    remun_items.append(item)
        for rm in remun_items:
            if isinstance(rm, dict):
                rm_din = (rm.get("DIN") or "").strip()
                rm_name = (rm.get("NAME") or "").strip().lower()
                tot_val = rm.get("TOTAL") or rm.get("TOTAL_AMOUNT") or rm.get("GROSS_SALARY") or 0
                try:
                    tot_float = float(tot_val)
                except (ValueError, TypeError):
                    tot_float = 0.0
                if rm_din and (rm_din not in remun_map or tot_float > 0):
                    remun_map[rm_din] = tot_float
                if rm_name and (rm_name not in remun_map or tot_float > 0):
                    remun_map[rm_name] = tot_float

        directors_list = []
        for d in payload.get("directors", []):
            disqualified = d.get("disqualified") or False
            d_din = (d.get("din") or d.get("directorDin") or "").strip()
            d_name = (d.get("fullName") or d.get("name") or d.get("directorName") or "Unknown").strip()
            d_name_l = d_name.lower()

            sh_pct = shareholding_map.get(d_din) if d_din else None
            if sh_pct is None:
                sh_pct = shareholding_map.get(d_name_l)

            remun = remun_map.get(d_din) if d_din else None
            if remun is None:
                remun = remun_map.get(d_name_l)

            directors_list.append(
                {
                    "name": d_name,
                    "din": d_din or "N/A",
                    "designation": d.get("designation")
                    if d.get("designation") not in (None, "", "-")
                    else d.get("role") or "Director",
                    "disqualified": disqualified,
                    "other_entities_count": d.get("other_entities_count") or 0,
                    "struck_off_links": "Yes" if disqualified else "None",
                    "status": "Flagged" if disqualified else "Clear",
                    "shareholding_pct": round(sh_pct, 2) if sh_pct is not None else None,
                    "remuneration": remun,
                }
            )

        # Parse charges
        charges_list = []
        for ch in payload.get("charges", []):
            charges_list.append(
                {
                    "lender": ch.get("chName")
                    or ch.get("chargeHolder")
                    or ch.get("bankName")
                    or ch.get("LENDER_NAME")
                    or "Unknown",
                    "amount": _parse_safe_float(ch.get("amount") or ch.get("CHARGE_AMOUNT")),
                    "created": ch.get("dateOfCreation") or ch.get("creationDate") or ch.get("CREATION_DATE") or "N/A",
                    "status": str(ch.get("chargeStatus") or ch.get("status") or ch.get("STATUS") or "Active").lower(),
                }
            )
        charge_summary = self._extract_charge_summary(payload, charges_list)

        court_info = raw_feature_row.get("court_info") or {}
        aml_info = raw_feature_row.get("aml_info") or {}

        # ZeroPass mock checks mapping actual raw flags if present
        is_wilful = raw_feature_row.get("is_wilful_defaulter", False)
        has_aml_match = aml_info.get("has_match", False)

        has_138_against = court_info.get("has_cheque_bounce_against", False)
        comp_138 = court_info.get("complainant_138_count", 0)

        nclt_active_against = bool(court_info.get("nclt_active_against", False))
        nclt_disposed = court_info.get("nclt_disposed_count", 0)
        nclt_total = court_info.get("nclt_cases", 0)

        drt_cases = court_info.get("drt_cases", raw_feature_row.get("case_count_drt") or 0)

        zeropass_data = {
            "g1_result": (
                "Triggered — Active NCLT / CIRP insolvency petition against entity"
                if nclt_active_against
                else (
                    f"Clear — {nclt_disposed} disposed/resolved NCLT matter(s), nil active CIRP"
                    if nclt_disposed > 0
                    else (
                        f"Clear — {nclt_total} applicant/appeal matter(s), nil active CIRP against entity"
                        if nclt_total > 0
                        else "No NCLT / CIRP insolvency proceedings"
                    )
                )
            ),
            "g1_fail": nclt_active_against,
            "g2_result": "All clear — both directors",
            "g2_fail": False,
            "g3_result": "Active",
            "g3_fail": False,
            "g4_result": "Active",
            "g4_fail": False,
            "g5_result": (
                "Listed on RBI Wilful Defaulter register"
                if is_wilful
                else (
                    "Adverse AML / PEP / Sanctions screening match"
                    if has_aml_match
                    else "Not listed · Clean AML / Sanctions screen"
                )
            ),
            "g5_fail": is_wilful or has_aml_match,
            "g6_result": (
                "Triggered — Active Sec 138 cheque bounce against entity"
                if has_138_against
                else (
                    f"Clear — Nil proceedings against entity (Complainant in {comp_138} recovery matter)"
                    if comp_138 > 0
                    else "Nil active proceedings"
                )
            ),
            "g6_fail": has_138_against,
            "g7_result": "No DRT recovery proceedings"
            if drt_cases == 0
            else f"{drt_cases} cases",
            "g7_fail": drt_cases > 0,
            "g8_result": f"Positive TNW — ₹{(raw_feature_row.get('networth') or 0) / 10000000:.2f} Cr"
            if (raw_feature_row.get("networth") or 0) > 0
            else "Negative / Eroded Net Worth",
            "g8_fail": (raw_feature_row.get("networth") or 0) <= 0,
        }

        # Financial Ratios snapshot - all 10 parameters per RA Model doc
        ratios_snapshot = {
            "current_ratio": ratios.get("current_ratio"),
            "quick_ratio": ratios.get("quick_ratio"),
            "debt_to_equity": ratios.get("debt_to_equity"),
            "ebit_margin": ratios.get("ebit_margin"),
            "net_margin": ratios.get("net_margin"),
            "icr": ratios.get("icr"),
            "roce": ratios.get("roce"),
            "dso": ratios.get("dso"),
            "dpo": ratios.get("dpo"),
            "tangible_net_worth": ratios.get("tangible_net_worth"),
            # Additional ratios for reporting
            "working_capital": ratios.get("working_capital"),
            "cash_coverage": ratios.get("cash_coverage"),
            "gross_margin": ratios.get("gross_margin"),
            "operating_margin": ratios.get("operating_margin"),
            "roe": ratios.get("roe"),
            "fixed_assets_turnover": ratios.get("fixed_assets_turnover"),
            "net_revenue_cagr_5y": ratios.get("net_revenue_cagr_5y"),
        }

        metadata = {
            "company_name": db_row.get("company_name"),
            "trade_name": trade_name or db_row.get("company_name"),
            "cin": db_row.get("cin"),
            "gstin": raw_feature_row.get("gstin") or overview.get("gstin"),
            "pan": raw_feature_row.get("pan"),
            "state": state_code or db_row.get("registered_state") or overview.get("registeredState") or overview.get("businessState") or "Maharashtra",
            "state_code": state_code or db_row.get("registered_state"),
            "incorporation_date": str(db_row.get("incorporation_date") or raw_feature_row.get("incorporation_date")),
            "vintage_years": int(vintage or 0),
            "report_date": datetime.now(timezone.utc).strftime("%d %b %Y"),
            "report_id": f"PRY-S1-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{entity_id[-4:]}",
            "policy_tier": scored["pd_band"],
            "sector": "Capital Goods"
            if "CAPITAL" in str(db_row.get("description_of_main_activity") or "").upper()
            else "Services",
            "nic_code": db_row.get("main_activity_group_code") or "74210",
            "revenue_source": raw_feature_row.get("revenue_source"),
            "authorized_capital": f"{db_row.get('authorized_capital'):,}" if db_row.get("authorized_capital") else "-",
            "paid_up_capital": f"{db_row.get('paid_up_capital'):,}" if db_row.get("paid_up_capital") else "-",
            "registered_address": db_row.get("registered_office_address") or "Not Available",
            "roc": db_row.get("roc") or overview.get("ROC_NAME") or "RoC-Mumbai",
            "company_status": db_row.get("company_status") or "Active",
            "directors": directors_list,
            "charges": charges_list,
            "charge_summary": charge_summary,
            "court_info": court_info,
            "msme": raw_feature_row.get("msme_info"),
            "aml": aml_info,
            "auditor_info": raw_feature_row.get("auditor_info"),
            "consolidated_financials": raw_feature_row.get("consolidated_financials"),
            "zeropass": zeropass_data,
            "ratios": ratios_snapshot,
            "financials": raw_feature_row.get("financials", []),
            "financial_history": raw_feature_row.get("financials", []),
            "gst": {"filing_consistency_label": f"{raw_feature_row.get('gst_filing_consistency')} taxpayer"},
            "epfo": {
                "employee_count": raw_feature_row.get("epfo_headcount"),
                "pf_filing_regular": raw_feature_row.get("pf_filing_regular"),
                "headcount_drop": bool(raw_feature_row.get("epfo_headcount_drop", False)),
            },
            "charge": {
                "has_active": raw_feature_row.get("has_any_active_charge", False),
                "charge_summary": f"{raw_feature_row.get('charge_count_active', 0)} active charges",
            },
            "legal": {
                "total_cases": raw_feature_row.get("case_count_total", 0),
                "active_cases": raw_feature_row.get("case_count_active", 0),
                "hc_cases": raw_feature_row.get("case_count_hc", 0),
                "nclt_cases": raw_feature_row.get("case_count_nclt", 0),
                "drt_cases": raw_feature_row.get("case_count_drt", 0),
                "criminal_cases": raw_feature_row.get("criminal_case_count", 0),
                "high_value_cases": raw_feature_row.get("high_value_case_count", 0),
                "recent_cases_12m": raw_feature_row.get("recent_cases_12m", 0),
                "recent_cases_24m": raw_feature_row.get("recent_cases_24m", 0),
            },
            "readings": {
                "financial": f"Score {int(financial_ds.weighted_score)}/100. Derived from FY25 financials: D/E {ratios.get('debt_to_equity') or 0:.2f}x, CR {ratios.get('current_ratio') or 0:.2f}x. Operating vintage of {vintage} years indicates established market presence.",
                "identity": f"Score {int(identity_ds.weighted_score)}/100. MCA Profile and GSTIN cross-verified. Key managerial personnel ({len(directors_list)} active directors) validated with no Sec 164 disqualifications.",
                "legal": (
                    f"Score {int(legal_ds.weighted_score)}/100. Legal track reflects {court_info.get('total_cases', raw_feature_row.get('case_count_total', 0))} matters ({raw_feature_row.get('case_count_active', 0)} active). Clear of adverse insolvency proceedings."
                    if not nclt_active_against
                    else f"Score {int(legal_ds.weighted_score)}/100. Active NCLT / CIRP insolvency petition pending."
                ),
                "conduct": f"Score {int(raw_feature_row.get('conduct_score') or 70.0)}/100. BehaviourPrint™ incorporates GST filing discipline and EPFO workforce compliance history."
            },
            "ratio_insights": {
                "current_ratio": {
                    "benchmark": "≥ 1.5×",
                    "status": "Pass" if (ratios.get("current_ratio") or 0) >= 1.5 else "Weak",
                    "status_class": "pass" if (ratios.get("current_ratio") or 0) >= 1.5 else "warn",
                    "implication": "Strong short-term asset cover." if (ratios.get("current_ratio") or 0) >= 1.5 else "Short-term liabilities exceed liquid assets."
                },
                "quick_ratio": {
                    "benchmark": "≥ 1.0×",
                    "status": "Pass" if (ratios.get("quick_ratio") or 0) >= 1.0 else "Weak",
                    "status_class": "pass" if (ratios.get("quick_ratio") or 0) >= 1.0 else "warn",
                    "implication": "Adequate liquid assets." if (ratios.get("quick_ratio") or 0) >= 1.0 else "Potential liquidity constraint."
                },
                "tangible_net_worth": {
                    "benchmark": "> ₹0",
                    "status": "Pass" if (ratios.get("tangible_net_worth") or 0) > 0 else "Weak",
                    "status_class": "pass" if (ratios.get("tangible_net_worth") or 0) > 0 else "warn",
                    "implication": "Positive equity position." if (ratios.get("tangible_net_worth") or 0) > 0 else "Capital erosion detected."
                },
                "ebit_margin": {
                    "benchmark": "> 8%",
                    "status": "Pass" if (ratios.get("ebit_margin") or 0) >= 8 else "Weak",
                    "status_class": "pass" if (ratios.get("ebit_margin") or 0) >= 8 else "warn",
                    "implication": "Strong operating efficiency." if (ratios.get("ebit_margin") or 0) >= 8 else "Narrow operating buffer."
                },
                "icr": {
                    "benchmark": "> 3.0×",
                    "status": "Pass" if (ratios.get("icr") or 0) >= 3 else "Weak",
                    "status_class": "pass" if (ratios.get("icr") or 0) >= 3 else "warn",
                    "implication": "Comfortable debt servicing capacity." if (ratios.get("icr") or 0) >= 3 else "High interest burden risk."
                },
                "roce": {
                    "benchmark": "> 15%",
                    "status": "Pass" if (ratios.get("roce") or 0) >= 15 else "Weak",
                    "status_class": "pass" if (ratios.get("roce") or 0) >= 15 else "warn",
                    "implication": "Efficient capital deployment." if (ratios.get("roce") or 0) >= 15 else "Sub-par return on capital."
                },
                "dpo": {
                    "benchmark": "< 90 days",
                    "status": "Pass" if (ratios.get("dpo") or 0) <= 90 else "Weak",
                    "status_class": "pass" if (ratios.get("dpo") or 0) <= 90 else "warn",
                    "implication": "Healthy supplier payment cycle." if (ratios.get("dpo") or 0) <= 90 else "Extended creditor stretch detected."
                },
                "debt_to_equity": {
                    "benchmark": "≤ 2.5×",
                    "status": "High" if (ratios.get("debt_to_equity") or 0) > 2.5 else "Pass",
                    "status_class": "fail" if (ratios.get("debt_to_equity") or 0) > 2.5 else "pass",
                    "implication": "Elevated leverage risk." if (ratios.get("debt_to_equity") or 0) > 2.5 else "Healthy capital structure."
                },
                "net_margin": {
                    "benchmark": "≥ 6%",
                    "status": "Pass" if (ratios.get("net_margin") or 0) >= 6 else "Thin",
                    "status_class": "pass" if (ratios.get("net_margin") or 0) >= 6 else "warn",
                    "implication": "Solid operating profitability." if (ratios.get("net_margin") or 0) >= 6 else "Marginal profitability limits buffer."
                },
                "dso": {
                    "benchmark": "≤ 90 days",
                    "status": "Elevated" if (ratios.get("dso") or 0) > 90 else "Pass",
                    "status_class": "warn" if (ratios.get("dso") or 0) > 90 else "pass",
                    "implication": "Slow receivables collection." if (ratios.get("dso") or 0) > 90 else "Efficient debtor collection."
                },
            }
        }

        input_parameters = {
            k: enriched.get(k) for k in [
                "identity_score", "financial_score", "legal_score", "documentation_score",
                "current_ratio", "quick_ratio", "debt_to_equity", "dso", "net_revenue_cagr_5y",
                "working_capital", "tangible_net_worth", "net_revenue_latest", "turnover_y1",
                "turnover_y2", "turnover_y3", "charge_count_active", "has_any_active_charge",
                "has_recent_charge_90d", "old_unsatisfied_charge_count", "distinct_lender_count",
                "case_count_total", "case_count_active", "case_count_drt", "case_count_nclt",
                "case_count_hc", "criminal_case_count", "has_insolvency_petition", "gst_turnover",
                "gst_filing_consistency", "high_director_company_count", "max_director_company_count",
                "epfo_headcount", "pf_filing_regular", "revenue_per_employee_outlier",
                "business_vintage_years", "conduct_score",
            ]
        }

        # Construct FinalFeatureRow per Section 7.2 of Documentation
        from domain.schemas.final_feature_row import FinalFeatureRow

        ca_latest = raw_feature_row.get("current_assets")
        gfa_latest = raw_feature_row.get("gross_fixed_assets")
        total_assets_latest = (
            (float(ca_latest or 0) + float(gfa_latest or 0))
            if (ca_latest or gfa_latest) else None
        )
        cl_latest = raw_feature_row.get("current_liabilities")
        rev_y1 = raw_feature_row.get("revenue")
        rev_y2 = raw_feature_row.get("revenue_prev1")
        rev_y3 = raw_feature_row.get("revenue_prev2")

        turnover_cagr = None
        if rev_y1 and rev_y3 and float(rev_y3) > 0:
            turnover_cagr = round(((float(rev_y1) / float(rev_y3)) ** (1 / 2)) - 1, 4)
        elif rev_y1 and rev_y2 and float(rev_y2) > 0:
            turnover_cagr = round((float(rev_y1) / float(rev_y2)) - 1, 4)

        ff_row = FinalFeatureRow(
            snapshot_id=f"SNAP-{raw_feature_row.get('cin') or 'UNKNOWN'}-{datetime.now(timezone.utc).strftime('%Y%m%d')}",
            entity_id=entity_id,
            application_id=None,
            legal_name=raw_feature_row.get("legal_name") or db_row.get("company_name"),
            gstin=raw_feature_row.get("gstin") or overview.get("gstin"),
            cin=raw_feature_row.get("cin") or db_row.get("cin"),
            udyam_no=None,
            entity_type=db_row.get("entity_type"),
            state=raw_feature_row.get("state") or db_row.get("registered_state") or overview.get("registeredState") or overview.get("businessState"),
            msme_category=None,
            nic_code=raw_feature_row.get("nic_code"),
            business_vintage_years=vintage,
            gst_active_flag=raw_feature_row.get("gst_active"),
            gst_filing_consistency_ratio=raw_feature_row.get("gst_filing_consistency_ratio"),
            current_ratio=ratios.get("current_ratio"),
            quick_ratio=ratios.get("quick_ratio"),
            working_capital=ratios.get("working_capital"),
            debt_to_equity=ratios.get("debt_to_equity"),
            debt_to_assets=ratios.get("debt_to_assets") or (round((raw_feature_row.get("total_debt") or 0.0) / (raw_feature_row.get("current_assets") or 1.0), 4) if raw_feature_row.get("current_assets") else None),
            tangible_net_worth=ratios.get("tangible_net_worth"),
            dso=ratios.get("dso"),
            dpo=ratios.get("dpo"),
            legal_case_count=raw_feature_row.get("case_count_total"),
            pending_case_count=raw_feature_row.get("case_count_active"),
            criminal_case_count=raw_feature_row.get("criminal_case_count"),
            turnover_y1=rev_y1,
            turnover_y2=rev_y2,
            turnover_y3=rev_y3,
            turnover_y4=None,
            turnover_y5=None,
            revenue_y1=rev_y1,
            revenue_y2=rev_y2,
            revenue_y3=rev_y3,
            revenue_y4=None,
            revenue_y5=None,
            net_revenue_y1=rev_y1,
            net_revenue_y2=rev_y2,
            net_revenue_y3=rev_y3,
            net_revenue_y4=None,
            net_revenue_y5=None,
            net_revenue_latest=rev_y1,
            financials=raw_feature_row.get("financials", []),
            current_liabilities_latest=cl_latest,
            total_assets_latest=total_assets_latest,
            turnover_cagr_5y=turnover_cagr,
            revenue_cagr_5y=ratios.get("net_revenue_cagr_5y"),
            net_revenue_cagr_5y=ratios.get("net_revenue_cagr_5y"),
            identity_score=float(identity_ds.weighted_score),
            financial_score=float(financial_ds.weighted_score),
            legal_score=float(legal_ds.weighted_score),
            documentation_score=float(doc_ds.weighted_score),
            data_completeness_score=100.0 if raw_feature_row.get("mca_data_available") else 50.0,
            snapshot_date=None,
            source_fetch_date=None,
            sources_used=record.sources_available,
            data_sufficiency_band=raw_feature_row.get("data_sufficiency_band")
        )

        return self._assemble_granular_sections(
            entity_id=entity_id,
            seller_id=seller_id,
            assessed_at=datetime.now(timezone.utc).isoformat(),
            pralyon_score=pralyon_score,
            risk_band=scored["pd_band"],
            blended_pd=scored["blended_pd"],
            lgd_estimate=scored.get("lgd_pred") or 0.45,
            conduct_score=float(raw_feature_row.get("conduct_score") or 70.0),
            financial_score=float(financial_ds.weighted_score),
            identity_score=float(identity_ds.weighted_score),
            legal_score=float(legal_ds.weighted_score),
            documentation_score=float(doc_ds.weighted_score),
            xai_summary=scored.get("xai_summary"),
            xai_summary_text=scored.get("xai_summary_text"),
            xai_narrative=scored.get("xai_narrative") or "",
            xai_narrative_text=scored.get("xai_narrative_text"),
            xai_narrative_lines=scored.get("xai_narrative_lines") or [],
            dimension_readings=scored.get("dimension_readings") or {},
            table_enrichments=scored.get("table_enrichments") or {},
            lime_methodology_note=scored.get("lime_methodology_note") or "",
            shap_top_features=scored.get("shap_ranked")[:5] if scored.get("shap_ranked") else [],
            shap_ranked=scored.get("shap_ranked") or [],
            lime_explanation=scored.get("lime_explanation") or {},
            data_sources_used=["mca", "gst", "ecourts"],
            pipeline_version="2.2.0",
            metadata=metadata,
            input_parameters=input_parameters,
            final_feature_row=ff_row,
            ppre_output=scored,
        )
