import abc
import logging

import inject
from sqlalchemy.orm.session import Session

from common.repository import TokenRedisRepository

logger = logging.getLogger(__name__)


def default_session_factory() -> Session:
    """
    Default DB Session Factory
    :return:
    """
    logger.info("Creating database Session")
    return inject.instance(Session)


class AbstractUnitOfWork(abc.ABC):
    # Database session
    session: Session = None
    tokens: TokenRedisRepository = None

    def __init__(self):
        self.tokens = TokenRedisRepository()

    async def __aenter__(self) -> "AbstractUnitOfWork":
        return self

    async def __aexit__(self, *args):
        self.rollback()

    def commit(self):
        self._commit()

    @abc.abstractmethod
    def _commit(self):
        raise NotImplementedError

    @abc.abstractmethod
    def rollback(self):
        raise NotImplementedError


class SqlAlchemyUnitOfWork(AbstractUnitOfWork):
    """
    SQLAlchemy Unit of Work
    """

    def __init__(self, session: Session = None, session_factory=default_session_factory):
        self.session = session
        self.session_factory = session_factory
        self.close_on_exit = False
        super(SqlAlchemyUnitOfWork, self).__init__()

    async def __aenter__(self):
        await super().__aenter__()
        if not self.session:
            self.session = self.session_factory()
            self.close_on_exit = True
        return self

    async def __aexit__(self, *args):
        await super().__aexit__(*args)
        if self.close_on_exit:
            logger.info("Closing SQLAlchemy Session created")
            self.session.close()

    def _commit(self):
        self.session.commit()

    def rollback(self):
        self.session.rollback()
