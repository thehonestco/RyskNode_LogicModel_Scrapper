import logging
from pathlib import Path
from typing import Any, Dict
from jinja2 import Environment, FileSystemLoader

logger = logging.getLogger(__name__)

class ReportService:
    def __init__(self, templates_dir: str = "templates"):
        self.templates_dir = Path(templates_dir)
        self.env = Environment(loader=FileSystemLoader(str(self.templates_dir)))

    def render_s1_report(self, assess_data: Dict[str, Any]) -> str:
        """Render S1 Buyer Risk Assessment report."""
        template = self.env.get_template("s1_risk_report.html")
        
        # Extract variables
        metadata = assess_data.get("metadata", {})
        risk_band = assess_data.get("risk_band")
        blended_pd = assess_data.get("blended_pd", 0.05)
        blended_pd_pct = round(blended_pd * 100, 2)
        declined = risk_band in ("D", "UNSCOREABLE")
        
        context = {
            "metadata": metadata,
            "gstin": metadata.get("gstin") or assess_data.get("gstin"),
            "pan": metadata.get("pan") or assess_data.get("pan"),
            "risk_band": risk_band,
            "blended_pd_pct": blended_pd_pct,
            "declined": declined,
            "xai_narrative": assess_data.get("xai_narrative") or "",
            "financial_score": assess_data.get("financial_score", 0.0),
            "identity_score": assess_data.get("identity_score", 0.0),
            "legal_score": assess_data.get("legal_score", 0.0),
            "conduct_score": assess_data.get("conduct_score", 0.0),
        }
        
        return template.render(**context)

    def render_s2_report(self, credit_data: Dict[str, Any], requested_amount: float = 0.0) -> str:
        """Render S2 Credit Limit Assessment report."""
        template = self.env.get_template("s2_credit_report.html")
        
        # Extract variables
        metadata = credit_data.get("metadata", {})
        risk_band = credit_data.get("risk_band")
        blended_pd = credit_data.get("blended_pd", 0.05)
        blended_pd_pct = round(blended_pd * 100, 2)
        declined = risk_band in ("D", "UNSCOREABLE")
        
        # Limit advisor details from _ppre_output or root level
        ppre_out = credit_data.get("_ppre_output") or credit_data
        recommended_limit = ppre_out.get("evaluated_limit") or ppre_out.get("evaluated_clean_limit") or 0.0
        max_tenor_days = ppre_out.get("recommended_tenor") or ppre_out.get("recommended_tenor_days") or 30
        decline_reason = credit_data.get("decline_reason") or ppre_out.get("decline_reason") or "ZeroPass trigger or high risk"
        
        stress_table = ppre_out.get("stress_table") or []
        tenor_schedule = ppre_out.get("tenor_schedule") or []
        
        context = {
            "metadata": metadata,
            "gstin": metadata.get("gstin") or credit_data.get("gstin"),
            "pan": metadata.get("pan") or credit_data.get("pan"),
            "risk_band": risk_band,
            "blended_pd_pct": blended_pd_pct,
            "declined": declined,
            "decline_reason": decline_reason,
            "recommended_limit": recommended_limit,
            "max_tenor_days": max_tenor_days,
            "xai_narrative": credit_data.get("xai_narrative") or "",
            "financial_score": credit_data.get("financial_score", 0.0),
            "identity_score": credit_data.get("identity_score", 0.0),
            "legal_score": credit_data.get("legal_score", 0.0),
            "conduct_score": credit_data.get("conduct_score", 0.0),
            "stress_table": stress_table,
            "tenor_schedule": tenor_schedule,
            "requested_amount": requested_amount or ppre_out.get("requested_amount") or 0.0,
            "pralyon_score": credit_data.get("pralyon_score", 300),
        }
        
        return template.render(**context)

    async def render_s1_report_pdf(self, assess_data: Dict[str, Any]) -> bytes:
        """Render S1 Buyer Risk Assessment report as PDF."""
        html_content = self.render_s1_report(assess_data)
        return await self._html_to_pdf(html_content)

    async def render_s2_report_pdf(self, credit_data: Dict[str, Any], requested_amount: float = 0.0) -> bytes:
        """Render S2 Credit Limit Assessment report as PDF."""
        html_content = self.render_s2_report(credit_data, requested_amount)
        return await self._html_to_pdf(html_content)

    async def _html_to_pdf(self, html_content: str) -> bytes:
        """Internal helper to render HTML to PDF bytes using Playwright."""
        from playwright.async_api import async_playwright
        async with async_playwright() as p:
            browser = await p.chromium.launch(headless=True)
            page = await browser.new_page()
            await page.set_content(html_content)
            await page.wait_for_load_state("networkidle")
            pdf_bytes = await page.pdf(
                print_background=True,
                format="A4",
                margin={"top": "0mm", "bottom": "0mm", "left": "0mm", "right": "0mm"}
            )
            await browser.close()
            return pdf_bytes
