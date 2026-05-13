from common.service.unit_of_work import AbstractUnitOfWork


class BaseService:
    uow: AbstractUnitOfWork = None

    def __init__(self):
        pass

    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
