from fastapi import FastAPI, Request
from fastapi.exceptions import HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.httpsredirect import HTTPSRedirectMiddleware
from fastapi.responses import JSONResponse

from common.base.error import BaseError, InternalServerError
from common.base.middlewares import RequestContextLogMiddleware
from common.base.router import APIRouter
from common.base.utils import respond
from common.schema import ApiInfoSchema


def create_app(settings) -> FastAPI:
    """
    Creates the App
    :param settings:
    :return:
    """
    # Creating app
    api = FastAPI(
        title=settings.app_title,
        description=settings.app_description,
        version=settings.app_version,
        root_path=settings.root_path,
        openapi_url=settings.openapi_url,
    )

    if settings.force_https:
        api.add_middleware(HTTPSRedirectMiddleware)

    api.add_middleware(RequestContextLogMiddleware)

    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"] if settings.app_env in ["LOCAL", "DEV"] else settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Add the error handlers
    @api.exception_handler(HTTPException)
    async def http_exc_handler(request: Request, exc: HTTPException) -> JSONResponse:
        headers = getattr(exc, "headers", None)
        exc_ = InternalServerError(message=f"Caught {exc} in http_exc_handler", headers=headers)
        return respond(exc=exc_)

    @api.exception_handler(BaseError)
    async def handle_base_error(request: Request, exc: BaseError):
        return respond(exc=exc)

    @api.exception_handler(Exception)
    async def exc_handler(request: Request, exc: Exception) -> JSONResponse:
        exc_ = InternalServerError(message=f"Caught {exc} in exc_handler")
        return respond(exc=exc_)

    router = APIRouter()

    # Adding the Global default routes
    @router.get("/info", summary="Information", description="Obtain API information", response_model=ApiInfoSchema)
    async def welcome():
        return {
            "message": f"Welcome to {settings.app_title}",
            "name": settings.app_title,
            "version": settings.app_version,
            "api_version": settings.api_version,
        }

    @router.get("/healthz")
    async def healthz():
        return f"{settings.api_version}: {settings.app_title}"

    # Adding all routes to the api
    for r in APIRouter.get_routes():
        api.include_router(r)

    # Mount background tasks dashboard
    from fastapi_bgtasks_dashboard import mount_bg_tasks_dashboard
    mount_bg_tasks_dashboard(app=api)

    return api
