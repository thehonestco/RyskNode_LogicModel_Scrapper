import abc
import datetime
import math
import typing
from typing import Any, Dict
from uuid import uuid4

from sqlalchemy import Column, and_, distinct, func, or_, update
from sqlalchemy.orm import Session

from common.base.context_vars import get_current_user_uuid
from common.domains import BaseDomain
from common.enums import SearchFieldOperatorEnum
from common.model.base import CoreModel
from common.model.types.uuid import UUID

CREATED_AT_FORMAT = "%Y-%m-%d %H:%M:%S"


class Singleton(type):
    """Singleton Metaclass"""

    _instances = {}

    def __call__(cls, *args, **kwargs):
        if cls not in cls._instances:
            cls._instances[cls] = super(Singleton, cls).__call__(*args, **kwargs)
        return cls._instances[cls]


class BaseBackend(metaclass=Singleton):
    """Provides Basic features for the backend"""

    def __init__(self, *args, **kwargs):
        super(BaseBackend, self).__init__()

    def set_dict(self, key, data, **kwargs):
        raise NotImplementedError

    def get_dict(self, key):
        raise NotImplementedError

    def set_list(self, key: str, data: list, **kwargs):
        raise NotImplementedError

    def get_list(self, key: str):
        raise NotImplementedError

    def set_str(self, key: str, data: str, **kwargs):
        raise NotImplementedError

    def get_str(self, key: str):
        raise NotImplementedError

    def find_keys(self, pattern):
        raise NotImplementedError

    def scan(self, match_: str, **kwargs) -> str:
        raise NotImplementedError

    def delete(self, *keys):
        raise NotImplementedError


class AbstractRepository(abc.ABC):
    @abc.abstractmethod
    def add(self, model: CoreModel):
        raise NotImplementedError

    @abc.abstractmethod
    def get(self, uuid) -> typing.Union[CoreModel, None]:
        raise NotImplementedError

    @abc.abstractmethod
    def update(self, data: dict, where: dict):
        raise NotImplementedError


