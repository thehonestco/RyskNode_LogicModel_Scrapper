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

    def render_s5_report(self, sector_report: Dict[str, Any]) -> str:
        """Render S5 Sector Intelligence report."""
        template = self.env.get_template("s5_sector_report.html")
        
        sec_risk = sector_report.get("sector_risk", "MODERATE")
        _RISK_COLORS = {
            "LOW":      ("#3fb950", "#0f2a1a", "#238636"),
            "MODERATE": ("#e3b341", "#2a1f0f", "#9e6a03"),
            "HIGH":     ("#f0883e", "#2a1a0f", "#bd561d"),
            "CRITICAL": ("#f85149", "#2a0f0f", "#f85149"),
        }
        risk_color, risk_bg, risk_border = _RISK_COLORS.get(sec_risk, _RISK_COLORS["MODERATE"])

        # Format comparison rows
        comparison = sector_report.get("comparison", {})
        _METRIC_LABELS = {
            "current_ratio":      "Current Ratio",
            "debt_to_equity":     "Debt / Equity",
            "dso":                "DSO (days)",
            "blended_pd":         "Blended PD",
            "el_pct":             "Expected Loss %",
            "governance_score":   "Governance Score",
            "evaluated_limit_cr": "Evaluated Limit (Cr)",
        }
        
        def _fmt_val(key: str, val: float) -> str:
            if key in ("blended_pd", "el_pct"):
                return f"{val*100:.2f}%"
            if key == "dso":
                return f"{val:.0f}d"
            if key == "evaluated_limit_cr":
                return f"₹\xa0{val:.2f}\xa0Cr"
            return f"{val:.2f}"

        comp_rows = []
        for key, label in _METRIC_LABELS.items():
            c = comparison.get(key, {})
            if not c:
                continue
            pct        = c.get("percentile", 50)
            buyer_val  = _fmt_val(key, c.get("buyer", 0))
            med_val    = _fmt_val(key, c.get("median", 0))
            delta      = c.get("delta", 0)
            better_if  = c.get("better_if", "HIGHER")
            
            bar_color  = "#3fb950" if pct >= 60 else ("#e3b341" if pct >= 35 else "#f85149")
            delta_sign = "+" if delta > 0 else ""
            delta_color = "#3fb950" if (better_if == "HIGHER" and delta >= 0) or (better_if == "LOWER" and delta <= 0) else "#f85149"
            
            delta_formatted = _fmt_val(key, abs(delta)) if key not in ('dso','current_ratio','debt_to_equity','governance_score','evaluated_limit_cr') else f'{delta_sign}{delta:.2f}'
            
            comp_rows.append(
                f"<tr>"
                f"<td>{label}</td>"
                f"<td style='color:#e6edf3;font-weight:600'>{buyer_val}</td>"
                f"<td style='color:#8b949e'>{med_val}</td>"
                f"<td style='color:{delta_color}'>{delta_sign}{delta_formatted}</td>"
                f"<td>"
                f"  <div style='display:flex;align-items:center;gap:8px'>"
                f"    <div style='flex:1;height:6px;background:#21262d;border-radius:3px;overflow:hidden'>"
                f"      <div style='width:{pct}%;height:100%;background:{bar_color};border-radius:3px'></div>"
                f"    </div>"
                f"    <span style='font-size:11px;color:#e6edf3;width:32px;text-align:right;font-weight:600'>{pct:.0f}</span>"
                f"  </div>"
                f"</td>"
                f"</tr>"
            )

        from datetime import datetime, timezone
        
        context = {
            "nic_code": sector_report.get("nic_code", ""),
            "sector_name": sector_report.get("sector_name", "Unknown Sector"),
            "sector_risk": sec_risk,
            "peer_count": sector_report.get("peer_count", 0),
            "overall_percentile": sector_report.get("overall_percentile", 50),
            "narrative": sector_report.get("narrative", ""),
            "benchmark": sector_report.get("benchmark", {}),
            "risk_color": risk_color,
            "risk_bg": risk_bg,
            "risk_border": risk_border,
            "gen_utc": datetime.now(timezone.utc).isoformat()[:19].replace("T", " "),
            "comp_rows_html": "\\n".join(comp_rows),
        }
        
        return template.render(**context)

    async def render_s5_report_pdf(self, sector_report: Dict[str, Any]) -> bytes:
        """Render S5 Sector Intelligence report as PDF."""
        html_content = self.render_s5_report(sector_report)
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
