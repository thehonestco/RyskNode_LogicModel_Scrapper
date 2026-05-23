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
        import asyncio

        def run_batch_sync(queries):
            from app.dependency import create_isolated_uow, dispose_isolated_uow
            from settings import Settings
            import logging
            thread_logger = logging.getLogger(__name__)

            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            settings = inject.instance(Settings)
            uow = create_isolated_uow(settings)

            try:
                session_factory = uow.session_factory
                loop.run_until_complete(scrape_service.batch_scrape_background(queries, session_factory=session_factory))
            except Exception as e:
                thread_logger.error(f"Error executing batch scrape in background: {e}", exc_info=True)
            finally:
                try:
                    loop.run_until_complete(dispose_isolated_uow(uow))
                except Exception as dispose_err:
                    thread_logger.error(f"Error disposing UOW engine in scrape thread: {dispose_err}")
                loop.close()

        background_tasks.add_task(run_batch_sync, request.queries)
        return respond(
            code=constants.HTTP_200_OK,
            data=ScrapeAcknowledgment(
                status="Processing in background", total_queries=len(request.queries)
            ).model_dump(),
        )
