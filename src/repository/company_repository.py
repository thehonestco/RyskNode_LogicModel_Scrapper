from common.adapter.base import FastCRUDRepository
from model.company_data import CompanyData
from common.schema.company import CompanyDataCreate, CompanyDataUpdate

class CompanyRepository(FastCRUDRepository[CompanyData, CompanyDataCreate, CompanyDataUpdate]):
    model = CompanyData
