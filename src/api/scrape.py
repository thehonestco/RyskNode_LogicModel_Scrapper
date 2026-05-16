import inject
from fastapi import BackgroundTasks, Depends

from api.schema.scrape import ScrapeAcknowledgment, ScrapeRequest
from common.base import constants
from common.base.router import APIRouter
from common.base.utils import respond
from common.schema.base import ResponseSchema
from service.scrape_service import ScrapeService

router = APIRouter(prefix="/api/v1", tags=["Scrape"])


@router.post("/scrape", response_model=ResponseSchema)
async def scrape(
    request: ScrapeRequest,
    background_tasks: BackgroundTasks,
    scrape_service: ScrapeService = Depends(lambda: inject.instance(ScrapeService)),
):
    if isinstance(request.queries, str):
        # Single request
        # service raises ApplicationError if not found
        result = await scrape_service.scrape_single(request.queries)
        return respond(code=constants.HTTP_200_OK, data=result)
    else:
        # Multiple requests
        background_tasks.add_task(scrape_service.batch_scrape_background, request.queries)
        return respond(
            code=constants.HTTP_200_OK,
            data=ScrapeAcknowledgment(
                status="Processing in background", total_queries=len(request.queries)
            ).model_dump(),
        )
