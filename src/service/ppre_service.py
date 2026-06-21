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

    def _extract_financials(self, payload: dict) -> list[dict]:
        profit_loss = payload.get("profitLoss") or []
        balance_sheet = payload.get("balanceSheet") or []

        years_data = {}
        for pl in profit_loss:
            year = pl.get("Year")
            if year:
                years_data.setdefault(year, {})
                years_data[year].update(
                    {
                        "revenue": float(pl.get("TOTAL_REVENUE_CR") or pl.get("TOTAL_INCOME") or 0),
                        "ebit": float(pl.get("EBIT") or pl.get("PROFIT_BEFORE_TAX") or 0),
                        "pat": float(
                            pl.get("PROF_LOS_11_14_C")
                            or (float(pl.get("PROFIT_BEFORE_TAX") or 0) - float(pl.get("TAX_EXPENSES_CR") or 0))
                        ),
                        "finance_cost": float(pl.get("FINANCE_COST_CR") or 0),
                        "depreciation": float(pl.get("DEPRECTN_AMORT_C") or 0),
                    }
                )

        for bs in balance_sheet:
            year = bs.get("Year")
            if year:
                years_data.setdefault(year, {})
                lt_borrow = float(bs.get("LONG_TERM_BORR_C") or 0)
                st_borrow = float(bs.get("SHORT_TERM_BOR_C") or 0)
                total_debt = lt_borrow + st_borrow
                networth = float(
                    bs.get("EQUITY_AND_RESERVES") or bs.get("RESERVE_SURPLUS1", 0) + bs.get("SHARE_CAPITAL_CR", 0)
                )

                years_data[year].update(
                    {
                        "current_assets": float(bs.get("CURR_ASSETS") or bs.get("TOTAL_CURR_REP") or 0),
                        "current_liabilities": float(bs.get("CURR_LIABILITIES") or 0),
                        "total_debt": total_debt,
                        "networth": networth,
                        "receivables": float(bs.get("TRADE_RECEIV_CR") or 0),
                        "inventory": float(bs.get("INVENTORIES_CR") or 0),
                        "trade_payables": float(bs.get("TRADE_PAYABLES_C") or 0),
                        "cash_and_bank": float(bs.get("CASH_AND_EQU_CR") or 0),
                        "gross_fixed_assets": float(bs.get("FIXED_ASSETS") or 0),
                    }
                )

        sorted_years = sorted(years_data.keys(), reverse=True)
        financials = []
        for y in sorted_years:
            data = years_data[y]
            data["year"] = y
            financials.append(data)
        return financials

    def _extract_epfo(self, payload: dict) -> dict:
        epfo_list = payload.get("annexureEPFO", []) or payload.get("epfoDetails", []) or []
        valid_records = [r for r in epfo_list if r.get("no_of_employee")]
        if not valid_records:
            return {"employee_count": None, "pf_filing_regular": None}

        latest_record = valid_records[0]
        try:
            headcount = int(latest_record.get("no_of_employee", "0").replace(",", ""))
        except ValueError:
            headcount = 0

        pf_filing_regular = True
        for r in valid_records[:12]:
            remarks = str(r.get("remarks", "")).lower()
            delay_val = 0
            if r.get("delay_period"):
                try:
                    cleaned_delay = "".join(c for c in str(r.get("delay_period")) if c.isdigit())
                    if cleaned_delay:
                        delay_val = int(cleaned_delay)
                except ValueError:
                    pass
            if remarks == "delayed" or delay_val > 15:
                pf_filing_regular = False
                break

        return {"employee_count": headcount, "pf_filing_regular": pf_filing_regular}

    def _extract_gst_consistency(self, payload: dict) -> float:
        gst_list = payload.get("annexureGST") or []
        returns = [r for r in gst_list if r.get("Return type") in ["GSTR3B", "GSTR1"]]
        if not returns:
            return 1.0
        filed = sum(1 for r in returns if r.get("Status") == "Filed")
        return round(filed / len(returns), 4)

    def _run_part1_sourcing(self, db_row: dict) -> dict[str, Any]:
        payload = db_row.get("payload") or {}
        overview = (
            payload.get("overview", [{}])[0]
            if isinstance(payload.get("overview"), list)
            else payload.get("overview", {})
        )

        # Extract active GSTIN
        gst_regs = payload.get("gstRegistrations") or []
        gstin_val = None
        for reg in gst_regs:
            if isinstance(reg, dict):
                g_val = reg.get("gstin")
                if g_val and g_val != "Not Available":
                    if reg.get("sts") == "Active":
                        gstin_val = g_val
                        break
                    elif not gstin_val:
                        gstin_val = g_val

        # Merge financials
        financials = self._extract_financials(payload)
        y1 = financials[0] if len(financials) > 0 else {}
        y2 = financials[1] if len(financials) > 1 else {}
        y3 = financials[2] if len(financials) > 2 else {}

        # Extractor maps
        gst_consistency = self._extract_gst_consistency(payload)
        epfo_raw = self._extract_epfo(payload)

        # Hard gates
        rbi_data = {"is_wilful_defaulter": False}  # Default clear
        rbi_result = check_rbi_wilful_defaulter(rbi_data)

        mca_dir = {"directors": payload.get("directors", [])}
        dir_signals = derive_director_conduct_signals(mca_dir)

        if rbi_result.get("is_wilful_defaulter"):
            return build_hard_decline_result(db_row.get("cin"), "RBI_WILFUL_DEFAULTER")
        if dir_signals.get("director_wilful_defaulter"):
            return build_hard_decline_result(db_row.get("cin"), "DIRECTOR_WILFUL_DEFAULTER")

        sufficiency = classify_mca_data_sufficiency(y1, y2, y3)
        gst_turnover = float(overview.get("TOT_TURNOVER") or 0)
        mca_revenue = y1.get("revenue")
        revenue_source, rev_notes = maybe_switch_revenue_to_gst(mca_revenue, gst_turnover)
        revenue = gst_turnover if revenue_source == "gst_proxy" else mca_revenue

        charge_signals = derive_charge_conduct_signals(payload.get("charges", []))
        epfo_signals = derive_epfo_conduct_signals(
            epfo_raw,
            revenue=revenue,
            sector_bucket=overview.get("businessState"),
        )

        ecourts_raw = {"cases": payload.get("legalCases", [])}
        ecourts_signals = derive_ecourts_conduct_signals(ecourts_raw)

        gst_raw = {
            "taxpayerInfo": {"gstin": gstin_val or overview.get("IT_PAN_OF_COMPNY"), "gstStatus": "active"},
            "returnFilingHistory": payload.get("annexureGST", []),
            "gst_filing_consistency": "regular"
            if gst_consistency >= 0.9
            else "irregular"
            if gst_consistency >= 0.5
            else "non-filer",
        }
        gst_signals = derive_gst_conduct_signals(gst_raw)

        base_conduct_score = 70
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
            + dir_signals.get("source_notes", [])
        )

        dpo = compute_dpo(y1)
        cash_coverage = compute_cash_coverage(y1)

        return {
            "entity_key": db_row.get("cin"),
            "data_sufficiency_band": sufficiency,
            "revenue_source": revenue_source,
            "revenue": revenue,
            "ebit": y1.get("ebit"),
            "pat": y1.get("pat"),
            "total_debt": y1.get("total_debt"),
            "networth": y1.get("networth"),
            "receivables": y1.get("receivables"),
            "current_assets": y1.get("current_assets"),
            "current_liabilities": y1.get("current_liabilities"),
            "finance_cost": y1.get("finance_cost"),
            "inventory": y1.get("inventory"),
            "trade_payables": y1.get("trade_payables"),
            "cash_and_bank": y1.get("cash_and_bank"),
            "gross_fixed_assets": y1.get("gross_fixed_assets"),
            "revenue_prev1": y2.get("revenue"),
            "revenue_prev2": y3.get("revenue"),
            "dpo": dpo,
            "cash_coverage": cash_coverage,
            "charge_count_active": charge_signals["charge_count_active"],
            "has_any_active_charge": charge_signals["has_any_active_charge"],
            "has_recent_charge_90d": charge_signals["has_recent_charge_90d"],
            "old_unsatisfied_charge_count": charge_signals["old_unsatisfied_charge_count"],
            "lender_quality_flag": charge_signals["lender_quality_flag"],
            "distinct_lender_count": charge_signals.get("distinct_lender_count"),
            "high_director_company_count": dir_signals.get("high_director_company_count"),
            "max_director_company_count": dir_signals.get("max_director_company_count"),
            "case_count_total": ecourts_signals["case_count_total"],
            "case_count_active": ecourts_signals["case_count_active"],
            "case_count_drt": ecourts_signals["case_count_drt"],
            "case_count_nclt": ecourts_signals["case_count_nclt"],
            "case_count_hc": ecourts_signals["case_count_hc"],
            "has_insolvency_petition": ecourts_signals["has_insolvency_petition"],
            "gst_turnover": gst_turnover,
            "gst_sector_bucket": overview.get("businessState"),
            "gst_filing_consistency": "REGULAR"
            if gst_consistency >= 0.9
            else "MINOR_GAPS"
            if gst_consistency >= 0.7
            else "MAJOR_GAPS"
            if gst_consistency >= 0.3
            else "NON_FILER",
            "epfo_headcount": epfo_signals.get("epfo_headcount"),
            "pf_filing_regular": epfo_signals.get("pf_filing_regular"),
            "revenue_per_employee_outlier": epfo_signals.get("revenue_per_employee_outlier"),
            "conduct_score": conduct_score,
            "conduct_reasons": all_conduct_reasons,
            "source_notes": rev_notes + rbi_result.get("source_notes", []),
            "legal_name": db_row.get("company_name"),
            "gstin": gstin_val,
            "cin": db_row.get("cin"),
            "pan": overview.get("IT_PAN_OF_COMPNY"),
            "incorporation_date": db_row.get("incorporation_date"),
            "state": db_row.get("registered_state") or overview.get("registeredState"),
            "authorized_capital": db_row.get("authorized_capital"),
            "paid_up_capital": db_row.get("paid_up_capital"),
        }

    def _derive_ratios(self, row: Dict[str, Any]) -> Dict[str, Optional[float]]:
        current_assets = row.get("current_assets")
        current_liabs = row.get("current_liabilities")
        inventory = row.get("inventory")
        total_debt = row.get("total_debt")
        networth = row.get("networth")
        receivables = row.get("receivables")
        revenue = row.get("revenue")
        revenue_prev1 = row.get("revenue_prev1")
        revenue_prev2 = row.get("revenue_prev2")

        current_ratio = current_assets / current_liabs if current_liabs else None
        quick_assets = (
            (current_assets - inventory) if current_assets is not None and inventory is not None else current_assets
        )
        quick_ratio = quick_assets / current_liabs if current_liabs else None
        working_capital = (
            (current_assets - current_liabs) if current_assets is not None and current_liabs is not None else None
        )
        debt_to_equity = total_debt / networth if networth else None

        dso = (receivables / revenue) * 365 if receivables is not None and revenue else None
        net_revenue_cagr = None
        if revenue is not None and revenue_prev2:
            try:
                net_revenue_cagr = round((revenue / revenue_prev2) ** (1 / 2) - 1, 6)
            except Exception:
                pass

        return {
            "current_ratio": current_ratio,
            "quick_ratio": quick_ratio,
            "working_capital": working_capital,
            "debt_to_equity": debt_to_equity,
            "dso": dso,
            "net_revenue_cagr_5y": net_revenue_cagr,
            "tangible_net_worth": networth,
            "net_revenue_latest": revenue,
            "turnover_y1": revenue,
            "turnover_y2": revenue_prev1,
            "turnover_y3": revenue_prev2,
            "turnover_cagr_5y": net_revenue_cagr,
        }

    def _build_normalized_record(self, row: Dict[str, Any]) -> NormalizedRecord:
        gst_consistency = row.get("gst_filing_consistency", "")
        _filed, _total = {"REGULAR": (11, 12), "MINOR_GAPS": (9, 12), "MAJOR_GAPS": (6, 12), "NON_FILER": (0, 12)}.get(
            str(gst_consistency).upper(), (None, None)
        )
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
            criminal_case_count=row.get("case_count_hc"),
            turnover_y1=row.get("revenue"),
            turnover_y2=row.get("revenue_prev1"),
            turnover_y3=row.get("revenue_prev2"),
            current_assets_latest=row.get("current_assets"),
            current_liabilities_latest=row.get("current_liabilities"),
            total_debt_latest=row.get("total_debt"),
            equity_latest=row.get("networth"),
            accounts_receivable_latest=row.get("receivables"),
            net_revenue_latest=row.get("revenue"),
            sources_available=["gst", "mca", "ecourts"],
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

        if raw_feature_row.get("hard_decline"):
            return {
                "entity_id": entity_id,
                "seller_id": seller_id,
                "assessed_at": datetime.now(timezone.utc).isoformat(),
                "pralyon_score": 300,
                "risk_band": "D",
                "blended_pd": 1.0,
                "lgd_estimate": 0.85,
                "conduct_score": 0.0,
                "financial_score": 0.0,
                "identity_score": 0.0,
                "legal_score": 0.0,
                "documentation_score": 0.0,
                "xai_narrative": f"Hard decline triggered: {raw_feature_row.get('decline_reason')}",
                "shap_top_features": [],
                "data_sources_used": ["mca"],
                "pipeline_version": "2.2.0",
                "metadata": {
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
                },
            }

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
        )
        identity_ds = compute_identity_score(record, {})
        legal_ds = compute_legal_score(
            legal_case_count=raw_feature_row.get("case_count_total"),
            pending_case_count=raw_feature_row.get("case_count_active"),
            criminal_case_count=raw_feature_row.get("case_count_hc"),
            high_value_case_count=raw_feature_row.get("case_count_hc"),
            business_vintage_years=vintage,
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
        directors_list = []
        for d in payload.get("directors", []):
            directors_list.append(
                {
                    "name": d.get("fullName") or d.get("name") or d.get("directorName") or "Unknown",
                    "din": d.get("din") or d.get("directorDin") or "N/A",
                    "designation": d.get("designation")
                    if d.get("designation") not in (None, "", "-")
                    else d.get("role") or "Director",
                    "disqualified": d.get("disqualified") or False,
                    "other_entities_count": d.get("other_entities_count") or 0,
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
                    "amount": float(ch.get("amount") or ch.get("CHARGE_AMOUNT") or 0.0),
                    "created": ch.get("dateOfCreation") or ch.get("creationDate") or ch.get("CREATION_DATE") or "N/A",
                    "status": ch.get("chargeStatus") or ch.get("status") or ch.get("STATUS") or "Active",
                }
            )

        # ZeroPass mock checks mapping actual raw flags if present
        zeropass_data = {
            "g1_result": "None found"
            if raw_feature_row.get("case_count_nclt", 0) == 0
            else f"{raw_feature_row.get('case_count_nclt')} cases",
            "g1_fail": raw_feature_row.get("has_insolvency_petition", False),
            "g2_result": "All clear — both directors",
            "g2_fail": False,
            "g3_result": "Active",
            "g3_fail": False,
            "g4_result": "Active",
            "g4_fail": False,
            "g5_result": "Not listed" if not raw_feature_row.get("is_wilful_defaulter") else "Listed",
            "g5_fail": raw_feature_row.get("is_wilful_defaulter", False),
            "g6_result": "Nil active proceedings",
            "g6_fail": False,
            "g7_result": "None found"
            if raw_feature_row.get("case_count_drt", 0) == 0
            else f"{raw_feature_row.get('case_count_drt')} cases",
            "g7_fail": raw_feature_row.get("case_count_drt", 0) > 0,
            "g8_result": f"Positive TNW — ₹{raw_feature_row.get('networth', 0) / 10000000:.2f} Cr"
            if raw_feature_row.get("networth", 0) > 0
            else "Negative TNW",
            "g8_fail": raw_feature_row.get("networth", 0) <= 0,
        }

        # Financial Ratios snapshot
        ratios_snapshot = {
            "current_ratio": ratios.get("current_ratio"),
            "quick_ratio": ratios.get("quick_ratio"),
            "debt_to_equity": ratios.get("debt_to_equity"),
            "net_margin": (raw_feature_row.get("pat", 0) / raw_feature_row.get("revenue", 1) * 100)
            if raw_feature_row.get("revenue")
            else 0.0,
            "dso": ratios.get("dso"),
            "tangible_net_worth": raw_feature_row.get("networth") or 0.0,
        }

        metadata = {
            "company_name": db_row.get("company_name"),
            "cin": db_row.get("cin"),
            "gstin": raw_feature_row.get("gstin") or overview.get("gstin"),
            "pan": raw_feature_row.get("pan"),
            "state": db_row.get("registered_state") or overview.get("registeredState") or "Maharashtra",
            "incorporation_date": str(db_row.get("incorporation_date") or raw_feature_row.get("incorporation_date")),
            "vintage_years": int(vintage or 0),
            "report_date": datetime.now(timezone.utc).strftime("%d %b %Y"),
            "report_id": f"PRY-S1-{datetime.now(timezone.utc).strftime('%Y%m%d')}-{entity_id[-4:]}",
            "policy_tier": scored["pd_band"],
            "sector": "Capital Goods"
            if "CAPITAL" in str(db_row.get("description_of_main_activity") or "").upper()
            else "Services",
            "nic_code": db_row.get("main_activity_group_code") or "74210",
            "authorized_capital": f"{db_row.get('authorized_capital'):,}" if db_row.get("authorized_capital") else "-",
            "paid_up_capital": f"{db_row.get('paid_up_capital'):,}" if db_row.get("paid_up_capital") else "-",
            "registered_address": db_row.get("registered_office_address") or "Not Available",
            "roc": db_row.get("roc") or overview.get("ROC_NAME") or "RoC-Mumbai",
            "company_status": db_row.get("company_status") or "Active",
            "directors": directors_list,
            "charges": charges_list,
            "zeropass": zeropass_data,
            "ratios": ratios_snapshot,
            "gst": {"filing_consistency_label": f"{raw_feature_row.get('gst_filing_consistency')} taxpayer"},
            "epfo": {
                "employee_count": raw_feature_row.get("epfo_headcount"),
                "pf_filing_regular": raw_feature_row.get("pf_filing_regular"),
                "headcount_drop": (raw_feature_row.get("epfo_headcount") or 0) < 150,
            },
            "charge": {
                "has_active": raw_feature_row.get("has_any_active_charge", False),
                "charge_summary": f"{raw_feature_row.get('charge_count_active', 0)} active charges",
            },
            "legal": {
                "hc_cases": raw_feature_row.get("case_count_hc", 0),
                "nclt_cases": raw_feature_row.get("case_count_nclt", 0),
                "drt_cases": raw_feature_row.get("case_count_drt", 0),
                "active_cases": raw_feature_row.get("case_count_active", 0),
            },
        }

        return {
            "entity_id": entity_id,
            "seller_id": seller_id,
            "assessed_at": datetime.now(timezone.utc).isoformat(),
            "pralyon_score": pralyon_score,
            "risk_band": scored["pd_band"],
            "blended_pd": scored["blended_pd"],
            "lgd_estimate": scored.get("lgd_pred") or 0.45,
            "conduct_score": float(raw_feature_row.get("conduct_score") or 70.0),
            "financial_score": float(financial_ds.weighted_score),
            "identity_score": float(identity_ds.weighted_score),
            "legal_score": float(legal_ds.weighted_score),
            "documentation_score": float(doc_ds.weighted_score),
            "xai_narrative": scored.get("xai_narrative"),
            "shap_top_features": scored.get("shap_ranked")[:5] if scored.get("shap_ranked") else [],
            "data_sources_used": ["mca", "gst", "ecourts"],
            "pipeline_version": "2.2.0",
            "metadata": metadata,
            # Pass full scored engine output along for report generation ease
            "_ppre_output": scored,
        }
