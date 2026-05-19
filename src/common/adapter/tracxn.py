import datetime
import logging
import re
from typing import Any, Dict, Optional

from playwright.async_api import async_playwright
from playwright_stealth import Stealth

from .scraper_base import BaseScraper

logger = logging.getLogger(__name__)


class TracxnScraper(BaseScraper):
    """
    Tracxn Scraper using search list parsing and enhanced regex for high reliability.
    Uses Playwright exclusively to bypass bot protection.
    """

    async def scrape(self, query: str) -> Dict[str, Any]:
        logger.info(f"Scraping Financials for: {query} using Playwright")

        data = {}

        # 1. NIC Code from CIN (Highly reliable)
        if len(query) == 21:
            data["main_activity_group_code"] = query[1:6]

        # 2. Fetch from Tracxn using Playwright
        tracxn_data = await self._fetch_from_tracxn(query)

        data.update(tracxn_data)
        return data

    async def _fetch_from_tracxn(self, query: str) -> Dict[str, Any]:
        """
        Launches a headless browser to solve Tracxn's bot challenge and render the page.
        """
        try:
            async with async_playwright() as p:
                # Launch chromium with no-sandbox for linux environments
                browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])

                # Use a realistic context with a modern User-Agent
                context = await browser.new_context(
                    user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
                    viewport={"width": 1280, "height": 720}
                )
                page = await context.new_page()

                # Apply stealth plugin to bypass automation detection
                await Stealth().apply_stealth_async(page)

                # Block unnecessary resources to massively speed up page loads
                async def block_resources(route):
                    if route.request.resource_type in ["image", "stylesheet", "media", "font"]:
                        await route.abort()
                    else:
                        await route.continue_()
                await page.route("**/*", block_resources)

                # Navigate to the base search page (using domcontentloaded for speed)
                logger.info("Navigating to Tracxn Search via Playwright Stealth")
                await page.goto("https://tracxn.com/search/legal-entities", wait_until="domcontentloaded", timeout=30000)

                # Type the query and search
                logger.info(f"Typing query: {query}")
                await page.fill("input[name='search']", "")
                await page.fill("input[name='search']", query)
                await page.keyboard.press("Enter")

                # Wait for results
                try:
                    await page.wait_for_selector("a[href*='/d/legal-entities/india/'], img[alt='no-result-found']", timeout=10000)
                except Exception:
                    logger.debug("Timeout waiting for search results to load.")

                content = await page.content()

                # Check if we are on a search result page with a link to the profile
                match = re.search(r'href="(/d/legal-entities/india/[^"]+)"', content)
                if match:
                    profile_url = f"https://tracxn.com{match.group(1)}"
                    logger.info(f"Found profile URL from search results: {profile_url}. Navigating...")
                    await page.goto(profile_url, wait_until="domcontentloaded", timeout=30000)

                    # Wait for specific data instead of arbitrary sleep
                    try:
                        await page.wait_for_selector("text='Latest Revenue'", timeout=2000)
                    except Exception:
                        await page.wait_for_timeout(1000)

                    content = await page.content()
                else:
                    logger.info("No profile URL found in search results, checking if current page has data.")

                await browser.close()

                logger.info(f"Successfully fetched {len(content)} bytes via Playwright")
                return self._parse_snippet(content)
        except Exception as e:
            logger.error(f"Playwright fetch failed: {e}")
        return {}

    def _parse_snippet(self, text: str) -> Dict[str, Any]:
        data = {}

        # STRICT PRIORITY: Only look for "Latest Revenue"
        # Using [\s\S]*? to handle line breaks inside HTML elements/tags
        latest_revenue_patterns = [
            # 1. HTML Specific structure (handles the <a> tag and multiline breaks)
            r"Latest\s+Revenue(?:\s*<[^>]*>)*\s*([\s\S]*?)(?:INR|USD)?(?:\s*<[^>]*>)*\s*(?:as\s+on|on)?\s*([A-Z][a-z]+\s+\d{1,2},?\s+\d{4})",
            # 2. General text pattern fallback
            r"Latest\s+Revenue[\s\S]*?([₹]?\s*[\d\.,]+\s*(?:Cr|cr|M|m|Lakh|lakh|L|K|k|Million|Billion|B))",
        ]

        for pattern in latest_revenue_patterns:
            match = re.search(pattern, text, re.I | re.S)
            if match:
                # Extract numerical value (clean capture group 1)
                clean_val = self._strip_html(match.group(1))

                # Format exactly as requested (e.g., "INR 42Cr")
                val_match = re.search(r"([\d\.]+)\s*(Cr|cr|M|m|Lakh|lakh|L|K|k|Million|Billion|B)?", clean_val, re.I)
                if val_match:
                    num = val_match.group(1)
                    unit = (val_match.group(2) or "").capitalize()
                    data["latest_revenue"] = f"INR {num}{unit}".strip()

                # Extract date from second group if it exists
                if match.lastindex >= 2 and match.group(2):
                    data["latest_revenue_date"] = self._parse_date_flexible(self._strip_html(match.group(2)))

                # If we found a valid number, we successfully extracted Latest Revenue
                if data.get("latest_revenue"):
                    return data

        return data

    def _strip_html(self, text: str) -> str:
        if not text:
            return ""
        # Remove all HTML tags
        clean = re.sub(r"<[^>]*>", " ", text)
        # Normalize whitespace (newlines/tabs -> single space)
        return re.sub(r"\s+", " ", clean).strip()

    def _parse_date_flexible(self, date_str: str) -> Optional[datetime.date]:
        if not date_str:
            return None
        date_str = date_str.strip().replace(",", "")
        # Try common date formats
        for fmt in ("%b %d %Y", "%B %d %Y", "%d %b %Y", "%d %B %Y", "%Y-%m-%d"):
            try:
                return datetime.datetime.strptime(date_str, fmt).date()
            except ValueError:
                continue

        # Last resort fallback: extract year and month
        month_match = re.search(r"(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)", date_str, re.I)
        year_match = re.search(r"(20\d{2})", date_str)
        if month_match and year_match:
            year = int(year_match.group(1))
            # Default to end of financial year month (March)
            return datetime.date(year, 3, 31)

        return None

