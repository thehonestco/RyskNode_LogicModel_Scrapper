from logging import getLogger

import inject
from fastapi import Depends
from jose import ExpiredSignatureError, JWTError, jwt

from common.base import constants
from common.base.error import ApplicationError, InternalServerError, JWTTokenError, JWTTokenExpiredError
from common.base.settings import CoreSettings
from common.base.utils import extract_authenticated_user
from common.repository import RedisRepository
from common.schema import AuthenticationSchema, JWTUser

logger = getLogger(__name__)


async def get_authorised_user(token: str = Depends(AuthenticationSchema())):
    """

    :param token:
    :return:
    """

    public_id = extract_authenticated_user(token)
    repo = RedisRepository()
    token_data = repo.get(public_id)
    if token_data:
        return public_id
    raise ApplicationError(response_code=constants.HTTP_401_UNAUTHORIZED, message="User already logout.")


async def get_token(
    token: str | None = Depends(AuthenticationSchema()),
) -> JWTUser | None:
    """Dependency injection which extracts and validates the bearer token from the request.

    This can be used to validate standard non-websocket tokens from any token actor.

    Args:
        token: the bearer token extracted from the header.

    Returns:
        decoded_token: the decoded token wrapped in our internal pydantic schema format
    """
    config: CoreSettings = inject.instance(CoreSettings)
    decoded_token: JWTUser | None = None
    if token:
        try:
            payload = jwt.decode(token=token, key=config.shared_secret_key, algorithms=[config.algorithm])
            decoded_token = JWTUser(**payload)
            return decoded_token
        except ExpiredSignatureError:
            raise JWTTokenExpiredError()
        except JWTError:
            raise JWTTokenError()
        except Exception as e:
            logger.error(f"General Exception error decoding token: {e}")
            raise InternalServerError()
    return decoded_token
