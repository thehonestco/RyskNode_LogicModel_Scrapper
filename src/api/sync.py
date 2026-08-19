import logging
import os

from fastapi import HTTPException
from fastapi.responses import PlainTextResponse

from api.schema.sync import SyncRequest
from common.base import constants
from common.base.router import APIRouter
from common.base.utils import respond
from common.schema.base import ResponseSchema

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1", tags=["Sync"])


@router.post("/sync/data-gov", response_model=ResponseSchema)
async def sync_data_gov(request: SyncRequest):
    """
    Trigger the synchronization of RoC Company Master Data from data.gov.in
    as a background Celery task.

    Returns a ``task_id`` that can be used to poll status via
    ``GET /api/v1/tasks/{task_id}``.
    """
    from asyncworker.tasks import sync_data_gov_state

    task = sync_data_gov_state.delay(
        statecode=request.statecode,
        offset=request.offset,
    )

    return respond(
        code=constants.HTTP_200_OK,
        message="Synchronization task queued.",
        data={
            "task_id": task.id,
            "status": "QUEUED",
            "target_state": request.statecode or "All States",
        },
    )


@router.post("/sync/continue", response_model=ResponseSchema)
async def continue_sync_data_gov(request: SyncRequest):
    """
    Continue / resume a previous synchronization.

    Loads the last report for the requested statecode/All States:
    - If the previous sync completed, starts fresh from offset 0.
    - If it was interrupted (Stopped/Failed), resumes from last offset.
    """
    from asyncworker.tasks import sync_data_gov_state

    task = sync_data_gov_state.delay(
        statecode=request.statecode,
        offset=None,  # dynamic offset determined from latest report
        resume_only_on_interruption=True,
    )

    return respond(
        code=constants.HTTP_200_OK,
        message="Continuation/Resume synchronization task queued.",
        data={
            "task_id": task.id,
            "status": "QUEUED",
            "target_state": request.statecode or "All States",
            "mode": "Continuation",
        },
    )


@router.post("/sync/stop", response_model=ResponseSchema)
async def stop_sync_data_gov(task_id: str | None = None):
    """
    Revoke a running synchronization task.

    Args:
        task_id: The Celery task ID to revoke (from the sync/continue response).
                 If not provided, returns an error.
    """
    if not task_id:
        return respond(
            code=constants.HTTP_200_OK,
            message="No task_id provided. Use the task_id from the sync/continue response.",
            data={"status": "NO_ACTION"},
        )

    from asyncworker import app as celery_app

    celery_app.control.revoke(task_id, terminate=True, signal="SIGTERM")

    return respond(
        code=constants.HTTP_200_OK,
        message=f"Revoke signal sent for task {task_id}.",
        data={"task_id": task_id, "status": "REVOKING"},
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
                            os.path.basename(file).split("_")[-1].replace(".md", "")
                        ),
                        "size_bytes": stat.st_size,
                    }
                )
        reports.sort(key=lambda x: x["filename"], reverse=True)
        return respond(code=constants.HTTP_200_OK, data=reports)
    except Exception as e:
        logger.error(f"Error listing reports: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to list reports: {e}")


@router.get("/sync/reports/{report_name}", response_class=PlainTextResponse)
async def get_report_content(report_name: str):
    """
    Retrieve the markdown content of a specific synchronization report.
    """
    safe_name = os.path.basename(report_name)
    if safe_name != report_name or not report_name.endswith(".md"):
        raise HTTPException(status_code=400, detail="Invalid report filename.")

    report_dir = os.path.join(os.getcwd(), "reports")
    file_path = os.path.join(report_dir, safe_name)

    if not os.path.exists(file_path):
        raise HTTPException(status_code=404, detail=f"Report file '{report_name}' not found.")

    try:
        with open(file_path, "r", encoding="utf-8") as f:
            content = f.read()
        return PlainTextResponse(content=content, status_code=200)
    except Exception as e:
        logger.error(f"Error reading report {report_name}: {e}")
        raise HTTPException(status_code=500, detail=f"Failed to read report: {e}")

