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
        logger.info(f"Scraping Tracxn for: {query}")

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
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://tracxn.com/",
                "Connection": "keep-alive"
            }
            # Use a shorter timeout to prevent "infinite" hanging
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=8) as resp:
                    if resp.status == 200:
                        content = await resp.text()
                        return self._parse_snippet(content)
        except Exception as e:
            logger.debug(f"Error fetching from Tracxn: {e}")
        return {}

    def _parse_snippet(self, text: str) -> Dict[str, Any]:
        data = {}

        # Enhanced flexible Revenue patterns including "Latest Revenue" and "Operating Revenue"
        patterns = [
            # Pattern 1: Range (e.g., "Operating Revenue: 1 Cr - 100 Cr")
            r"(?:Latest\s+)?(?:Operating\s+)?(?:Revenue|Turnover|Finances).*?([\d\.,]+\s*(?:Cr|M|Lakh|L|K|Million|Billion|B))\s*-\s*([\d\.,]+\s*(?:Cr|M|Lakh|L|K|Million|Billion|B))",
            # Pattern 2: Revenue followed by value (e.g., "Latest Revenue: ₹ 42.0 Cr" or "Over 500 Cr")
            r"(?:Latest\s+)?(?:Operating\s+)?(?:Revenue|Turnover|Finances).*?([\d\.,]+\s*(?:Cr|M|Lakh|L|K|Million|Billion|B))",
            # Pattern 3: Value followed by Revenue (e.g., "100 Cr Revenue")
            r"([\d\.,]+\s*(?:Cr|M|Lakh|L|K|Million|Billion|B)).*?(?:Latest\s+)?(?:Operating\s+)?(?:Revenue|Turnover|Finances)"
        ]

        for pattern in patterns:
            match = re.search(pattern, text, re.I | re.S)
            if match:
                # Handle range match (groups 1 and 2)
                if "-" in pattern:
                    val1 = self._parse_revenue(match.group(1))
                    val2 = self._parse_revenue(match.group(2))
                    data["latest_revenue"] = val2 or val1
                else:
                    data["latest_revenue"] = self._parse_revenue(match.group(1))

                if data.get("latest_revenue"):
                    # Extract date from surrounding context
                    start = max(0, match.start()-150)
                    end = min(len(text), match.end()+150)
                    context = text[start:end]

                    # Look for specific date like "31 March 2023" or "March 31, 2023"
                    date_match = re.search(r"(?:\d{1,2}\s+)?(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)[a-z]*\s*(?:\d{1,2})?,?\s*(20\d{2})", context, re.I)
                    if date_match:
                        year = date_match.group(1)
                        data["latest_revenue_date"] = datetime.date(int(year), 3, 31)
                    else:
                        # Fallback to FY/Year
                        year_match = re.search(r"(?:FY|Year|ending\s+on)\s*(?:20)?(\d{2,4})", context, re.I)
                        if year_match:
                            year = year_match.group(1)
                            if len(year) == 2: year = "20" + year
                            data["latest_revenue_date"] = datetime.date(int(year), 3, 31)
                    break

        return data

    def _parse_revenue(self, rev_str: str) -> Optional[int]:
        if not rev_str: return None
        try:
            # Clean up formatting
            rev_str = rev_str.replace(",", "").replace(" ", "").upper()

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
