import importlib
import logging

import inject
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from common.base.settings import CoreSettings
from error_conf import ErrorConfig
from settings import Settings

logger = logging.getLogger(__name__)

# Singletons for engine and session maker
_engine: AsyncEngine = None
_session_maker: async_sessionmaker[AsyncSession] = None


def get_settings() -> Settings:
    return Settings()


def get_sql_engine() -> AsyncEngine:
    """
    Creates or returns the singleton SQLAlchemy AsyncEngine
    """
    global _engine
    if _engine is not None:
        return _engine

    settings = get_settings()
    pool_class = None
    if settings.db_connection_pool_class:
        try:
            # Importing pool class
            module = importlib.import_module("sqlalchemy.pool")
            pool_class = getattr(module, settings.db_connection_pool_class)
        except ImportError as e:
            logger.error(f"Error while importing db session: {e}")
            pool_class = None

    _engine = create_async_engine(
        settings.sqlalchemy_uri,
        poolclass=pool_class,
    )
    logger.info("Created SQLAlchemy AsyncEngine singleton")
    return _engine


def get_session_maker() -> async_sessionmaker[AsyncSession]:
    """
    Creates or returns the singleton SQLAlchemy Async Session Maker
    """
    global _session_maker
    if _session_maker is not None:
        return _session_maker

    engine = get_sql_engine()
    logger.info("Initializing SQLAlchemy Async Session Maker singleton")
    _session_maker = async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)
    return _session_maker


def configure_dependency(binder: inject.Binder):
    from common.adapter.falcon_biz import FalconBizScraper
    from common.adapter.tracxn import TracxnScraper
    from common.service.unit_of_work import AbstractUnitOfWork, FastCRUDUnitOfWork
    from service.data_gov_sync_service import DataGovSyncService
    from service.scrape_service import ScrapeService

    settings = get_settings()
    engine = get_sql_engine()
    session_maker = get_session_maker()

    # bind instances
    binder.bind(CoreSettings, settings)
    binder.bind(Settings, settings)
    binder.bind(AsyncEngine, engine)

    # Singleton Error configuration
    binder.bind_to_constructor(ErrorConfig, ErrorConfig)

    # Use the singleton session maker as a provider for AsyncSession
    binder.bind_to_provider(AsyncSession, session_maker)

    # Bind Unit of Work as a provider to allow fresh instances when needed,
    # but we will favor reusing them in batch operations.
    binder.bind_to_provider(AbstractUnitOfWork, FastCRUDUnitOfWork)

    # Bind Scrapers
    binder.bind(FalconBizScraper, FalconBizScraper())
    binder.bind(TracxnScraper, TracxnScraper())

    # Bind ScrapeService
    def get_scrape_service():
        return ScrapeService(
            uow=inject.instance(AbstractUnitOfWork),
            falcon_scraper=inject.instance(FalconBizScraper),
            tracxn_scraper=inject.instance(TracxnScraper),
        )

    binder.bind_to_constructor(ScrapeService, get_scrape_service)

    # Bind DataGovSyncService
    def get_data_gov_sync_service():
        return DataGovSyncService(
            uow=inject.instance(AbstractUnitOfWork),
            settings=inject.instance(Settings),
        )

    binder.bind_to_constructor(DataGovSyncService, get_data_gov_sync_service)

    # Bind PPREService
    from service.ppre_service import PPREService
    def get_ppre_service():
        return PPREService(uow=inject.instance(AbstractUnitOfWork))

    binder.bind_to_constructor(PPREService, get_ppre_service)

    # Bind ReportService
    from service.report_service import ReportService
    def get_report_service():
        return ReportService()

    binder.bind_to_constructor(ReportService, get_report_service)




def create_isolated_uow(settings: Settings):
    """
    Creates an isolated AsyncEngine, async_sessionmaker, and FastCRUDUnitOfWork
    to be used within worker threads (background tasks). This ensures that background tasks
    manage their own connections within their own event loop, preventing InterfaceErrors.
    """
    import importlib

    from common.service.unit_of_work import FastCRUDUnitOfWork

    pool_class = None
    if settings.db_connection_pool_class:
        try:
            module = importlib.import_module("sqlalchemy.pool")
            pool_class = getattr(module, settings.db_connection_pool_class)
        except ImportError:
            pool_class = None

    engine = create_async_engine(
        settings.sqlalchemy_uri,
        poolclass=pool_class,
    )
    session_maker = async_sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=engine,
        class_=AsyncSession
    )

    def session_factory() -> AsyncSession:
        return session_maker()

    uow = FastCRUDUnitOfWork(session_factory=session_factory)
    uow._engine_to_dispose = engine
    return uow


async def dispose_isolated_uow(uow) -> None:
    """
    Cleanly disposes of the dedicated engine associated with the isolated UOW.
    """
    engine = getattr(uow, "_engine_to_dispose", None)
    if engine:
        try:
            await engine.dispose()
            logger.info("Cleanly disposed of isolated AsyncEngine connection pool")
        except Exception as e:
            logger.error(f"Error disposing isolated engine: {e}")
