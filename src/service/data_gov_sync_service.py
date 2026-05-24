import asyncio
import datetime
import logging
import os
import threading
from typing import Any, Dict, List, Optional

import aiohttp
import inject

from common.base import constants
from common.base.error import ApplicationError
from common.service.base import BaseService
from common.service.unit_of_work import AbstractUnitOfWork
from repository.company_repository import CompanyRepository
from settings import Settings

logger = logging.getLogger(__name__)


class DataGovSyncService(BaseService):
    def __init__(self, uow: AbstractUnitOfWork, settings: Settings):
        super().__init__()
        self.uow = uow
        self.settings = settings
        self._thread_lock = threading.Lock()
        self._stop_requested = False

    async def sync_state(
        self,
        state: Optional[str] = None,
        api_key: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit_per_page: Optional[int] = None,
        max_records: Optional[int] = None,
        uow: Optional[AbstractUnitOfWork] = None,
        offset: Optional[int] = None,
        resume_only_on_interruption: bool = False,
    ) -> Dict[str, Any]:
        """
        Synchronize ROC Company Master Data from data.gov.in for a state or all states.
        Paginates using the limit (up to 1000) and saves data into the local DB.
        Generates a state-specific or global markdown report in the reports directory.
        """
        acquired = self._thread_lock.acquire(blocking=False)
        if not acquired:
            logger.warning("Another synchronization task is already in progress.")
            raise ApplicationError(
                response_code=constants.HTTP_409_CONFLICT,
                message="Another synchronization task is already in progress.",
            )

        # Compute effective rate limit cooldown duration solely from settings
        cooldown_duration = getattr(self.settings, "data_gov_rate_limit_cooldown", 120)

        # Resolve effective limit per page from settings if not explicitly provided
        effective_limit = limit_per_page
        if effective_limit is None:
            effective_limit = getattr(self.settings, "data_gov_api_limit", 10)

        # Reset stop flag on entry
        self._stop_requested = False

        try:
            state_clean = state.strip() if state else None
            state_key = state_clean or "All States"
            logger.info(f"Starting synchronization for state: {state_key}")

            # Auto-resume from latest markdown report if starting offset not explicitly provided
            start_offset = offset
            if start_offset is None:
                start_offset = self._load_last_offset_from_reports(
                    state_key,
                    resume_only_on_interruption=resume_only_on_interruption
                )

            offset = start_offset

            effective_api_key = api_key or self.settings.data_gov_api_key
            if not effective_api_key:
                raise ApplicationError(
                    response_code=500,
                    message="Data.gov.in API key is not configured. Please supply an API key in the request or .env file.",
                )

            effective_resource_id = resource_id or self.settings.data_gov_resource_id
            base_url = self.settings.data_gov_base_url
            url = f"{base_url}/{effective_resource_id}"

            # Initialize counters/stats
            stats = {
                "state": state_clean or "All States",
                "total_fetched": start_offset,
                "added": 0,
                "updated": 0,
                "failed_records": [],
                "start_time": datetime.datetime.now(),
                "end_time": None,
                "pages_processed": start_offset // effective_limit if effective_limit > 0 else 0,
                "status": "Running",
                "last_offset": start_offset,
            }

            limit = min(effective_limit, 1000)

            # aiohttp session for querying data.gov.in
            async with aiohttp.ClientSession() as session:
                while True:
                    if self._stop_requested:
                        logger.info(f"Synchronization task stopped by user at offset {offset}")
                        stats["status"] = "Stopped"
                        break

                    # Stop if we hit user-specified max_records
                    if max_records and (stats["total_fetched"] - start_offset) >= max_records:
                        logger.info(f"Reached configured limit of {max_records} records for state {state_clean or 'All States'}.")
                        break

                    params = {
                        "api-key": effective_api_key,
                        "format": "json",
                        "offset": offset,
                        "limit": limit,
                    }
                    if state_clean:
                        params["filters[CompanyStateCode]"] = state_clean.lower()

                    headers = {
                        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
                        "Accept": "application/json",
                    }

                    logger.info(f"Fetching page (offset={offset}, limit={limit}) for state: {state_clean or 'All States'}")
                    retry_delay = 5
                    res_json = None

                    try:
                        while True:
                            if self._stop_requested:
                                break
                            try:
                                async with session.get(url, params=params, headers=headers, timeout=90) as resp:
                                    if resp.status == 200:
                                        res_json = await resp.json()
                                        break

                                    err_body = await resp.text()
                                    if resp.status == 429:
                                        logger.warning(
                                            f"Rate limit exceeded (HTTP 429) on data.gov.in API. "
                                            f"Pausing for {cooldown_duration} seconds before retrying..."
                                        )
                                        await self._interruptible_sleep(cooldown_duration)
                                        continue

                                    # Exponential backoff capped at 60s for 5xx/other errors
                                    logger.warning(
                                        f"API returned status {resp.status} for state {state_clean or 'All States'}. "
                                        f"Details: {err_body}. Retrying in {retry_delay}s..."
                                    )
                                    await self._interruptible_sleep(retry_delay)
                                    retry_delay = min(retry_delay * 2, 60)
                                    continue
                            except Exception as e:
                                if self._stop_requested:
                                    break
                                logger.warning(
                                    f"Fetch attempt failed for state {state_clean or 'All States'}: {e}. "
                                    f"Retrying in {retry_delay}s..."
                                )
                                await self._interruptible_sleep(retry_delay)
                                retry_delay = min(retry_delay * 2, 60)
                                continue
                    except Exception as e:
                        logger.error(f"Error fetching data from API for state {state_clean or 'All States'} at offset {offset}: {e}")
                        stats["status"] = "Failed"
                        stats["error"] = str(e)
                        break

                    if self._stop_requested:
                        stats["status"] = "Stopped"
                        break

                    if not res_json or res_json.get("status") != "ok":
                        logger.warning(
                            f"Unexpected response status or empty body: {res_json.get('status') if res_json else 'None'}"
                        )
                        break

                    records = res_json.get("records", [])
                    if not records:
                        logger.info(f"No more records found for state: {state_clean or 'All States'}. Synchronization completed.")
                        break

                    stats["pages_processed"] += 1
                    stats["total_fetched"] += len(records)

                    # Process page records and save to database
                    # Each page is saved under its own transaction to ensure progress is saved
                    page_uow = uow or inject.instance(AbstractUnitOfWork)
                    try:
                        async with page_uow as active_uow:
                            repo = CompanyRepository(active_uow.session)
                            for record in records:
                                cin = record.get("CIN")
                                if not cin:
                                    continue

                                # Parse AuthorizedCapital safely
                                auth_cap = None
                                raw_auth_cap = record.get("AuthorizedCapital")
                                if raw_auth_cap:
                                    try:
                                        auth_cap = int(float(str(raw_auth_cap).strip()))
                                    except Exception:
                                        pass

                                # Parse PaidupCapital safely
                                paid_cap = None
                                raw_paid_cap = record.get("PaidupCapital")
                                if raw_paid_cap:
                                    try:
                                        paid_cap = int(float(str(raw_paid_cap).strip()))
                                    except Exception:
                                        pass

                                # Parse CompanyRegistrationdate_date safely
                                inc_date = None
                                raw_date = record.get("CompanyRegistrationdate_date")
                                if raw_date:
                                    try:
                                        # Formats like YYYY-MM-DD
                                        inc_date = datetime.date.fromisoformat(str(raw_date).strip())
                                    except Exception:
                                        pass

                                company_name = record.get("CompanyName")
                                mapped_data = {
                                    "company_name": company_name or "UNKNOWN",
                                    "company_name_normalized": company_name.lower() if company_name else None,
                                    "cin": cin.strip(),
                                    "entity_type": record.get("CompanyClass"),
                                    "current_status": record.get("CompanyStatus"),
                                    "incorporation_date": inc_date,
                                    "registrar_of_companies": record.get("CompanyROCcode"),
                                    "address": record.get("Registered_Office_Address"),
                                    "authorized_capital": auth_cap,
                                    "paid_up_capital": paid_cap,
                                    "business_activity_code": record.get("nic_code"),
                                    "main_activity_group_code": record.get("nic_code"),
                                    "description_of_main_activity": record.get("CompanyIndustrialClassification"),
                                    "description_of_business_activity": record.get("CompanyIndustrialClassification"),
                                    "data_source": "data.gov.in API",
                                    "scraped_at": datetime.datetime.now(),
                                    "raw_data": record,
                                    "is_active": True,
                                }

                                try:
                                    existing = await repo.get_single(cin=cin.strip())
                                    if existing:
                                        # Filter out None/Empty values to avoid overwriting existing valid DB data
                                        update_data = {k: v for k, v in mapped_data.items() if v is not None and v != ""}
                                        await repo.update_by(update_data, {"cin": cin.strip()})
                                        stats["updated"] += 1
                                    else:
                                        await repo.add(mapped_data)
                                        stats["added"] += 1
                                except Exception as save_err:
                                    logger.error(f"Failed to save record with CIN {cin}: {save_err}")
                                    stats["failed_records"].append({
                                        "cin": cin,
                                        "name": company_name or "N/A",
                                        "reason": str(save_err),
                                    })
                    except Exception as db_err:
                        logger.error(f"Database transaction error for state {state_clean or 'All States'} page: {db_err}")
                        stats["status"] = "Failed"
                        stats["error"] = f"DB Transaction error: {db_err}"
                        break

                    # Increment offset for pagination
                    offset += len(records)
                    stats["last_offset"] = offset

                    # Short sleep to prevent heavy load on API / DB
                    await asyncio.sleep(0.1)

            stats["end_time"] = datetime.datetime.now()
            if stats["status"] == "Running":
                stats["status"] = "Completed"

            # Generate markdown report
            await self._generate_state_report(stats)

            return stats
        finally:
            self._thread_lock.release()

    async def sync_multiple_states(
        self,
        states: List[str],
        api_key: Optional[str] = None,
        resource_id: Optional[str] = None,
        limit_per_page: int = 1000,
        max_records_per_state: Optional[int] = None,
        concurrency: int = 2,
        rate_limit_cooldown: Optional[int] = None,
    ) -> Dict[str, Any]:
        """
        Run synchronization across multiple states concurrently, limited by the concurrency factor.
        """
        semaphore = asyncio.Semaphore(concurrency)
        results = {}

        async def worker(state_name: str):
            async with semaphore:
                try:
                    result = await self.sync_state(
                        state=state_name,
                        api_key=api_key,
                        resource_id=resource_id,
                        limit_per_page=limit_per_page,
                        max_records=max_records_per_state,
                        rate_limit_cooldown=rate_limit_cooldown,
                    )
                    results[state_name] = result
                except Exception as e:
                    logger.error(f"Fatal failure in sync worker for state {state_name}: {e}")
                    results[state_name] = {
                        "state": state_name,
                        "status": "Failed",
                        "error": str(e),
                        "total_fetched": 0,
                        "added": 0,
                        "updated": 0,
                        "failed_records": [],
                        "start_time": datetime.datetime.now(),
                        "end_time": datetime.datetime.now(),
                    }

        await asyncio.gather(*(worker(s) for s in states))
        return results

    async def _generate_state_report(self, stats: Dict[str, Any]):
        """
        Generates a statewise markdown report.
        """
        report_dir = os.path.join(os.getcwd(), "reports")
        if not os.path.exists(report_dir):
            os.makedirs(report_dir)

        state_clean = "".join([c if c.isalnum() else "_" for c in stats["state"]])
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        if stats["state"] == "All States":
            filename = f"global_sync_{timestamp}.md"
        else:
            filename = f"state_sync_{state_clean}_{timestamp}.md"
        report_path = os.path.join(report_dir, filename)

        duration = stats["end_time"] - stats["start_time"]
        success_rate = 100.0
        total_processed = stats["added"] + stats["updated"] + len(stats["failed_records"])
        if total_processed > 0:
            success_rate = ((stats["added"] + stats["updated"]) / total_processed) * 100.0

        report_content = f"""# data.gov.in State Sync Report: {stats["state"]}

## Sync Execution Summary
- **Target State / Code:** {stats["state"]}
- **Status:** {stats["status"]}
- **Start Time:** {stats["start_time"].strftime("%Y-%m-%d %H:%M:%S")}
- **End Time:** {stats["end_time"].strftime("%Y-%m-%d %H:%M:%S")}
- **Duration:** {duration.total_seconds():.2f} seconds
- **Total Records Fetched:** {stats["total_fetched"]}
- **Pages Processed:** {stats["pages_processed"]}
- **Last Processed Offset:** {stats.get("last_offset", 0)}

## Database Insertion Statistics
- **Newly Added Companies:** {stats["added"]}
- **Updated Existing Companies:** {stats["updated"]}
- **Failed Saves/Updates:** {len(stats["failed_records"])}
- **Success Rate:** {success_rate:.2f}%

"""
        if "error" in stats:
            report_content += f"""### Critical Error
> [!CAUTION]
> Synchronizer encountered a fatal error during processing:
> **{stats["error"]}**

"""

        if stats["failed_records"]:
            report_content += """## Failed Records Detail
The following records could not be saved to the database:

| CIN | Company Name | Reason / Error |
| :--- | :--- | :--- |
"""
            for record in stats["failed_records"]:
                report_content += f"| `{record['cin']}` | {record['name']} | {record['reason']} |\n"

        try:
            with open(report_path, "w") as f:
                f.write(report_content)
            logger.info(f"Generated state sync report successfully at {report_path}")
        except Exception as e:
            logger.error(f"Failed to write state sync report file: {e}")

    def _load_last_offset_from_reports(self, state_key: str, resume_only_on_interruption: bool = False) -> int:
        """
        Scan the reports/ directory for the most recent sync report matching the target state/code,
        and parse the 'Last Processed Offset' or 'Total Records Fetched' value from it.
        """
        import re
        report_dir = os.path.join(os.getcwd(), "reports")
        if not os.path.exists(report_dir):
            return 0

        state_clean = "".join([c if c.isalnum() else "_" for c in state_key])
        if state_key == "All States":
            prefix = "global_sync_"
        else:
            prefix = f"state_sync_{state_clean}_"

        try:
            files = os.listdir(report_dir)
            matching_files = [f for f in files if f.startswith(prefix) and f.endswith(".md")]
            if not matching_files:
                return 0

            # Sort files descending to get the latest (by timestamp YYYYMMDD_HHMMSS)
            matching_files.sort(reverse=True)
            latest_file = matching_files[0]
            filepath = os.path.join(report_dir, latest_file)

            with open(filepath, "r", encoding="utf-8") as f:
                content = f.read()

            # If resume_only_on_interruption is requested, check the status inside the report first
            if resume_only_on_interruption:
                status_match = re.search(r"-\s+\*\*Status:\*\*\s+(\w+)", content)
                if status_match:
                    status_val = status_match.group(1).strip()
                    if status_val == "Completed":
                        logger.info(f"Latest report for '{state_key}' shows status is Completed. Starting fresh from offset 0 to update old records.")
                        return 0

            # 1. First, search for Last Processed Offset
            match = re.search(r"-\s+\*\*Last Processed Offset:\*\*\s+(\d+)", content)
            if match:
                offset_val = int(match.group(1))
                logger.info(f"Resuming '{state_key}' from parsed Last Processed Offset: {offset_val} (report: {latest_file})")
                return offset_val

            # 2. Second, search for Total Records Fetched (to support existing reports)
            match = re.search(r"-\s+\*\*Total Records Fetched:\*\*\s+(\d+)", content)
            if match:
                offset_val = int(match.group(1))
                logger.info(f"Resuming '{state_key}' from parsed Total Records Fetched: {offset_val} (report: {latest_file})")
                return offset_val

        except Exception as e:
            logger.error(f"Error loading last offset from latest markdown report: {e}")
        return 0

    async def _interruptible_sleep(self, duration: float):
        """Sleeps in small chunks checking for self._stop_requested to ensure rapid responsiveness to stop signals."""
        chunk = 0.5
        elapsed = 0.0
        while elapsed < duration:
            if self._stop_requested:
                break
            sleep_time = min(chunk, duration - elapsed)
            await asyncio.sleep(sleep_time)
            elapsed += sleep_time
