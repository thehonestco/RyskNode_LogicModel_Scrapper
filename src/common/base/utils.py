import logging
import traceback
import typing

import inject
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from common.base.error import BaseError
from common.base.error_conf import ErrorConfig
from common.base.settings import CoreSettings
from common.schema import ResponseSchema

logger = logging.getLogger("app")


def respond(
    code: int = 0, exc: BaseError = None, message: str = None, resp_schema: typing.Type[ResponseSchema] = None, **kwargs
) -> JSONResponse:
    """
    Give proper response to the client
    :param code:
    :param exc:
    :param message:
    :param resp_schema:
    :return:
    """
    logger.info(f"Generate response for Code: {code!r} or Exc:{exc!r} with Message:{message!r}")
    error_conf = inject.instance(ErrorConfig)
    settings = inject.instance(CoreSettings)
    headers = getattr(exc, "headers", None) if exc else None

    if not code:
        code = exc.response_code
        message = message or exc.message

    include_trace = False
    if exc:
        include_trace = getattr(exc, "include_trace", False)

    msg = error_conf.get(code)

    if not msg and exc:
        msg = {
            "response_code": exc.response_code,
            "message": exc.message,
            "http_code": exc.http_code,
            "response_type": exc.response_type,
        }

    if message:
        msg["message"] = message

    kwargs = kwargs or {}
    msg.update(kwargs)

    try:
        http_code = msg.pop("http_code")
    except KeyError:
        http_code = code

    logger.info(f"{code!r} {exc!r} Respond with message: {msg}")

    if settings.app_env not in ["PROD"]:
        description = msg.get("description", "")
        if include_trace:
            trace = traceback.format_exc()
            logger.exception("Exception:")
            description = f"{description} {trace}" if description else f"{trace}"
        # Set the description only on lower environments
        msg["description"] = description

    if kwargs:
        msg.update(kwargs)
    resp_schema = resp_schema or ResponseSchema
    response = resp_schema(**msg)

    # Return the success response
    if headers:
        return JSONResponse(status_code=http_code, content=jsonable_encoder(response), headers=headers)
    else:
        return JSONResponse(
            status_code=http_code,
            content=jsonable_encoder(response),
        )
