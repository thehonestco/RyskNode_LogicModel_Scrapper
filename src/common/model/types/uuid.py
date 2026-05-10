"""UUID types used in models."""
import uuid
from typing import Any

from sqlalchemy.dialects.postgresql import UUID as psqlUUID
from sqlalchemy.types import BINARY, TypeDecorator


class UUID(TypeDecorator):
    """Platform-independent GUID type.

    Uses Postgresql's UUID type, otherwise uses
    BINARY(16), to store UUID.

    """

    impl = BINARY
    cache_ok = True

    def __init__(self, length: int = 16) -> None:
        self.impl.length = length
        TypeDecorator.__init__(self, length=self.impl.length)

    def load_dialect_impl(self, dialect: Any) -> "UUID":
        if dialect.name == "postgresql":
            return dialect.type_descriptor(psqlUUID())
        else:
            return dialect.type_descriptor(BINARY(16))

    def process_bind_param(self, value: None | uuid.UUID, dialect) -> Any:
        if value is None:
            return value
        else:
            if not isinstance(value, uuid.UUID):
                if isinstance(value, bytes):
                    value = uuid.UUID(bytes=value)
                elif isinstance(value, int):
                    value = uuid.UUID(int=value)
                elif isinstance(value, str):
                    value = uuid.UUID(value)
        if dialect.name == "postgresql":
            return str(value)
        else:
            return value.bytes

    def process_result_value(self, value: Any, dialect: Any):
        if value is None:
            return value
        if dialect.name == "postgresql":
            return uuid.UUID(value)
        else:
            return uuid.UUID(bytes=value)

    def is_mutable(self) -> bool:
        return False
