import typing
from datetime import date
from logging import getLogger

from pydantic import UUID4, BaseModel
from typing_extensions import Literal, TypeAlias

from common import enums

IncEx: TypeAlias = "set[int] | set[str] | dict[int, typing.Any] | dict[str, typing.Any] | None"

T = typing.TypeVar("T")
NOT_DEFINED = typing.TypeVar("NOT_DEFINED")
logger = getLogger(__name__)


class BaseDomain(BaseModel):
    @property
    def protected_fields(self) -> typing.List[str]:
        return ["id"]

    @property
    def special_fields(self) -> typing.List[str]:
        return []

    @property
    def related_fields(self) -> typing.List[str]:
        return []

    id: UUID4 | None | str = None

    @staticmethod
    def _check_field(obj_: typing.Any, field_: str):
        try:
            if isinstance(obj_, BaseModel):
                return field_ in obj_.model_fields
            elif isinstance(obj_, dict):
                return field_ in obj_
            else:
                return hasattr(obj_, field_)
        except TypeError:
            return hasattr(obj_, field_)

    @staticmethod
    def _get_value_(obj: typing.Any, field_: str, default: typing.Any = None) -> typing.Any:
        try:
            return obj[field_]
        except (TypeError, KeyError):
            return getattr(obj, field_, default)

    def _copy_value(self, name: str, o_val: typing.Any, override_strategy: str = None):
        value = getattr(self, name)
        if override_strategy == "SELF_NULL" and not value:
            setattr(self, name, o_val)
        elif override_strategy == "OTHER_NOT_NULL" and o_val:
            setattr(self, name, o_val)
        elif not override_strategy:
            setattr(self, name, o_val)

    def copy_(self, other_obj: typing.Any, override_strategy: str = None):
        computed_fields_ = self.model_computed_fields
        for name, field_ in self.model_fields.items():
            if computed_fields_.get(name):
                continue

            alias = field_.alias
            if alias and self._check_field(other_obj, alias):
                o_val = self._get_value_(other_obj, alias, None)
            elif self._check_field(other_obj, name):
                o_val = self._get_value_(other_obj, name, None)
            else:
                continue
            self._copy_value(name, o_val, override_strategy=override_strategy)

    def __add__(self, other: typing.Union[dict, T]) -> T:
        if not issubclass(type(other), BaseDomain) and not type(other) == dict:
            raise NotImplementedError(f"Can not add {self.__class__.__name__} type in {type(other)}")

        if issubclass(type(other), BaseDomain):
            for field_, value in self:
                if field_ in other.model_fields_set:
                    o_val = self._get_value_(other, field_, None)
                    self._add(field_, o_val)
            return self
        else:
            for field_, value in other.items():
                o_val = value
                self._add(field_, o_val)
            return self

    def model_dump(
        self,
        *,
        mode: Literal["json", "python"] | str = "python",
        include: IncEx = None,
        exclude: IncEx = None,
        by_alias: bool = False,
        exclude_unset: bool = False,
        exclude_defaults: bool = False,
        exclude_none: bool = False,
        round_trip: bool = False,
        warnings: bool = True,
        exclude_related: bool = False,
        exclude_computed: bool = False,
    ) -> dict[str, typing.Any]:
        if exclude_related:
            if isinstance(exclude, set):
                exclude.update(set(self.related_fields))
            else:
                exclude = set(self.related_fields)
        if exclude_computed:
            if isinstance(exclude, set):
                exclude.update(set(self.model_computed_fields.keys()))
            else:
                exclude = set(set(self.model_computed_fields.keys()))
        return super().model_dump(
            mode=mode,
            include=include,
            exclude=exclude,
            by_alias=by_alias,
            exclude_unset=exclude_unset,
            exclude_defaults=exclude_defaults,
            exclude_none=exclude_none,
            round_trip=round_trip,
            warnings=warnings,
        )

    def _add(self, field_: str, o_val: typing.Any):
        computed_fields_ = self.model_computed_fields
        value = getattr(self, field_)
        try:
            if computed_fields_.get(field_):
                return
            if field_ in self.protected_fields and value:
                return
        except TypeError as e:
            logger.warning(f"{type(self)} is not having 'protected_fields' attribute Exception: {e}", exc_info=True)

        try:
            is_special_field = field_ in self.special_fields
        except TypeError as e:
            is_special_field = False
            logger.warning(f"{type(self)} is not having 'special_fields' attribute Exception: {e}", exc_info=True)

        if isinstance(o_val, BaseDomain) and isinstance(value, BaseDomain):
            value = value + o_val
            setattr(self, field_, value)
        elif is_special_field:
            if isinstance(value, list):
                value = list(set(value).union(set(o_val)))
            setattr(self, field_, value)
        else:
            setattr(self, field_, o_val)


class PaginatedParameters(BaseModel):
    page: int = 1
    page_size: int = 100
    order_by: str = "created_at"
    order: enums.OrderEnum = enums.OrderEnum.desc


class SearchPaginatedParameters(PaginatedParameters):
    search: str | None = None
    page: int = 1
    page_size: int = 100
    order_by: str = "created_at"
    order: enums.OrderEnum = enums.OrderEnum.desc


class SearchRangeFilterParameters(BaseDomain):
    range_from: date | None = None
    range_to: date | None = None
    range_field: str = "created_at"
    range_operator: enums.SearchFieldOperatorEnum = enums.SearchFieldOperatorEnum.between
