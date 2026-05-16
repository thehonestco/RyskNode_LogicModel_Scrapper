import datetime
import json
import logging
import re
from typing import Any, Dict, Optional

import aiohttp
from lxml import html

from .scraper_base import BaseScraper

logger = logging.getLogger(__name__)

class FalconBizScraper(BaseScraper):
    BASE_URL = "https://www.falconebiz.com/company"

    async def scrape(self, query: str) -> Dict[str, Any]:
        logger.info(f"Scraping Falcon Biz for: {query}")
        url = f"{self.BASE_URL}/{query}"

        try:
            headers = {
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
            }
            async with aiohttp.ClientSession() as session:
                async with session.get(url, headers=headers, timeout=30) as response:
                    if response.status != 200:
                        logger.error(f"Falcon Biz returned status {response.status} for {query}")
                        return {}

                    content = await response.text()
                    tree = html.fromstring(content)

                    data = {}

                    # 1. Try to get high-quality data from JSON-LD
                    try:
                        json_ld_script = tree.xpath("//script[@type='application/ld+json']/text()")
                        if json_ld_script:
                            ld_data = json.loads(json_ld_script[0])
                            graph = ld_data.get("@graph", [])
                            org = next((item for item in graph if item.get("@type") == "Organization"), {})

                            if org:
                                data["company_name"] = org.get("legalName") or org.get("name")
                                data["cin"] = org.get("identifier", {}).get("value")
                                data["address"] = org.get("address", {}).get("streetAddress")
                                data["incorporation_date"] = self._parse_date(org.get("foundingDate"))
                                if "founders" in org:
                                    data["director_names"] = ", ".join([f.get("name") for f in org["founders"] if f.get("name")])
                                # Description usually contains activity
                                desc = org.get("description", "")
                                if "involved in activities such as" in desc:
                                    data["description_of_main_activity"] = desc.split("involved in activities such as")[-1].split(".")[0].strip()
                    except Exception as e:
                        logger.warning(f"Error parsing JSON-LD for {query}: {e}")

                    # 2. Extract from tables
                    info_table = tree.xpath("//div[contains(.//h3, 'COMPANY INFORMATION')]//table//tr")
                    info_map = {}
                    for row in info_table:
                        cols = row.xpath("./td")
                        if len(cols) == 2:
                            key = "".join(cols[0].xpath(".//text()")).strip()
                            val = "".join(cols[1].xpath(".//text()")).strip()
                            info_map[key] = val

                    # Fallback/Supplemental fields
                    data["company_name"] = data.get("company_name") or self._get_text(tree, "//h1//text()").split("/")[0].strip()
                    data["cin"] = data.get("cin") or info_map.get("CIN") or query
                    data["registration_number"] = info_map.get("Registration Number")
                    data["current_status"] = info_map.get("Company Status")
                    data["incorporation_date"] = data.get("incorporation_date") or self._parse_date(info_map.get("Date of Incorporation"))
                    data["authorized_capital"] = self._parse_int(info_map.get("Authorized Capital"))
                    data["paid_up_capital"] = self._parse_int(info_map.get("Paid-up capital"))
                    data["entity_type"] = info_map.get("Class of company")
                    data["registrar_of_companies"] = info_map.get("RoC")
                    data["company_age"] = info_map.get("Company Age")
                    data["last_agm_date"] = self._parse_date(info_map.get("Date of Last Annual General Meeting"))
                    data["latest_balance_sheet_date"] = self._parse_date(info_map.get("Date of Latest Balance Sheet"))

                    if not data.get("description_of_main_activity"):
                        data["description_of_main_activity"] = info_map.get("Activity")

                    # 3. Extract Directors
                    director_rows = tree.xpath("//div[@id='directors']//table//tbody/tr")
                    d_names, d_dins, d_dates = [], [], []
                    for row in director_rows:
                        din = "".join(row.xpath("./td[1]//text()")).strip()
                        name = "".join(row.xpath("./td[2]//text()")).strip()
                        app_date = "".join(row.xpath("./td[4]//text()")).strip()
                        if name: d_names.append(name)
                        if din: d_dins.append(din)
                        if app_date: d_dates.append(app_date)

                    if d_names:
                        data["director_names"] = ", ".join(d_names)
                        data["director_din"] = ", ".join(d_dins)
                        data["director_appointment_date"] = self._parse_date(d_dates[0] if d_dates else None)

                    # 4. Address Fallback
                    if not data.get("address"):
                        addr = self._get_text(tree, "//td[strong[contains(text(), 'Address')]]/following-sibling::td//text()")
                        if addr: data["address"] = addr

                    return data
        except Exception as e:
            logger.exception(f"Error scraping Falcon Biz for {query}: {e}")
            return {}

    def _get_text(self, tree, xpath):
        results = tree.xpath(xpath)
        if not results: return None
        return " ".join([r.strip() for r in results if r.strip()]).strip()

    def _parse_date(self, date_str: Optional[str]) -> Optional[datetime.date]:
        if not date_str or not date_str.strip(): return None
        date_str = date_str.strip()
        date_str = re.sub(r"(\d+)(st|nd|rd|th)", r"\1", date_str)
        for fmt in ("%Y-%m-%d", "%d %B %Y", "%d %b %Y", "%d/%m/%Y", "%d %b, %Y"):
            try: return datetime.datetime.strptime(date_str, fmt).date()
            except ValueError: continue
        date_str = date_str.replace(",", "")
        for fmt in ("%d %B %Y", "%d %b %Y"):
            try: return datetime.datetime.strptime(date_str, fmt).date()
            except ValueError: continue
        return None

    def _parse_int(self, int_str: Optional[str]) -> Optional[int]:
        if not int_str: return None
        try:
            clean_str = "".join(filter(str.isdigit, int_str))
            return int(clean_str) if clean_str else None
        except: return None
