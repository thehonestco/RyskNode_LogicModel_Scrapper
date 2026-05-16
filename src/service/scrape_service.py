import asyncio
import datetime
import logging
import os
import re
from typing import List, Dict, Any, Optional

import inject

from common.base.error import ApplicationError
from common.base import constants
from common.service.base import BaseService
from common.service.unit_of_work import AbstractUnitOfWork
from repository.company_repository import CompanyRepository
from common.adapter.falcon_biz import FalconBizScraper
from common.adapter.tracxn import TracxnScraper
from domain.company import CompanyDomain

logger = logging.getLogger(__name__)

class ScrapeService(BaseService):
    def __init__(self, uow: AbstractUnitOfWork, falcon_scraper: FalconBizScraper, tracxn_scraper: TracxnScraper):
        super().__init__()
        self.uow = uow
        self.falcon_scraper = falcon_scraper
        self.tracxn_scraper = tracxn_scraper

    async def scrape_single(self, cin: str, uow: AbstractUnitOfWork = None) -> Optional[CompanyDomain]:
        """
        Scrape a single CIN and return a CompanyDomain object.
        Uses provided uow or creates a new one.
        """
        data = await self._perform_scrape(cin)
        
        if not data or not data.get("company_name"):
             raise ApplicationError(response_code=constants.HTTP_404_NOT_FOUND, message=f"Company with CIN {cin} not found.")

        # Use provided UOW or get a fresh one from inject
        effective_uow = uow or inject.instance(AbstractUnitOfWork)
        
        # If uow is provided, it's already in a context, so we don't 'async with' it here
        # unless it's the one we just created.
        if uow:
            return await self._save_data(data, uow)
        else:
            async with effective_uow as fresh_uow:
                return await self._save_data(data, fresh_uow)

    async def _save_data(self, data: Dict[str, Any], uow: AbstractUnitOfWork) -> CompanyDomain:
        repo = CompanyRepository(uow.session)
        existing = await repo.get_single(cin=data["cin"])
        if existing:
            await repo.update_by(data, {"cin": data["cin"]})
            result = await repo.get_single(cin=data["cin"])
        else:
            result = await repo.add(data)
        
        # Handle both model instance and dictionary
        if isinstance(result, dict):
            return CompanyDomain(**result)
        else:
            # SQLAlchemy model instance
            return CompanyDomain(**{c.name: getattr(result, c.name) for c in result.__table__.columns})

    async def _perform_scrape(self, cin: str) -> Dict[str, Any]:
        falcon_task = asyncio.create_task(self.falcon_scraper.scrape(cin))
        tracxn_task = asyncio.create_task(self.tracxn_scraper.scrape(cin))
        
        falcon_data, tracxn_data = await asyncio.gather(falcon_task, tracxn_task)
        
        if not falcon_data and not tracxn_data:
            return {}
            
        merged_data = self._merge_data(falcon_data, tracxn_data)
        if "cin" not in merged_data or not merged_data["cin"]:
            merged_data["cin"] = cin
            
        merged_data["scraped_at"] = datetime.datetime.now()
        
        raw_falcon = self._json_serializable(falcon_data)
        raw_tracxn = self._json_serializable(tracxn_data)
        merged_data["raw_data"] = {"falcon": raw_falcon, "tracxn": raw_tracxn}
        merged_data["data_source"] = "FalconBiz / Tracxn"
        
        if "company_name" in merged_data and merged_data["company_name"]:
            merged_data["company_name_normalized"] = merged_data["company_name"].lower()
            
        self._cleanup_data(merged_data)
        return merged_data

    def _cleanup_data(self, data: Dict):
        if data.get("description_of_main_activity"):
             desc = data["description_of_main_activity"]
             if "'s registered office address is" in desc:
                  data["description_of_main_activity"] = desc.split("'s registered office address is")[0].strip()
        
        if not data.get("description_of_business_activity"):
             data["description_of_business_activity"] = data.get("description_of_main_activity")
        
        if not data.get("business_activity_code"):
             data["business_activity_code"] = data.get("main_activity_group_code")

        if not data.get("latest_revenue_date") and data.get("raw_data", {}).get("tracxn", {}).get("latest_revenue_date"):
             data["latest_revenue_date"] = data["raw_data"]["tracxn"]["latest_revenue_date"]

    def _json_serializable(self, data: Dict) -> Dict:
        serializable = {}
        for k, v in data.items():
            if isinstance(v, (datetime.date, datetime.datetime)):
                serializable[k] = v.isoformat()
            else:
                serializable[k] = v
        return serializable

    def _merge_data(self, falcon: Dict, tracxn: Dict) -> Dict:
        merged = {}
        merged.update(falcon)
        for k, v in tracxn.items():
            if v is not None and v != "":
                if k in ["latest_revenue", "latest_revenue_date", "main_activity_group_code", "description_of_main_activity", "business_activity_code", "description_of_business_activity"]:
                     merged[k] = v
                elif k not in merged or not merged[k]:
                    merged[k] = v
        return merged

    async def batch_scrape_background(self, queries: List[str]):
        """
        Processes batch sequentially using a single connection to avoid overhead
        and match 'gc backend' pattern.
        """
        success_count = 0
        failed_count = 0
        failures = []

        # Use exactly ONE UOW (and thus ONE session/connection) for the entire batch
        async with inject.instance(AbstractUnitOfWork) as uow:
            for cin in queries:
                try:
                    await self.scrape_single(cin, uow=uow)
                    success_count += 1
                except Exception as e:
                    logger.error(f"Error in batch scrape for {cin}: {e}")
                    failed_count += 1
                    failures.append({"query": cin, "reason": str(e)})

        await self._generate_report(len(queries), success_count, failed_count, failures)

    async def _generate_report(self, total: int, success: int, failed: int, failures: List[Dict]):
        report_dir = os.path.join(os.getcwd(), "reports")
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)
            
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        report_file = os.path.join(report_dir, f"scrape_report_{timestamp}.md")
        
        report_content = f"""# Scrape Batch Report
Date: {datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")}

## Summary
- **Total Requests Processed:** {total}
- **Total Successful Scrapes:** {success}
- **Total Failed Scrapes:** {failed}

## Failure Details
"""
        if not failures:
            report_content += "No failures recorded.\n"
        else:
            report_content += "| Query | Reason |\n|---|---|\n"
            for f in failures:
                report_content += f"| {f['query']} | {f['reason']} |\n"
                
        try:
            with open(report_file, "w") as f:
                f.write(report_content)
            logger.info(f"Batch scrape report generated: {report_file}")
        except Exception as e:
            logger.error(f"Failed to generate report file: {e}")
