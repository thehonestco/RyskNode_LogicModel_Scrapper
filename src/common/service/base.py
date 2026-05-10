from common.base.context_vars import reset_current_user_uuid, set_current_user_uuid
from common.service.unit_of_work import AbstractUnitOfWork


class BaseService:
    current_user_id: str = None
    uow: AbstractUnitOfWork = None

    def __init__(self, current_user_id: str = None):
        self.current_user_id = current_user_id
        self._ctx_token = set_current_user_uuid(current_user_id)

    def __exit__(self, exc_type, exc_val, exc_tb):
        reset_current_user_uuid(self._ctx_token)
