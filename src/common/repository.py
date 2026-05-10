import typing

import inject

from common.adapter.base import AbstractRepository, BaseBackend
from common.base.settings import CoreSettings
from common.model.base import CoreModel


class RedisRepository(AbstractRepository):
    @inject.autoparams("backend")
    def __init__(self, backend: BaseBackend):
        self.backend: BaseBackend = backend

    def _add(self, key: str, data: typing.Union[dict, list, str], **kwargs):
        if isinstance(data, dict):
            self.backend.set_dict(key, data, **kwargs)
        elif isinstance(data, list):
            self.backend.set_list(key, data, **kwargs)
        elif isinstance(data, str):
            self.backend.set_str(key, data, **kwargs)
        else:
            raise NotImplementedError(f"Unable to save the {type(data)!r}")

    def add(self, model: CoreModel):
        # Simplistic implementation for boilerplate
        self._add(str(model.id), {"id": str(model.id)})

    def get(self, uuid: str) -> typing.Union[dict, str, list, None]:
        return self.backend.get_dict(uuid)

    def update(self, data: dict, where: dict):
        raise NotImplementedError("Cannot implement update record for RedisRepository")


class TokenRedisRepository(RedisRepository):
    def __init__(self, *args, **kwargs):
        settings = inject.instance(CoreSettings)
        self.token_time_exp = settings.access_token_expire_minutes * 60
        super(TokenRedisRepository, self).__init__(*args, **kwargs)

    def add_token(self, uuid, token_data):
        self._add(uuid, token_data, ex=self.token_time_exp)

    def get_token(self, uuid) -> typing.Optional[dict]:
        return self.backend.get_dict(uuid)

    def delete(self, uuid):
        self.backend.delete(uuid)
