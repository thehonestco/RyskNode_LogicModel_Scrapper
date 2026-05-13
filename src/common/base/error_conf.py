"""
Error Configuration file to map error codes with messages
"""
from typing import Any, Dict

from common.base import constants
from common.base.singlton_meta import SingletonMeta
from common.enums import ResponseTypeEnum


class ErrorConfig(metaclass=SingletonMeta):
    """
    Keeps the error/Success messages at one place
    """

    _error_config: Dict[int, Dict[str, Any]] = {}

    def __init__(self) -> None:
        pass

    @classmethod
    def extend(cls, conf: Dict[int, Dict[str, Any]]) -> None:
        cls._error_config.update(conf)

    @classmethod
    def get(cls, code: int) -> Dict[str, Any]:
        return cls._error_config.get(code, {}).copy()


# Internal application messages
ErrorConfig.extend(
    {
        constants.HTTP_500_INTERNAL_SERVER_ERROR: {  # type: ignore [attr-defined]
            "response_code": constants.HTTP_500_INTERNAL_SERVER_ERROR,  # type: ignore [attr-defined]
            "http_code": constants.HTTP_500_INTERNAL_SERVER_ERROR,  # type: ignore [attr-defined]
            "response_type": ResponseTypeEnum.error,
            "message": "Internal server error. Please contact site administrator.",
        },
        constants.HTTP_401_UNAUTHORIZED: {  # type: ignore [attr-defined]
            "response_code": constants.HTTP_401_UNAUTHORIZED,  # type: ignore [attr-defined]
            "http_code": constants.HTTP_401_UNAUTHORIZED,  # type: ignore [attr-defined]
            "response_type": ResponseTypeEnum.error,
            "message": "Unauthorized",
        },
        constants.HTTP_403_FORBIDDEN: {  # type: ignore [attr-defined]
            "response_code": constants.HTTP_403_FORBIDDEN,  # type: ignore [attr-defined]
            "http_code": constants.HTTP_403_FORBIDDEN,  # type: ignore [attr-defined]
            "response_type": ResponseTypeEnum.error,
            "message": "User is not having access to requested resource.",
        },
        constants.HTTP_429_TOO_MANY_REQUESTS: {  # type: ignore [attr-defined]
            "response_code": constants.HTTP_429_TOO_MANY_REQUESTS,  # type: ignore [attr-defined]
            "http_code": constants.HTTP_429_TOO_MANY_REQUESTS,  # type: ignore [attr-defined]
            "response_type": ResponseTypeEnum.error,
            "message": "Request limit reached.",
        },
        constants.HTTP_404_NOT_FOUND: {  # type: ignore [attr-defined]
            "response_code": constants.HTTP_404_NOT_FOUND,  # type: ignore [attr-defined]
            "http_code": constants.HTTP_404_NOT_FOUND,  # type: ignore [attr-defined]
            "response_type": ResponseTypeEnum.error,
            "message": "Record not found",
        },
        constants.HTTP_417_EXPECTATION_FAILED: {  # type: ignore [attr-defined]
            "response_code": constants.HTTP_417_EXPECTATION_FAILED,  # type: ignore [attr-defined]
            "http_code": constants.HTTP_417_EXPECTATION_FAILED,  # type: ignore [attr-defined]
            "response_type": ResponseTypeEnum.error,
            "message": "System is not in a state to perform this operation",
        },
        constants.HTTP_422_UNPROCESSABLE_ENTITY: {  # type: ignore [attr-defined]
            "response_code": constants.HTTP_422_UNPROCESSABLE_ENTITY,  # type: ignore [attr-defined]
            "http_code": constants.HTTP_422_UNPROCESSABLE_ENTITY,  # type: ignore [attr-defined]
            "response_type": ResponseTypeEnum.error,
            "message": "Some data is missing",
        },
        constants.HTTP_201_CREATED: {  # type: ignore [attr-defined]
            "response_code": constants.HTTP_201_CREATED,  # type: ignore [attr-defined]
            "http_code": constants.HTTP_201_CREATED,  # type: ignore [attr-defined]
            "response_type": ResponseTypeEnum.success,
            "message": "Entity created successfully",
        },
        constants.HTTP_409_CONFLICT: {  # type: ignore [attr-defined]
            "response_code": constants.HTTP_409_CONFLICT,  # type: ignore [attr-defined]
            "http_code": constants.HTTP_409_CONFLICT,  # type: ignore [attr-defined]
            "response_type": ResponseTypeEnum.error,
            "message": "Entity already exists",
        },
    }
)
