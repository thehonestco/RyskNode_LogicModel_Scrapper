from common.adapter.base import FastCRUDRepository
from common.schema.company import CompanyCreate, CompanyUpdate
from model.company import Company


class CompanyRepository(FastCRUDRepository[Company, CompanyCreate, CompanyUpdate]):
    model = Company