class SqlAlchemyRepository(AbstractRepository):
    model: typing.Type[CoreModel] = None
    search_fields: typing.List[Column] = None

    def __init__(self, session: Session):
        super().__init__()
        self.session = session

    async def add(self, model: CoreModel | Dict[str, Any] | BaseDomain):
        if isinstance(model, BaseDomain):
            inp_model = model.model_dump(exclude_related=True, exclude_unset=True)
            model_data = {}
            for column in self.model.get_columns():
                if column in inp_model:
                    model_data[column] = inp_model[column]
            model = self.model(**model_data)
        elif isinstance(model, dict):
            model_data = {}
            for column in self.model.get_columns():
                if column in model:
                    model_data[column] = model[column]
            model = self.model(**model_data)

        model.created_by = get_current_user_uuid()
        model.modified_by = model.created_by
        if not model.id:
            model.id = str(uuid4())

        self.session.add(model)
        return model

    async def get(self, id_: UUID, is_deleted: bool = False) -> typing.Union[CoreModel, None]:
        return self.session.query(self.model).filter_by(id=id_, is_deleted=is_deleted).first()

    async def update(self, values: Dict[str, Any] | BaseDomain, where: typing.Tuple):
        if not values:
            values = {}
        model_data = {}
        if isinstance(values, BaseDomain):
            inp_model = values.model_dump(exclude_related=True, exclude_computed=True, exclude_unset=True)
            for column in self.model.get_columns():
                if column in inp_model:
                    model_data[column] = inp_model[column]
        elif isinstance(values, dict):
            for column in self.model.get_columns():
                if column in values:
                    model_data[column] = values[column]
        model_data["modified_by"] = get_current_user_uuid()

        self.session.query(self.model).filter(*where).update(model_data)

    async def update_by(self, values: Dict[str, Any] | BaseDomain, where: Dict[str, Any]):
        if not values:
            values = {}
        model_data = {}
        if isinstance(values, BaseDomain):
            inp_model = values.model_dump(exclude_related=True, exclude_computed=True, exclude_unset=True)
            for column in self.model.get_columns():
                if column == "id":
                    continue
                if column in inp_model:
                    model_data[column] = inp_model[column]
        elif isinstance(values, dict):
            for column in self.model.get_columns():
                if column in values:
                    model_data[column] = values[column]
        model_data["modified_by"] = get_current_user_uuid()

        self.session.query(self.model).filter_by(**where).update(model_data)

    async def update_multiple(self, values: dict, where: tuple):
        stmt = update(self.model).where(*where).values(**values)
        self.session.execute(stmt)

    async def get_single(self, **kwargs) -> typing.Union[typing.Type[CoreModel], None]:
        return self.session.query(self.model).filter_by(**kwargs).first()

    def filter(self, order_by: str = None, order: str = None, **kwargs):
        if kwargs:
            kwargs["is_deleted"] = kwargs.get("is_deleted", False)
        else:
            kwargs = {"is_deleted": False}
        if kwargs.get("is_deleted", False) is None:
            del kwargs["is_deleted"]
        query = self.session.query(self.model)
        if kwargs:
            query = query.filter_by(**kwargs)

        if order_by:
            order = order or "desc"
            return query.order_by(getattr(getattr(self.model, order_by), order.lower())()).all()
        else:
            return query.all()

    def refresh(self, instance_):
        self.session.refresh(instance_)

    async def delete(self, record: typing.Union[CoreModel, str, int]):
        if isinstance(record, CoreModel):
            self.session.query(self.model).filter(self.model.id == record.id).update({"is_deleted": True})
        elif type(record) == UUID or type(record) == str:
            self.session.query(self.model).filter(self.model.id == record).update({"is_deleted": True})

    async def hard_delete(self, **kwargs):
        if not kwargs:
            raise Exception(f"Cannot delete all record from {self.model.__tablename__}")
        self.session.query(self.model).filter_by(**kwargs).delete()

    async def get_paginated_result(
        self,
        search: str = None,
        order_by: str = "created_at",
        order: str = "DESC",
        page: int = 1,
        page_size: int = 100,
        **kwargs,
    ):
        query, total_count = await self.get_list_filter_query(search=search, order_by=order_by, order=order, **kwargs)
        offset_value = page * page_size - page_size
        result = query.offset(offset_value).limit(page_size).all()
        total_pages = math.ceil(total_count / page_size)
        return {
            "page": page,
            "page_size": page_size,
            "data": result,
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next_page": bool(page < total_pages),
            "has_prev_page": bool(page > 1),
        }

    async def get_list_filter_query(
        self,
        search: str = None,
        order_by: str = "created_at",
        order: str = "desc",
        excluded_ids: typing.List[str | UUID] | None = None,
        exact_search: bool = False,
        **kwargs,
    ):
        if kwargs:
            kwargs["is_deleted"] = kwargs.get("is_deleted", False)
        else:
            kwargs = {"is_deleted": False}
        if kwargs.get("is_deleted", False) is None:
            del kwargs["is_deleted"]
        query = self.session.query(self.model).distinct()

        count_query = self.session.query(func.count(distinct(self.model.id)))
        if kwargs:
            query, count_query, kwargs = self.range_filters(query, count_query, **kwargs)
            query = query.filter_by(**kwargs)
            count_query = count_query.filter_by(**kwargs)

        if excluded_ids:
            query = query.filter(self.model.id.not_in(excluded_ids))
            count_query = count_query.filter(self.model.id.not_in(excluded_ids))

        if search and not exact_search:
            if self.search_fields and len(self.search_fields) > 1:
                query = query.filter(or_(*[field_.ilike(f"%{search}%") for field_ in self.search_fields]))
                count_query = count_query.filter(or_(*[field_.ilike(f"%{search}%") for field_ in self.search_fields]))
            elif self.search_fields and len(self.search_fields) == 1:
                query = query.filter(self.search_fields[0].ilike(f"%{search}%"))
                count_query = count_query.filter(self.search_fields[0].ilike(f"%{search}%"))

        if search and exact_search:
            if self.search_fields and len(self.search_fields) > 1:
                query = query.filter(or_(*[field_.like(f"{search}") for field_ in self.search_fields]))
                count_query = count_query.filter(or_(*[field_.like(f"{search}") for field_ in self.search_fields]))
            elif self.search_fields and len(self.search_fields) == 1:
                query = query.filter(self.search_fields[0].like(f"{search}"))
                count_query = count_query.filter(self.search_fields[0].like(f"{search}"))

        if order_by:
            return query.order_by(getattr(getattr(self.model, order_by), order.lower())()), count_query.scalar()
        else:
            return query, count_query.scalar()

    def count_records(self, **kwargs) -> int:
        query = self.session.query(func.count(distinct(self.model.id)))
        if kwargs:
            query = query.filter_by(**kwargs)
        return query.scalar()

    def range_filters(self, query, count_query, **kwargs):
        if (
            (kwargs.get("range_from") or kwargs.get("range_to"))
            and kwargs.get("range_field")
            and kwargs.get("range_operator")
        ):
            range_query = None
            field = getattr(self.model, kwargs.get("range_field"))
            if kwargs.get("range_field") == "created_at":
                if kwargs.get("range_from"):
                    range_from_data = datetime.datetime.strftime(kwargs.get("range_from"), CREATED_AT_FORMAT)
                    if len(range_from_data.split(" ")) <= 1:
                        kwargs["range_from"] = datetime.datetime.strptime(
                            f"{kwargs.get('range_from')} 00:00:00", CREATED_AT_FORMAT
                        )
                if kwargs.get("range_to"):
                    range_to_data = datetime.datetime.strftime(kwargs.get("range_to"), CREATED_AT_FORMAT)
                    if len(range_to_data.split(" ")) <= 1:
                        kwargs["range_to"] = datetime.datetime.strptime(
                            f"{kwargs.get('range_to')} 23:59:59", CREATED_AT_FORMAT
                        )

            if kwargs.get("range_from") is not None and kwargs.get("range_to") is None:
                range_query = field.__ge__(kwargs.get("range_from"))
            elif kwargs.get("range_from") is None and kwargs.get("range_to") is not None:
                range_query = field.__le__(kwargs.get("range_to"))
            else:
                if kwargs.get("range_to") and kwargs.get("range_operator") == SearchFieldOperatorEnum.between:
                    range_query = and_(field.__ge__(kwargs.get("range_from")), field.__le__(kwargs.get("range_to")))
                elif kwargs.get("range_operator") == SearchFieldOperatorEnum.gt:
                    range_query = field.__gt__(kwargs.get("range_from"))
                elif kwargs.get("range_operator") == SearchFieldOperatorEnum.lt:
                    range_query = field.__lt__(kwargs.get("range_from"))
                elif kwargs.get("range_operator") == SearchFieldOperatorEnum.eq:
                    range_query = field.__eq__(kwargs.get("range_from"))
                elif kwargs.get("range_operator") == SearchFieldOperatorEnum.ne:
                    range_query = field.__ne__(kwargs.get("range_from"))
                elif kwargs.get("range_operator") == SearchFieldOperatorEnum.gteq:
                    range_query = field.__ge__(kwargs.get("range_from"))
                elif kwargs.get("range_operator") == SearchFieldOperatorEnum.lteq:
                    range_query = field.__le__(kwargs.get("range_from"))

            if range_query is not None:
                query = query.filter(range_query)
                count_query = count_query.filter(range_query)

        for item in ["range_from", "range_to", "range_field", "range_operator"]:
            if item in kwargs:
                del kwargs[item]
        return query, count_query, kwargs
