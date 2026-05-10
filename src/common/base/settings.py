import typing

from pydantic import field_validator
from pydantic_core.core_schema import FieldValidationInfo
from pydantic_settings import BaseSettings, SettingsConfigDict

SQLITE_DEV = "sqlite:///data_dev.db"
SQLITE_TEST = "sqlite:///data_test.db"
SQLITE_STAGE = "sqlite:///data_stage.db"


class CoreSettings(BaseSettings):
    """ """

    app_title: str
    app_version: str
    api_version: str
    app_description: str

    app_env: str = "DEV"
    app_port: int = 8001
    app_host: str = "127.0.0.1"
    app_base_url: str | None = None
    base_url: str | None = None
    root_path: str = ""
    openapi_url: str = "/openapi.json"
    log_file: str = "app.log"
    current_env: str = "LOCAL"
    algorithm: str = "HS256"
    access_token_expire_minutes: int = 30
    token_url: str = "token"
    shared_secret_key: str | None = None
    app_secret_key: str | None = None

    force_https: bool = False
    cors_origins: typing.Set[str] = {"http://127.0.0.1:8888", "http://127.0.0.1:3000"}

    db_server: str | None = None
    db_user: str | None = "postgres"
    db_pass: str | None = "postgres"
    db_name: str | None = "postgres"
    db_host_type: str = "ipv4"
    db_port: int | None = 5432
    sqlalchemy_uri: str | None = None
    db_connection_pool_class: str | None = None

    redis_host: str = "localhost"
    redis_port: int = 6379
    redis_user: str | None = None
    redis_pass: str | None = None

    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    @property
    def redis_dsn(self):
        user_pass = [self.redis_user or "", self.redis_pass]
        user_pass = ":".join(user_pass)
        if user_pass:
            return f"redis://{user_pass}@{self.redis_host}:{self.redis_port}"
        else:
            return f"redis://{self.redis_host}:{self.redis_port}"

    @field_validator("sqlalchemy_uri")
    def assemble_db_connection(cls, v: typing.Optional[str], info: FieldValidationInfo) -> typing.Any:
        """Generates the database connection URI."""
        if isinstance(v, str):
            return v
        if info.data.get("db_server"):
            return (
                f'postgresql://{info.data.get("db_user")}:{info.data.get("db_pass")}@{info.data.get("db_server")}:'
                f'{info.data.get("db_port")}/{info.data.get("db_name") or ""}'
            )

        return SQLITE_DEV

    @property
    def is_local(self) -> bool:
        """Returns True if the current environment is LOCAL."""
        return self.app_env == "LOCAL"

    @property
    def is_dev(self) -> bool:
        """Returns TRue if current Environment is DEV."""
        return self.app_env == "DEV"

    @property
    def is_prod(self) -> bool:
        """Returns tru if the current environment is PROD"""
        return self.app_env == "PROD"

    @property
    def can_reload(self) -> bool:
        """Determines if service can be reloaded on file change."""
        return self.is_local or self.is_dev
