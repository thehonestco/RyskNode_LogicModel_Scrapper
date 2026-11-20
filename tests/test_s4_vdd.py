import os
import sys
from unittest.mock import AsyncMock, MagicMock, patch

import inject
import pytest
from fastapi.testclient import TestClient

# Ensure src is in sys.path
SRC_DIR = os.path.abspath(os.path.join(os.path.dirname(__file__), "../src"))
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from api.schema.vdd import (  # noqa: E402
    VendorDueDiligenceResponse,
)
from app.bootstrap import api  # noqa: E402
from service.report_service import ReportService  # noqa: E402
from service.vdd_service import VDDService, _verdict  # noqa: E402


@pytest.fixture
def sample_company_data():
    """Provides a realistic mock company dataset."""
    return {
        "cin": "U74210MH1999PLC123010",
        "company_name": "ABC Engineering Works Private Limited",
        "payload": {
            "overview": [
                {
                    "companyName": "ABC Engineering Works Private Limited",
                    "udyamNumber": "UDYAM-MH-01-0012345",
                    "nicCodes": [{"nicCode": "28110"}],
                }
            ],
            "gstRegistrations": [
                {
                    "gstin": "27AABCU9603R1ZM",
                    "sts": "Active",
                }
            ],
            "directors": [
                {
                    "name": "Ramesh Kumar",
                    "din": "01234567",
                    "disqualificationStatus": "Clean",
                },
                {
                    "name": "Suresh Kumar",
                    "din": "07654321",
                    "disqualificationStatus": "Clean",
                },
            ],
            "charges": [
                {
                    "id": "1001",
                    "holderName": "State Bank of India",
                    "amount": 25000000.0,
                    "creationDate": "2021-04-15",
                    "status": "OPEN",
                }
            ],
            "profitLoss": [
                {"revenue": 150000000.0, "ebit": 18000000.0, "pat": 12000000.0}
            ],
            "balanceSheet": [
                {"totalAssets": 80000000.0, "netWorth": 45000000.0}
            ],
        },
    }


@pytest.fixture
def sample_part1_sourcing():
    """Provides a realistic mock of _run_part1_sourcing return dictionary."""
    return {
        "cin": "U74210MH1999PLC123010",
        "pan": "AABCU9603R",
        "gstin": "27AABCU9603R1ZM",
        "gst_active": True,
        "gst_filing_consistency_ratio": 0.95,
        "gst_filing_consistency": "REGULAR",
        "incorporation_date": "1999-05-12",
        "state": "Maharashtra",
        "is_wilful_defaulter": False,
        "revenue": 150000000.0,
        "gst_turnover": 148000000.0,
        "court_info": {
            "total_cases": 1,
            "active_cases": 1,
            "has_cheque_bounce_against": False,
            "nclt_active_against": False,
            "drt_cases": 0,
            "detailed_cases": [],
        },
        "aml_info": {
            "has_match": False,
            "result": "NO_MATCH_FOUND",
            "pep_result": "NO_MATCH_FOUND",
        },
        "auditor_info": {"has_adverse": False, "summary": "Clean"},
        "msme_info": {"is_msme": True, "udyam": "UDYAM-MH-01-0012345"},
        "financials": [{"revenue": 150000000.0, "ebit": 18000000.0, "pat": 12000000.0}],
    }


