import abc
import datetime
import math
import typing
from typing import Any, Dict, Generic, Optional, Type, TypeVar
from uuid import uuid4

from fastcrud import FastCRUD
from pydantic import BaseModel
from sqlalchemy import Column, update
from sqlalchemy.ext.asyncio import AsyncSession

from common.domains import BaseDomain
from common.model.base import CoreModel
from common.model.types.uuid import UUID

CREATED_AT_FORMAT = "%Y-%m-%d %H:%M:%S"

ModelType = TypeVar("ModelType", bound=CoreModel)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseModel)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseModel)
DeleteSchemaType = TypeVar("DeleteSchemaType", bound=BaseModel)


class AbstractRepository(abc.ABC):
    @abc.abstractmethod
    async def add(self, model: Any):
        raise NotImplementedError

    @abc.abstractmethod
    async def get(self, uuid: Any) -> Optional[Any]:
        raise NotImplementedError

    @abc.abstractmethod
    async def update(self, data: dict, where: dict):
        raise NotImplementedError


class FastCRUDRepository(AbstractRepository, Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    model: Type[ModelType] = None
    search_fields: typing.List[Column] = None

    def __init__(self, session: AsyncSession):
        super().__init__()
        self.session = session
        self.crud = FastCRUD[ModelType, CreateSchemaType, UpdateSchemaType, DeleteSchemaType](self.model)

    async def add(self, model: CoreModel | Dict[str, Any] | BaseDomain) -> ModelType:
        if isinstance(model, BaseDomain):
            model_data = model.model_dump(exclude_related=True, exclude_unset=True)
        elif isinstance(model, dict):
            model_data = model
        else:
            model_data = {c: getattr(model, c) for c in self.model.get_columns() if hasattr(model, c)}

        if "id" not in model_data or not model_data["id"]:
            model_data["id"] = str(uuid4())

        return await self.crud.create(self.session, model_data)

    async def get(self, id_: UUID | str, is_deleted: bool = False) -> Optional[ModelType]:
        return await self.crud.get(self.session, id=id_, is_deleted=is_deleted)

    async def update(self, values: Dict[str, Any] | BaseDomain, where: typing.Tuple):
        """
        FastCRUD does not natively support arbitrary tuple-based where clauses in its `.update()`
        without building the query. We will fall back to core SQLAlchemy update for tuple where clauses.
        """
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

        stmt = update(self.model).where(*where).values(**model_data)
        await self.session.execute(stmt)

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

        await self.crud.update(self.session, model_data, **where)

    async def update_multiple(self, values: dict, where: tuple):
        stmt = update(self.model).where(*where).values(**values)
        await self.session.execute(stmt)

    async def get_single(self, **kwargs) -> Optional[ModelType]:
        return await self.crud.get(self.session, **kwargs)

    async def filter(self, order_by: str = None, order: str = None, **kwargs) -> list[ModelType]:
        if kwargs.get("is_deleted", False) is None:
            del kwargs["is_deleted"]
        elif "is_deleted" not in kwargs:
            kwargs["is_deleted"] = False

        sort_columns = [order_by] if order_by else None
        sort_orders = [order.lower()] if order else None

        result = await self.crud.get_multi(
            self.session, limit=None, sort_columns=sort_columns, sort_orders=sort_orders, **kwargs
        )
        return result.get("data", [])

    async def refresh(self, instance_):
        await self.session.refresh(instance_)

    async def delete(self, record: typing.Union[CoreModel, str, int]):
        if isinstance(record, CoreModel):
            await self.crud.update(self.session, {"is_deleted": True}, id=record.id)
        else:
            await self.crud.update(self.session, {"is_deleted": True}, id=record)

    async def hard_delete(self, **kwargs):
        if not kwargs:
            raise Exception(f"Cannot delete all record from {self.model.__tablename__}")
        await self.crud.db_delete(self.session, **kwargs)

    async def get_paginated_result(
        self,
        search: str = None,
        order_by: str = "created_at",
        order: str = "DESC",
        page: int = 1,
        page_size: int = 100,
        **kwargs,
    ):
        if kwargs.get("is_deleted", False) is None:
            del kwargs["is_deleted"]
        elif "is_deleted" not in kwargs:
            kwargs["is_deleted"] = False

        # Range filters conversion if needed
        kwargs = self._process_range_filters(**kwargs)

        offset_value = (page - 1) * page_size
        sort_columns = [order_by] if order_by else None
        sort_orders = [order.lower()] if order else None

        # Implement simple search logic if FastCRUD supports it, or rely on kwargs
        # Note: Advanced multi-column OR searches are tricky natively in get_multi kwargs,
        # so for exact match with FastCRUD, we handle single column or map them down.
        if search and self.search_fields and len(self.search_fields) == 1:
            search_col_name = self.search_fields[0].name
            kwargs[f"{search_col_name}__ilike"] = f"%{search}%"

        result = await self.crud.get_multi(
            self.session,
            offset=offset_value,
            limit=page_size,
            sort_columns=sort_columns,
            sort_orders=sort_orders,
            **kwargs,
        )

        total_count = result.get("total_count", 0)
        total_pages = math.ceil(total_count / page_size) if page_size else 0

        return {
            "page": page,
            "page_size": page_size,
            "data": result.get("data", []),
            "total_count": total_count,
            "total_pages": total_pages,
            "has_next_page": bool(page < total_pages),
            "has_prev_page": bool(page > 1),
        }

    async def count_records(self, **kwargs) -> int:
        if kwargs.get("is_deleted", False) is None:
            del kwargs["is_deleted"]
        elif "is_deleted" not in kwargs:
            kwargs["is_deleted"] = False

        kwargs = self._process_range_filters(**kwargs)
        return await self.crud.count(self.session, **kwargs)

    def _process_range_filters(self, **kwargs) -> Dict[str, Any]:
        """Convert gc/backend range filters to FastCRUD kwarg filters."""
        if (
            (kwargs.get("range_from") or kwargs.get("range_to"))
            and kwargs.get("range_field")
            and kwargs.get("range_operator")
        ):
            field = kwargs.get("range_field")
            op = kwargs.get("range_operator")
            r_from = kwargs.get("range_from")
            r_to = kwargs.get("range_to")

            if field == "created_at":
                if r_from and isinstance(r_from, datetime.date):
                    r_from = datetime.datetime.combine(r_from, datetime.datetime.min.time())
                elif r_from and isinstance(r_from, str):
                    r_from = (
                        datetime.datetime.strptime(r_from, CREATED_AT_FORMAT)
                        if " " in r_from
                        else datetime.datetime.strptime(f"{r_from} 00:00:00", CREATED_AT_FORMAT)
                    )

                if r_to and isinstance(r_to, datetime.date):
                    r_to = datetime.datetime.combine(r_to, datetime.datetime.max.time())
                elif r_to and isinstance(r_to, str):
                    r_to = (
                        datetime.datetime.strptime(r_to, CREATED_AT_FORMAT)
                        if " " in r_to
                        else datetime.datetime.strptime(f"{r_to} 23:59:59", CREATED_AT_FORMAT)
                    )

            if r_from is not None and r_to is None:
                kwargs[f"{field}__gte"] = r_from
            elif r_from is None and r_to is not None:
                kwargs[f"{field}__lte"] = r_to
            else:
                if op == "BETWEEN":  # SearchFieldOperatorEnum.between
                    kwargs[f"{field}__gte"] = r_from
                    kwargs[f"{field}__lte"] = r_to
                elif op == ">":
                    kwargs[f"{field}__gt"] = r_from
                elif op == "<":
                    kwargs[f"{field}__lt"] = r_from
                elif op == "=":
                    kwargs[f"{field}"] = r_from
                elif op == "!=":
                    kwargs[f"{field}__ne"] = r_from
                elif op == ">=":
                    kwargs[f"{field}__gte"] = r_from
                elif op == "<=":
                    kwargs[f"{field}__lte"] = r_from

        for item in ["range_from", "range_to", "range_field", "range_operator"]:
            if item in kwargs:
                del kwargs[item]
        return kwargs
