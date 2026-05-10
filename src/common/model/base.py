import datetime
import re
from functools import lru_cache
from uuid import uuid4

from sqlalchemy import TIMESTAMP, Boolean, Column, DateTime, String, func
from sqlalchemy.orm import as_declarative, declared_attr

from common.model.types.uuid import UUID


class CoreModel:
    id = Column(UUID, primary_key=True, default=uuid4)
    created_at: datetime.datetime = Column(DateTime, server_default=func.now())
    modified_at: datetime.datetime = Column(TIMESTAMP, server_default=func.now(), onupdate=func.current_timestamp())
    is_deleted = Column(Boolean(), default=False)
    created_by: str = Column(String(36))
    modified_by: str = Column(String(36))

    @classmethod
    @lru_cache(maxsize=1)
    def get_columns(cls):
        columns = []
        for column in cls.__table__.columns:
            name = f"{column}".split(".")[-1]
            columns.append(name)
        return columns


@as_declarative()
class Base:
    __name__: str

    # Generate __tablename__ automatically
    @declared_attr
    def __tablename__(cls) -> str:
        table_name = "_".join(["app", re.sub(r"(?<!^)(?=[A-Z])", "_", cls.__name__).lower()])
        return table_name.replace("_model", "")