class TestVDDVerdictLogic:
    """Test unit verdict determination rules."""

    def test_verdict_rbi_defaulter(self):
        status, headline, _ = _verdict(
            rbi_defaulter=True,
            confirmed_sanctions=False,
            critical_flags=0,
            high_flags=0,
            docs_missing=0,
        )
        assert status == "REJECTED"
        assert "Hard regulatory block" in headline

    def test_verdict_confirmed_sanctions(self):
        status, headline, _ = _verdict(
            rbi_defaulter=False,
            confirmed_sanctions=True,
            critical_flags=0,
            high_flags=0,
            docs_missing=0,
        )
        assert status == "REJECTED"
        assert "Hard regulatory block" in headline

    def test_verdict_critical_flags(self):
        status, headline, _ = _verdict(
            rbi_defaulter=False,
            confirmed_sanctions=False,
            critical_flags=2,
            high_flags=1,
            docs_missing=0,
        )
        assert status == "HOLD"
        assert "Critical blockers" in headline

    def test_verdict_conditional(self):
        status, headline, _ = _verdict(
            rbi_defaulter=False,
            confirmed_sanctions=False,
            critical_flags=0,
            high_flags=2,
            docs_missing=0,
        )
        assert status == "CONDITIONAL"
        assert "High-risk items require resolution" in headline

    def test_verdict_approved_clean(self):
        status, headline, _ = _verdict(
            rbi_defaulter=False,
            confirmed_sanctions=False,
            critical_flags=0,
            high_flags=0,
            docs_missing=0,
        )
        assert status == "APPROVED"
        assert "Approved for Onboarding" in headline

    def test_verdict_missing_docs_conditional(self):
        status, headline, _ = _verdict(
            rbi_defaulter=False,
            confirmed_sanctions=False,
            critical_flags=0,
            high_flags=0,
            docs_missing=2,
        )
        assert status == "CONDITIONAL"
        assert "Pending documentation" in headline

    def test_verdict_missing_docs_hold(self):
        status, headline, _ = _verdict(
            rbi_defaulter=False,
            confirmed_sanctions=False,
            critical_flags=0,
            high_flags=0,
            docs_missing=4,
        )
        assert status == "HOLD"
        assert "Missing mandatory documentation" in headline

    def test_verdict_high_flags_excessive_hold(self):
        status, headline, _ = _verdict(
            rbi_defaulter=False,
            confirmed_sanctions=False,
            critical_flags=0,
            high_flags=4,
            docs_missing=0,
        )
        assert status == "HOLD"
        assert "Multiple high-risk items" in headline


