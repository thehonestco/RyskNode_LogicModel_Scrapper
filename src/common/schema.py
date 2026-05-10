import typing
from datetime import date
from enum import Enum

import inject
from fastapi import Request
from fastapi.security import OAuth2PasswordBearer
from fastapi.security.oauth2 import get_authorization_scheme_param
from pydantic import UUID4, BaseModel, Field

from common.base.settings import CoreSettings
from common.enums import OrderEnum, SearchFieldOperatorEnum


class JWTBase(BaseModel):
    exp: int | None = None
    sub: str | None = None

    scopes: typing.List[str] = Field(default_factory=list)

    @property
    def computed_actions(self) -> typing.List[str]:
        return list(set(self.scopes or []))


class JWTUser(JWTBase):
    """Schema for a JWT token created for a user."""

    id: str
    name: str
    email: str
    user_type: str


class ResponseType(str, Enum):
    success: str = "SUCCESS"
    error: str = "ERROR"
    warning: str = "WARNING"
    info: str = "INFO"


class ApiInfoSchema(BaseModel):
    name: str = Field(..., title="Name", description="Name of the app")
    version: str = Field(..., title="Version", description="Version of the app")
    api_version: str = Field(..., title="API Version", description="Version of the API")
    message: str = Field(..., title="Message", description="Welcome message")


class ResponseSchema(BaseModel):
    response_code: int = Field(..., title="Response Code", description="Unique response " "code specific to error")
    response_type: ResponseType = Field(None, title="Response Type", description="Response type")
    message: str = Field(..., title="Message", description="Message may be useful for user")
    description: typing.Optional[str] = Field(
        default=None, title="Description", description="Debug " "information for developer"
    )
    public_id: typing.Optional[UUID4] = Field(
        default=None,
        title="Entity Public ID",
        description="Public ID of the entity " "on which currently we are operating on",
    )

    def __init__(self, **kwargs):
        super(ResponseSchema, self).__init__(**kwargs)
        settings = inject.instance(CoreSettings)
        if settings.app_env == "PROD":
            self.description = None


class BaseRequestSchema(BaseModel):
    pass


class AuthenticationSchema(OAuth2PasswordBearer):
    def __init__(self, **kwargs):
        config = inject.instance(CoreSettings)
        super(AuthenticationSchema, self).__init__(tokenUrl=config.token_url, **kwargs)

    async def __call__(self, request: Request) -> typing.Optional[str]:
        authorization: str = request.headers.get("Authorization")
        scheme, token = get_authorization_scheme_param(authorization)
        return token


class PaginationResponseSchema(BaseModel):
    data: typing.List[dict] = Field(default_factory=list, title="List of records")
    total_count: int = Field(default=0, title="Total record count")
    page_size: int = Field(default=10, title="Records per page")
    page: int = Field(default=1, title="Current Page Number")
    total_pages: int = Field(default=1, title="Total number of pages")
    has_next_page: bool = Field(default=False, title="Has next page")
    has_prev_page: bool = Field(default=False, title="Has previous page")


class SearchPaginatedRequestSchema(BaseModel):
    search: str | None = Field(default=None, title="Filter records")
    page: int = Field(default=1, title="Requested page number", gt=0)
    page_size: int = Field(default=10, title="Number of records per page", gt=0)
    order_by: str = Field(default="created_at", title="Sort records by")
    order: OrderEnum = Field(default=OrderEnum.desc, title="Sort order")


class SearchRangeFilterSchema(BaseModel):
    range_from: typing.Optional[date] = Field(default=None, title="Range from")
    range_to: typing.Optional[date] = Field(default=None, title="Range to")
    range_field: typing.Optional[str] = Field(default="created_at", title="Date range field")
    range_operator: SearchFieldOperatorEnum = Field(
        default=SearchFieldOperatorEnum.between, title="Range operator"
    )
