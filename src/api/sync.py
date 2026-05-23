import asyncio
import logging
import os
from fastapi import BackgroundTasks, Depends, HTTPException
from fastapi.responses import PlainTextResponse

import inject
from api.schema.sync import SyncRequest
from common.base import constants
from common.base.router import APIRouter
from common.base.utils import respond
from common.schema.base import ResponseSchema
from service.data_gov_sync_service import DataGovSyncService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Sync"])


def run_sync_in_thread(
    statecode: str | None,
    sync_service: DataGovSyncService,
    cooldown: int | None = None,
    offset: int | None = None,
    resume_only_on_interruption: bool = False,
) -> None:
    """
    Synchronous wrapper to execute the async sync_state inside a new thread event loop.
    This is required to make the background task fully compatible with fastapi-bgtasks-dashboard,
    which executes background tasks in worker threads.
    """
    from app.dependency import create_isolated_uow, dispose_isolated_uow

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    uow = create_isolated_uow(sync_service.settings)
    try:
        loop.run_until_complete(
            sync_service.sync_state(
                state=statecode,
                uow=uow,
                rate_limit_cooldown=cooldown,
                offset=offset,
                resume_only_on_interruption=resume_only_on_interruption,
            )
        )
    except Exception as e:
        logger.error(f"Error executing sync task in background: {e}", exc_info=True)
    finally:
        try:
            loop.run_until_complete(dispose_isolated_uow(uow))
        except Exception as dispose_err:
            logger.error(f"Error disposing UOW engine in thread: {dispose_err}")
        loop.close()


@router.post("/sync/data-gov", response_model=ResponseSchema)
async def sync_data_gov(
    request: SyncRequest,
    background_tasks: BackgroundTasks,
    sync_service: DataGovSyncService = Depends(lambda: inject.instance(DataGovSyncService)),
):
    """
    Trigger the synchronization process of Registrars of Companies (RoC) Company Master Data
    from data.gov.in as a background task.
    If statecode is provided in the request body, only that state is synchronized.
    If statecode is missing/omitted, all states are synchronized page-by-page.
    """
    if sync_service._thread_lock.locked():
        logger.warning("Rejecting concurrent sync request: another sync is already in progress.")
        from common.base.error import ApplicationError
        raise ApplicationError(
            response_code=constants.HTTP_409_CONFLICT,
            message="Another synchronization task is already in progress.",
        )

    # Schedule the background task using BackgroundTasks.
    # We use a synchronous wrapper run_sync_in_thread that manages its own event loop.
    # This ensures full compatibility with the third-party fastapi-bgtasks-dashboard
    # (which executes tasks in separate OS threads) without raising RuntimeWarnings.
    background_tasks.add_task(
        run_sync_in_thread,
        request.statecode,
        sync_service,
        request.cooldown,
        request.offset,
    )

    return respond(
        code=constants.HTTP_200_OK,
        message="Synchronization started in the background.",
        data={
            "status": "Processing in background",
            "target_state": request.statecode or "All States",
        },
    )


@router.post("/sync/continue", response_model=ResponseSchema)
async def continue_sync_data_gov(
    request: SyncRequest,
    background_tasks: BackgroundTasks,
    sync_service: DataGovSyncService = Depends(lambda: inject.instance(DataGovSyncService)),
):
    """
    Trigger the synchronization continuation. 
    Loads the last report for the requested statecode/All States.
    - If status is Completed, starts from 0 to refresh/update old records.
    - Otherwise (Stopped, Failed, Running), resumes from the last successfully synced offset.
    """
    if sync_service._thread_lock.locked():
        logger.warning("Rejecting concurrent sync request: another sync is already in progress.")
        from common.base.error import ApplicationError
        raise ApplicationError(
            response_code=constants.HTTP_409_CONFLICT,
            message="Another synchronization task is already in progress.",
        )

    background_tasks.add_task(
        run_sync_in_thread,
        request.statecode,
        sync_service,
        request.cooldown,
        None,  # Dynamic offset from latest report
        True,  # resume_only_on_interruption = True
    )

    return respond(
        code=constants.HTTP_200_OK,
        message="Continuation/Resume synchronization started in the background.",
        data={
            "status": "Processing in background",
            "target_state": request.statecode or "All States",
            "mode": "Continuation",
        },
    )


@router.post("/sync/stop", response_model=ResponseSchema)
async def stop_sync_data_gov(
    sync_service: DataGovSyncService = Depends(lambda: inject.instance(DataGovSyncService)),
):
    """
    Stop the currently running background synchronization task.
    """
    if not sync_service._thread_lock.locked():
        return respond(
            code=constants.HTTP_200_OK,
            message="No synchronization task is currently running.",
            data={"status": "Inactive"},
        )

    sync_service._stop_requested = True
    return respond(
        code=constants.HTTP_200_OK,
        message="Stop request received. The synchronization task will stop cleanly after processing the current page/retries.",
        data={"status": "Stopping"},
    )


@router.get("/sync/reports", response_model=ResponseSchema)
async def list_reports():
    """
    List all generated statewise synchronization reports.
    """
    report_dir = os.path.join(os.getcwd(), "reports")
    if not os.path.exists(report_dir):
        return respond(
            code=constants.HTTP_200_OK,
            message="No reports generated yet.",
            data=[],
        )

    try:
        files = os.listdir(report_dir)
        reports = []
        for file in files:
            if (file.startswith("state_sync_") or file.startswith("global_sync_")) and file.endswith(".md"):
                file_path = os.path.join(report_dir, file)
                stat = os.stat(file_path)
                reports.append(
                    {
                        "filename": file,
                        "created_at": str(
                            os.path.basename(file)
                            .split("_")[-1]
                            .replace(".md", "")
                        ),  # parse/approximate timestamp from name
                        "size_bytes": stat.st_size,
                    }
                )
        # Sort by filename descending (newest first)
        reports.sort(key=lambda x: x["filename"], reverse=True)
        return respond(code=constants.HTTP_200_OK, data=reports)
    except Exception as e:
        logger.error(f"Error listing reports: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list reports: {e}")


@router.get("/sync/reports/{report_name}", response_class=PlainTextResponse)
async def get_report_content(report_name: str):
    """
    Retrieve the markdown content of a specific statewise synchronization report.
    """
    # Strict path sanitization to prevent directory traversal
    safe_name = os.path.basename(report_name)
    if safe_name != report_name or not report_name.endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid report filename.")

    report_dir = os.path.join(os.getcwd(), "reports")
    file_path = os.path.join(report_dir, safe_name)

    if not os.path.exists(file_path):
        raise HTTPException(
            status_code=404, detail=f"Report file '{report_name}' not found."
        )

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return PlainTextResponse(content=content, status_code=200)
    except Exception as e:
        logger.error(f"Error reading report {report_name}: {e}")
        raise HTTPException(
            status_code=500, detail=f"Failed to read report: {e}"
        )
