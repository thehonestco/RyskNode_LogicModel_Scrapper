import abc
import logging

import inject
from sqlalchemy.ext.asyncio import AsyncSession

logger = logging.getLogger(__name__)


def default_session_factory() -> AsyncSession:
    """
    Default DB Async Session Factory
    :return:
    """
    logger.info("Creating database Async Session")
    return inject.instance(AsyncSession)


class AbstractUnitOfWork(abc.ABC):
    # Database session
    session: AsyncSession = None

    def __init__(self):
        pass

    async def __aenter__(self) -> "AbstractUnitOfWork":
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if exc_type:
            await self.rollback()
        else:
            await self.commit()

    async def commit(self):
        await self._commit()

    @abc.abstractmethod
    async def _commit(self):
        raise NotImplementedError

    @abc.abstractmethod
    async def rollback(self):
        raise NotImplementedError


class FastCRUDUnitOfWork(AbstractUnitOfWork):
    """
    FastCRUD / SQLAlchemy Unit of Work with Transactional Support
    """

    def __init__(self, session: AsyncSession = None, session_factory=default_session_factory):
        self.session = session
        self.session_factory = session_factory
        self.close_on_exit = False
        super(FastCRUDUnitOfWork, self).__init__()

    async def __aenter__(self):
        await super().__aenter__()
        if not self.session:
            self.session = self.session_factory()
            self.close_on_exit = True
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await super().__aexit__(exc_type, exc_val, exc_tb)
        if self.close_on_exit:
            logger.info("Closing SQLAlchemy Async Session created")
            await self.session.close()

    async def _commit(self):
        await self.session.commit()

    async def rollback(self):
        await self.session.rollback()
