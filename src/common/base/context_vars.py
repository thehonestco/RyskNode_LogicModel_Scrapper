import typing
from contextvars import ContextVar

CURRENT_USER_UUID_CTX_KEY = "current_user_uuid"
_current_user_uuid_ctx_var: ContextVar[typing.Union[str, None]] = ContextVar(CURRENT_USER_UUID_CTX_KEY, default=None)


def get_current_user_uuid() -> str | None:
    return _current_user_uuid_ctx_var.get()


def set_current_user_uuid(user_uuid: typing.Union[str, None]) -> typing.Any:
    if user_uuid:
        return _current_user_uuid_ctx_var.set(user_uuid)


def reset_current_user_uuid(_token) -> None:
    if _token:
        _current_user_uuid_ctx_var.reset(_token)
