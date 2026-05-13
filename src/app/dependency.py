import importlib
import logging

import inject
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker, create_async_engine

from common.base.settings import CoreSettings
from error_conf import ErrorConfig
from settings import Settings

logger = logging.getLogger(__name__)


def get_settings() -> Settings:
    return Settings()


def get_sql_engine() -> AsyncEngine:
    """
    Creates SQLAlchemy AsyncEngine
    :return:
    """
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

    engine = create_async_engine(
        settings.sqlalchemy_uri,
        poolclass=pool_class,
    )
    logger.info("Creating SQLAlchemy AsyncEngine")
    return engine


def sql_alchemy_session_factory() -> async_sessionmaker[AsyncSession]:
    """
    SQLAlchemy Async Session Maker
    """
    engine = get_sql_engine()
    logger.info("Initializing SQLAlchemy Async Session Maker")
    return async_sessionmaker(autocommit=False, autoflush=False, bind=engine, class_=AsyncSession)


def configure_dependency(binder: inject.Binder):
    # bind instances
    binder.bind(CoreSettings, get_settings())
    binder.bind(Settings, get_settings())
    binder.bind(AsyncEngine, get_sql_engine())

    # Singleton Error configuration
    binder.bind_to_constructor(ErrorConfig, ErrorConfig)

    # Always return the new SQLAlchemy Async Session
    binder.bind_to_provider(AsyncSession, sql_alchemy_session_factory())