class TestVDDService:
    """Test VDDService end-to-end payload construction and schema validation."""

    @pytest.mark.anyio
    async def test_vendor_due_diligence_schema(self, sample_company_data, sample_part1_sourcing):
        mock_uow = MagicMock()
        mock_artifacts = MagicMock()
        vdd_service = VDDService(uow=mock_uow, artifact_service=mock_artifacts)

        # Mock PPRE calls
        vdd_service._ppre._get_company_data = AsyncMock(return_value=sample_company_data)
        vdd_service._ppre._run_part1_sourcing = MagicMock(return_value=sample_part1_sourcing)

        result = await vdd_service.vendor_due_diligence(
            entity_id="U74210MH1999PLC123010",
            requester_id="procurement-user-1",
            trade_name="ABC Engineering Works",
            vendor_profile="manufacturing",
        )

        assert isinstance(result, dict)
        assert "overview" in result
        assert "onboarding_decision" in result
        assert "entity_identity" in result
        assert "red_flag_register" in result
        assert "aml_integrity" in result
        assert "ownership_governance" in result
        assert "litigation_legal" in result
        assert "compliance_intelligence" in result
        assert "financial_quality" in result
        assert "mca_charges" in result
        assert "operational_viability" in result
        assert "document_register" in result
        assert "metadata" in result

        # Validate with Pydantic model
        validated = VendorDueDiligenceResponse.model_validate(result)
        assert validated.overview.vendor_name == "ABC Engineering Works"
        assert validated.onboarding_decision.status in ("APPROVED", "CONDITIONAL", "HOLD", "REJECTED")
        assert validated.metadata["entity_id"] == "U74210MH1999PLC123010"

    @pytest.mark.anyio
    async def test_vendor_due_diligence_rbi_block(self, sample_company_data, sample_part1_sourcing):
        mock_uow = MagicMock()
        mock_artifacts = MagicMock()
        vdd_service = VDDService(uow=mock_uow, artifact_service=mock_artifacts)

        # Simulate RBI wilful defaulter
        sample_part1_sourcing["is_wilful_defaulter"] = True
        vdd_service._ppre._get_company_data = AsyncMock(return_value=sample_company_data)
        vdd_service._ppre._run_part1_sourcing = MagicMock(return_value=sample_part1_sourcing)

        result = await vdd_service.vendor_due_diligence(
            entity_id="U74210MH1999PLC123010",
            requester_id="procurement-user-1",
        )

        assert result["onboarding_decision"]["status"] == "REJECTED"
        assert result["overview"]["escalation_level"] == "Risk Committee"
        flags = [f["issue"] for f in result["red_flag_register"]]
        assert "RBI wilful defaulter listing" in flags

    @pytest.mark.anyio
    async def test_vendor_due_diligence_sanctions_match(self, sample_company_data, sample_part1_sourcing):
        mock_uow = MagicMock()
        mock_artifacts = MagicMock()
        vdd_service = VDDService(uow=mock_uow, artifact_service=mock_artifacts)

        sample_part1_sourcing["aml_info"] = {
            "has_match": True,
            "result": "OFAC_SDN_CLOSE_MATCH",
            "pep_result": "NO_MATCH_FOUND",
        }
        vdd_service._ppre._get_company_data = AsyncMock(return_value=sample_company_data)
        vdd_service._ppre._run_part1_sourcing = MagicMock(return_value=sample_part1_sourcing)

        result = await vdd_service.vendor_due_diligence(
            entity_id="U74210MH1999PLC123010",
            requester_id="procurement-user-1",
        )

        assert result["onboarding_decision"]["status"] == "REJECTED"
        flags = [f["issue"] for f in result["red_flag_register"]]
        assert "Sanctions / watchlist match" in flags

    @pytest.mark.anyio
    async def test_vendor_due_diligence_cheque_bounce(self, sample_company_data, sample_part1_sourcing):
        mock_uow = MagicMock()
        mock_artifacts = MagicMock()
        vdd_service = VDDService(uow=mock_uow, artifact_service=mock_artifacts)

        sample_part1_sourcing["court_info"]["has_cheque_bounce_against"] = True
        vdd_service._ppre._get_company_data = AsyncMock(return_value=sample_company_data)
        vdd_service._ppre._run_part1_sourcing = MagicMock(return_value=sample_part1_sourcing)

        result = await vdd_service.vendor_due_diligence(
            entity_id="U74210MH1999PLC123010",
            requester_id="procurement-user-1",
        )

        assert result["onboarding_decision"]["status"] == "HOLD"
        flags = [f["issue"] for f in result["red_flag_register"]]
        assert "Cheque bounce matters (NI Act 138)" in flags

        decisions = {d["parameter"]: d for d in result["onboarding_decision"]["decisions"]}
        assert "Advance payment allowed?" in decisions
        assert decisions["Advance payment allowed?"]["recommendation"] == "No"

    @pytest.mark.anyio
    async def test_vendor_due_diligence_nclt_insolvency(self, sample_company_data, sample_part1_sourcing):
        mock_uow = MagicMock()
        mock_artifacts = MagicMock()
        vdd_service = VDDService(uow=mock_uow, artifact_service=mock_artifacts)

        sample_part1_sourcing["court_info"]["nclt_active_against"] = True
        vdd_service._ppre._get_company_data = AsyncMock(return_value=sample_company_data)
        vdd_service._ppre._run_part1_sourcing = MagicMock(return_value=sample_part1_sourcing)

        result = await vdd_service.vendor_due_diligence(
            entity_id="U74210MH1999PLC123010",
            requester_id="procurement-user-1",
        )

        assert result["onboarding_decision"]["status"] == "HOLD"
        flags = [f["issue"] for f in result["red_flag_register"]]
        assert "Active NCLT / CIRP insolvency petition" in flags

    @pytest.mark.anyio
    async def test_vendor_due_diligence_approved_entity(self, sample_company_data, sample_part1_sourcing):
        mock_uow = MagicMock()
        mock_artifacts = MagicMock()
        vdd_service = VDDService(uow=mock_uow, artifact_service=mock_artifacts)

        # Supply clean data with all mandatory documents resolved
        clean_company = dict(sample_company_data)
        clean_company["payload"] = dict(sample_company_data["payload"])
        clean_company["payload"]["ubo_declaration"] = True
        clean_company["payload"]["factory_consent"] = True
        clean_company["payload"]["directors_kyc_complete"] = True
        clean_company["payload"]["epfo_certificate"] = True
        clean_company["payload"]["shareholders"] = [{"name": "Promoter One", "percentage": 100}]

        clean_part1 = dict(sample_part1_sourcing)
        clean_part1["court_info"] = {
            "total_cases": 0,
            "active_cases": 0,
            "has_cheque_bounce_against": False,
            "nclt_active_against": False,
            "drt_cases": 0,
        }
        clean_part1["gst_filing_consistency_ratio"] = 1.0
        clean_part1["gst_turnover"] = 150000000.0

        vdd_service._ppre._get_company_data = AsyncMock(return_value=clean_company)
        vdd_service._ppre._run_part1_sourcing = MagicMock(return_value=clean_part1)

        result = await vdd_service.vendor_due_diligence(
            entity_id="U74210MH1999PLC123010",
            requester_id="procurement-user-1",
            trade_name="ABC Engineering Works",
            vendor_profile="services",
        )

        assert result["onboarding_decision"]["status"] == "APPROVED"
        assert result["overview"]["escalation_level"] == "Auto-approve"
        assert result["onboarding_decision"]["critical_flags_count"] == 0
        assert result["onboarding_decision"]["high_flags_count"] == 0

    @pytest.mark.anyio
    async def test_vendor_due_diligence_conditional_entity(self, sample_company_data, sample_part1_sourcing):
        mock_uow = MagicMock()
        mock_artifacts = MagicMock()
        vdd_service = VDDService(uow=mock_uow, artifact_service=mock_artifacts)

        # Low GSTR filing consistency triggers a High flag (without critical flag)
        sample_part1_sourcing["gst_filing_consistency_ratio"] = 0.65
        clean_company = dict(sample_company_data)
        clean_company["payload"] = dict(sample_company_data["payload"])
        clean_company["payload"]["ubo_declaration"] = True
        clean_company["payload"]["factory_consent"] = True
        clean_company["payload"]["directors_kyc_complete"] = True
        clean_company["payload"]["epfo_certificate"] = True
        clean_company["payload"]["shareholders"] = [{"name": "Promoter One", "percentage": 100}]

        vdd_service._ppre._get_company_data = AsyncMock(return_value=clean_company)
        vdd_service._ppre._run_part1_sourcing = MagicMock(return_value=sample_part1_sourcing)

        result = await vdd_service.vendor_due_diligence(
            entity_id="U74210MH1999PLC123010",
            requester_id="procurement-user-1",
            trade_name="ABC Engineering Works",
            vendor_profile="services",
        )

        assert result["onboarding_decision"]["status"] == "CONDITIONAL"
        assert result["overview"]["escalation_level"] == "Procurement Manager"


