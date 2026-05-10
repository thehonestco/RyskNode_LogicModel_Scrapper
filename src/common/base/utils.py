import logging
import traceback
import typing
from datetime import datetime, timedelta

import inject
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse
from jose import JWTError, jwt
from passlib.context import CryptContext

from common.base import constants
from common.base.error import ApplicationError, BaseError
from common.base.error_conf import ErrorConfig
from common.base.settings import CoreSettings
from common.schema import ResponseSchema

logger = logging.getLogger("app")
pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


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


def verify_password(plain_password, hashed_password):
    try:
        return pwd_context.verify(plain_password, hashed_password)
    except Exception as exp:
        logger.error(f"Unable to verify password {exp}", exc_info=True)
        return False


def get_password_hash(password):
    try:
        return pwd_context.hash(password)
    except Exception:
        return password


def get_token_data(token, auto_error=True):
    config = inject.instance(CoreSettings)
    try:
        payload = jwt.decode(token, config.shared_secret_key, algorithms=[config.algorithm])
        public_id: str = payload.get("sub")
        if public_id is None:
            raise ApplicationError(response_code=constants.HTTP_401_UNAUTHORIZED, message="Token is invalid")
    except (JWTError, AttributeError) as e:
        logger.exception(f"Exception: {e}")
        raise ApplicationError(response_code=constants.HTTP_401_UNAUTHORIZED, message="Access Token is invalid")
    return public_id


def extract_authenticated_user(token):
    return get_token_data(token)


def create_access_token(data: dict, expires_delta: typing.Optional[timedelta] = None):
    config = inject.instance(CoreSettings)
    to_encode = data.copy()
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=config.access_token_expire_minutes)
    to_encode.update({"exp": expire})
    encoded_jwt = jwt.encode(to_encode, config.shared_secret_key, algorithm=config.algorithm)
    return encoded_jwt
