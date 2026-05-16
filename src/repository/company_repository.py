from common.adapter.base import FastCRUDRepository
from common.schema.company import CompanyDataCreate, CompanyDataUpdate
from model.company_data import CompanyData


class CompanyRepository(FastCRUDRepository[CompanyData, CompanyDataCreate, CompanyDataUpdate]):
    model = CompanyData
