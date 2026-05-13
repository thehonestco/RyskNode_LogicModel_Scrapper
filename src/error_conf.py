from fastapi import status

import constants
from common.base.error_conf import ErrorConfig
from common.enums import ResponseTypeEnum

# Global application related messages
ErrorConfig.extend(
    {
        constants.RECORD_NOT_FOUND: {
            "response_code": constants.RECORD_NOT_FOUND,
            "http_code": status.HTTP_404_NOT_FOUND,
            "response_type": ResponseTypeEnum.error,
            "message": "Record not found",
        },
        constants.MISSING_DATA: {
            "response_code": constants.MISSING_DATA,
            "http_code": status.HTTP_422_UNPROCESSABLE_ENTITY,
            "response_type": ResponseTypeEnum.error,
            "message": "Data is missing in request",
        },
        constants.RESPONSE_OK: {
            "response_code": constants.RESPONSE_OK,
            "http_code": status.HTTP_200_OK,
            "response_type": ResponseTypeEnum.success,
            "message": "Operation performed with success",
        },
        constants.HTTP_409_CONFLICT: {
            "response_code": constants.HTTP_409_CONFLICT,
            "http_code": status.HTTP_409_CONFLICT,
            "response_type": ResponseTypeEnum.error,
            "message": "Record already exists",
        },
    }
)
