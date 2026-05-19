from datetime import date, datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict


class CompanyDataBase(BaseModel):
    company_name: str
    company_name_normalized: Optional[str] = None
    cin: str
    entity_type: Optional[str] = None
    registration_number: Optional[str] = None
    current_status: Optional[str] = None
    incorporation_date: Optional[date] = None
    company_age: Optional[str] = None
    registrar_of_companies: Optional[str] = None
    director_names: Optional[str] = None
    director_din: Optional[str] = None
    director_appointment_date: Optional[date] = None
    address: Optional[str] = None
    last_agm_date: Optional[date] = None
    latest_revenue: Optional[str] = None
    latest_revenue_date: Optional[date] = None
    latest_balance_sheet_date: Optional[date] = None
    authorized_capital: Optional[int] = None
    paid_up_capital: Optional[int] = None
    main_activity_group_code: Optional[str] = None
    description_of_main_activity: Optional[str] = None
    business_activity_code: Optional[str] = None
    description_of_business_activity: Optional[str] = None
    data_source: Optional[str] = None
    scraped_at: Optional[datetime] = None
    raw_data: Optional[dict] = None
    is_active: bool = True

class CompanyDataCreate(CompanyDataBase):
    pass

class CompanyDataUpdate(CompanyDataBase):
    company_name: Optional[str] = None
    cin: Optional[str] = None

class CompanyDataResponse(CompanyDataBase):
    id: int
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)
