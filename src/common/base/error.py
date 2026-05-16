import inject
from pydantic import BaseModel

from common.base import constants
from common.base.error_conf import ErrorConfig
from common.enums import ResponseTypeEnum

# Define the Error Codes For the Exceptions


class BaseApplicationResponse(BaseModel):
    response_code: int
    response_type: ResponseTypeEnum
    message: str
    description: str | None = None


class InternalApplicationErrorResponse(BaseApplicationResponse):
    response_code: int = 500
    response_type: ResponseTypeEnum = ResponseTypeEnum.error


class EntityNotFoundErrorResponse(BaseApplicationResponse):
    response_code: int = 404
    response_type: ResponseTypeEnum = ResponseTypeEnum.error


class BaseError(Exception):
    def __init__(
        self,
        message: str,
        response_code: int,
        headers: dict = None,
        response_type: ResponseTypeEnum = ResponseTypeEnum.error,
        http_code: int = constants.HTTP_500_INTERNAL_SERVER_ERROR,
    ) -> None:
        self.response_code = response_code
        self.response_type = response_type
        self.http_code = http_code
        self.message = message
        self.headers = headers
        args = (response_code, message)
        super(BaseError, self).__init__(*args)

    def __str__(self) -> str:
        return f"{self.message}"

    def __repr__(self) -> str:
        return f"{self.__class__.__name__}({self.message!r}, {self.response_code})"


class ApplicationError(BaseError):
    @inject.autoparams("msg_conf")
    def __init__(
        self,
        response_code: int,
        msg_conf: ErrorConfig,
        headers: dict = None,
        message: str = None,
    ):
        msg = msg_conf.get(response_code)
        if not msg:
            msg = {
                "response_code": response_code,
                "response_type": ResponseTypeEnum.error,
                "message": message or f"Undefined error code {response_code!r}",
                "http_code": response_code,
            }

        if headers:
            msg["headers"] = headers
        if message:
            msg["message"] = message
        super(ApplicationError, self).__init__(**msg)


class InternalServerError(BaseError):
    def __init__(self, response_code=constants.HTTP_500_INTERNAL_SERVER_ERROR, message="", headers=None):
        super(InternalServerError, self).__init__(message=message, response_code=response_code, headers=headers)
