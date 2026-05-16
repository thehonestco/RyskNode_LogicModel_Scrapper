import datetime
import logging
import re
from typing import Any, Dict, Optional

import aiohttp

from .scraper_base import BaseScraper

logger = logging.getLogger(__name__)

class TracxnScraper(BaseScraper):
    """
    Tracxn Scraper using search list parsing and enhanced regex for high reliability.
    """

    async def scrape(self, query: str) -> Dict[str, Any]:
        logger.info(f"Scraping Financials for: {query}")

        data = {}

        # 1. NIC Code from CIN (Highly reliable)
        if len(query) == 21:
            data["main_activity_group_code"] = query[1:6]

        # 2. Try Tracxn search snippets via aiohttp
        tracxn_data = await self._fetch_from_tracxn(query)
        data.update(tracxn_data)

        return data

    async def _fetch_from_tracxn(self, query: str) -> Dict[str, Any]:
        try:
            url = f"https://tracxn.com/search/legal-entities?q={query}"
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
                "Referer": "https://www.google.com/",
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=10) as resp:
                    if resp.status == 200:
                        content = await resp.text()
                        return self._parse_snippet(content)
        except Exception as e:
            logger.debug(f"Error fetching from Tracxn: {e}")
        return {}

    def _parse_snippet(self, text: str) -> Dict[str, Any]:
        data = {}

        # 1. STRICT PRIORITY: Look for "Latest Revenue" specifically
        latest_revenue_patterns = [
            # Pattern for the specific HTML format provided by user: 
            # <div>Latest Revenue</div><div><a>42Cr INR</a> as on Mar 31, 2023
            r"Latest\s+Revenue(?:[^>]*>){1,20}\s*([₹]?\s*[\d\.,]+\s*(?:Cr|cr|M|m|Lakh|L|K|Million|Billion|B))\s*(?:INR|USD)?(?:\s*</a>)?\s*(?:as\s+on|on)?\s*([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
            # Text based Latest Revenue
            r"Latest\s+Revenue.*?([₹]?\s*[\d\.,]+\s*(?:Cr|cr|M|m|Lakh|L|K|Million|Billion|B))\s*(?:INR|USD)?\s*(?:as\s+on|on)?\s*([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
            # Simple Latest Revenue without date
            r"Latest\s+Revenue.*?([₹]?\s*[\d\.,]+\s*(?:Cr|cr|M|m|Lakh|L|K|Million|Billion|B))",
        ]

        for pattern in latest_revenue_patterns:
            match = re.search(pattern, text, re.I | re.S)
            if match:
                raw_match = match.group(0).strip()
                data["revenue_text"] = self._strip_html(raw_match)
                data["latest_revenue"] = self._parse_revenue(match.group(1))
                
                # Extract date if captured in the pattern
                if match.lastindex >= 2:
                    data["latest_revenue_date"] = self._parse_date_flexible(match.group(2))
                
                # If we found LATEST revenue, we stop looking
                if data.get("latest_revenue"):
                    return data

        # 2. SECONDARY: Look for Operating Revenue or General Revenue if Latest wasn't found
        secondary_patterns = [
            # Operating Revenue Range (Upper bound prioritized)
            r"Operating\s+Revenue.*?([\d\.,]+\s*(?:Cr|cr|M|m|Lakh|L|K))\s*-\s*([\d\.,]+\s*(?:Cr|cr|M|m|Lakh|L|K))",
            # Simple Revenue value
            r"(?:Operating\s+)?(?:Revenue|Turnover|Finances).*?([₹]?\s*[\d\.,]+\s*(?:Cr|cr|M|m|Lakh|L|K|Million|Billion|B))",
        ]

        for pattern in secondary_patterns:
            match = re.search(pattern, text, re.I | re.S)
            if match:
                raw_match = match.group(0).strip()
                data["revenue_text"] = self._strip_html(raw_match)
                
                if "-" in pattern and match.lastindex >= 2:
                    val1 = self._parse_revenue(match.group(1))
                    val2 = self._parse_revenue(match.group(2))
                    data["latest_revenue"] = val2 or val1
                else:
                    data["latest_revenue"] = self._parse_revenue(match.group(1))
                break

        # 3. Final Date Fallback (Look near whatever revenue we found)
        if not data.get("latest_revenue_date") and data.get("latest_revenue"):
            start = max(0, text.lower().find("revenue") - 100)
            end = min(len(text), start + 800)
            context = text[start:end]

            date_match = re.search(r"(?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*(?:\d{1,2})?,?\s*(20\d{2})", context, re.I)
            if date_match:
                data["latest_revenue_date"] = self._parse_date_flexible(date_match.group(0))

        return data

    def _strip_html(self, text: str) -> str:
        if not text: return ""
        # Remove HTML tags
        clean = re.sub(r"<[^>]*>", " ", text)
        # Normalize whitespace
        return re.sub(r"\s+", " ", clean).strip()

    def _parse_date_flexible(self, date_str: str) -> Optional[datetime.date]:
        if not date_str: return None
        date_str = date_str.strip().replace(",", "")
        # Try common formats
        for fmt in ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue
        
        # Last resort: extract year and month
        month_match = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", date_str, re.I)
        year_match = re.search(r"(20\d{2})", date_str)
        if month_match and year_match:
             # Just use the last day of that month or assume March 31 if it's "Mar"
             year = int(year_match.group(1))
             return datetime.date(year, 3, 31)
             
        return None

    def _parse_revenue(self, rev_str: str) -> Optional[int]:
        if not rev_str: return None
        try:
            # Clean up formatting
            rev_str = rev_str.replace(",", "").replace(" ", "").replace("₹", "")
            rev_str = rev_str.replace("INR", "").replace("USD", "").upper()

            # Filter out years mistakenly caught as numbers
            if rev_str in ["2026", "2025", "2024", "2023", "2022"]: return None

            # Split number and unit
            match = re.search(r"([\d\.]+)\s*(CR|M|LAKH|L|K|BILLION|MILLION|B)?", rev_str)
            if not match: return None

            num_str = match.group(1)
            unit = match.group(2)

            try:
                num = float(num_str)
            except ValueError:
                return None

            if not unit:
                return int(num)

            if unit == "CR": num *= 10_000_000
            elif unit in ["MILLION", "M"]: num *= 1_000_000
            elif unit in ["LAKH", "L"]: num *= 100_000
            elif unit == "K": num *= 1_000
            elif unit in ["BILLION", "B"]: num *= 1_000_000_000

            return int(num)
        except: return None