class TestVDDReportRendering:
    """Test dynamic HTML generation for S4 Vendor Due Diligence."""

    @pytest.mark.anyio
    async def test_render_s4_report_html(self, sample_company_data, sample_part1_sourcing):
        mock_uow = MagicMock()
        mock_artifacts = MagicMock()
        vdd_service = VDDService(uow=mock_uow, artifact_service=mock_artifacts)
        vdd_service._ppre._get_company_data = AsyncMock(return_value=sample_company_data)
        vdd_service._ppre._run_part1_sourcing = MagicMock(return_value=sample_part1_sourcing)

        result = await vdd_service.vendor_due_diligence(
            entity_id="U74210MH1999PLC123010",
            requester_id="procurement-user-1",
            trade_name="ABC Engineering Works",
            vendor_profile="manufacturing",
        )

        report_service = ReportService()
        html = report_service.render_s4_report(result)

        assert isinstance(html, str)
        assert "<!DOCTYPE html>" in html
        assert "ABC Engineering Works" in html
        assert "Vendor Due Diligence" in html
        assert "Entity Identity" in html
        assert "Compliance Intelligence" in html
        assert "Financial Quality" in html
        assert "MCA Charges" in html
        assert "Document Register" in html

    def test_render_s4_report_banners(self):
        report_service = ReportService()

        # APPROVED
        approved_data = {
            "overview": {"vendor_name": "Acme Corp", "report_id": "RPT-001", "report_date": "2026-11-21"},
            "onboarding_decision": {
                "status": "APPROVED",
                "verdict_headline": "Vendor Approved",
                "verdict_detail": "All clear",
            },
        }
        html_app = report_service.render_s4_report(approved_data)
        assert "banner green" in html_app
        assert "t-green" in html_app

        # CONDITIONAL
        conditional_data = {
            "overview": {"vendor_name": "Beta Corp", "report_id": "RPT-002", "report_date": "2026-11-21"},
            "onboarding_decision": {
                "status": "CONDITIONAL",
                "verdict_headline": "Conditional Onboarding",
                "verdict_detail": "Pending docs",
            },
        }
        html_cond = report_service.render_s4_report(conditional_data)
        assert "banner amber" in html_cond
        assert "t-amber" in html_cond

        # HOLD
        hold_data = {
            "overview": {"vendor_name": "Gamma Corp", "report_id": "RPT-003", "report_date": "2026-11-21"},
            "onboarding_decision": {
                "status": "HOLD",
                "verdict_headline": "Onboarding on Hold",
                "verdict_detail": "Critical issues",
            },
        }
        html_hold = report_service.render_s4_report(hold_data)
        assert 'class="banner "' in html_hold
        assert "t-red" in html_hold


