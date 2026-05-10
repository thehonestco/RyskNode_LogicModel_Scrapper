import importlib
import logging
from functools import lru_cache

import inject
from sqlalchemy import create_engine
from sqlalchemy.engine.base import Engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.orm.session import Session

from common.base.settings import CoreSettings
from error_conf import ErrorConfig
from settings import Settings

logger = logging.getLogger(__name__)


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache()
def get_sql_engine() -> Engine:
    """
    Creates SQLAlchemy Engine
    :return:
    """
    settings = get_settings()
    pool_class = None
    if settings.db_connection_pool_class:
        try:
            # Importing pool  class
            module = importlib.import_module("sqlalchemy.pool")
            pool_class = getattr(module, settings.db_connection_pool_class)
        except ImportError as e:
            logger.error(f"Error while importing db session: {e}")
            pool_class = None

    engine = create_engine(
        settings.sqlalchemy_uri,
        poolclass=pool_class,
    )
    logger.info("Creating SQLAlchemy Engine")
    return engine


def sql_alchemy_session_factory() -> sessionmaker:
    """ "
    SQLAlchemy Session Maker
    """
    engine = get_sql_engine()
    logger.info("Initializing SQLAlchemy Session Maker")
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


def configure_dependency(binder: inject.Binder):
    # bind instances
    binder.bind(CoreSettings, get_settings())
    binder.bind(Settings, get_settings())
    binder.bind(Engine, get_sql_engine())

    # Singleton Error configuration
    binder.bind_to_constructor(ErrorConfig, ErrorConfig)

    # Always return the new SQLAlchemy Session
    binder.bind_to_provider(Session, sql_alchemy_session_factory())