class TestVDDFastAPIIntegration:
    """Test FastAPI JSON and Report endpoints."""

    def test_json_api_endpoint(self, sample_company_data, sample_part1_sourcing):
        client = TestClient(api)
        mock_vdd_service = MagicMock(spec=VDDService)

        # Build mock response
        mock_vdd_service.vendor_due_diligence = AsyncMock(
            return_value={
                "overview": {
                    "vendor_name": "ABC Engineering Works",
                    "report_id": "RPT-VDD-20261121-0001",
                    "report_date": "21 November 2026",
                    "suite_label": "Service 4 of 5 · Pralyon Intelligence Suite",
                    "vendor_profile": "Manufacturing Vendor",
                    "escalation_level": "Procurement Manager",
                    "distribution": "Internal Use Only",
                },
                "onboarding_decision": {
                    "status": "CONDITIONAL",
                    "verdict_headline": "Conditional Onboarding",
                    "verdict_detail": "High-risk items require resolution",
                    "critical_flags_count": 0,
                    "high_flags_count": 1,
                    "docs_missing_count": 1,
                    "legal_cases_count": 0,
                    "api_cost_inr": 0.0,
                    "decisions": [
                        {
                            "parameter": "Onboard vendor?",
                            "recommendation": "Conditional approval",
                            "reason": "High-risk items require management sign-off.",
                        }
                    ],
                },
                "entity_identity": {
                    "vendor_name": "ABC Engineering Works",
                    "business_type": "Manufacturing vendor",
                    "gstin_status": "Active",
                    "udyam_number": "UDYAM-MH-01-0012345",
                    "udyam_status": "Valid",
                    "authorised_signatory": "Ramesh Kumar",
                    "site_address_consistency": "Consistent",
                    "cin": "U74210MH1999PLC123010",
                    "pan": "AABCU9603R",
                    "incorporation_date": "1999-05-12",
                    "registered_state": "Maharashtra",
                },
                "red_flag_register": [],
                "aml_integrity": {},
                "ownership_governance": {"directors": []},
                "litigation_legal": [],
                "compliance_intelligence": [],
                "financial_quality": {},
                "mca_charges": {"charges": []},
                "operational_viability": [],
                "document_register": [],
                "data_sources_used": ["mca", "gst"],
                "assessed_at": "2026-11-21T00:00:00Z",
                "metadata": {
                    "entity_id": "U74210MH1999PLC123010",
                    "requester_id": "proc-1",
                },
            }
        )

        orig_inject_instance = inject.instance

        def mock_inject_instance(cls):
            if cls == VDDService:
                return mock_vdd_service
            return orig_inject_instance(cls)

        with patch("api.vendor_due_diligence.inject.instance", side_effect=mock_inject_instance):
            # Test with seller_id (primary field)
            response = client.post(
                "/api/v1/vendor-due-diligence",
                json={
                    "entity_id": "U74210MH1999PLC123010",
                    "seller_id": "seller-demo",
                    "trade_name": "ABC Engineering Works",
                    "vendor_profile": "manufacturing",
                },
            )
            assert response.status_code == 200
            data = response.json()
            assert data["response_code"] == 200
            assert "data" in data
            assert data["data"]["overview"]["vendor_name"] == "ABC Engineering Works"
            assert data["data"]["onboarding_decision"]["status"] == "CONDITIONAL"

            # Test backward compatibility with requester_id alias
            response_legacy = client.post(
                "/api/v1/vendor-due-diligence",
                json={
                    "entity_id": "U74210MH1999PLC123010",
                    "requester_id": "proc-1",
                },
            )
            assert response_legacy.status_code == 200

    def test_report_api_endpoint(self):
        client = TestClient(api)
        mock_vdd_service = MagicMock(spec=VDDService)
        mock_report_service = MagicMock(spec=ReportService)

        mock_vdd_service.vendor_due_diligence = AsyncMock(
            return_value={
                "overview": {"vendor_name": "ABC Engineering Works"},
                "onboarding_decision": {"status": "APPROVED"},
            }
        )
        mock_report_service.render_s4_report = MagicMock(
            return_value="<!DOCTYPE html><html><body><h1>ABC Engineering Works</h1></body></html>"
        )

        orig_inject_instance = inject.instance

        def mock_inject_instance(cls):
            if cls == VDDService:
                return mock_vdd_service
            if cls == ReportService:
                return mock_report_service
            return orig_inject_instance(cls)

        with patch("api.vendor_due_diligence.inject.instance", side_effect=mock_inject_instance):
            response = client.post(
                "/api/v1/vendor-due-diligence/report",
                json={
                    "entity_id": "U74210MH1999PLC123010",
                    "requester_id": "proc-1",
                },
            )
            assert response.status_code == 200
            assert "text/html" in response.headers["content-type"]
            assert "ABC Engineering Works" in response.text
