from sqlalchemy import Column, BigInteger, String, Text, Date, DateTime, Boolean, func
from sqlalchemy.dialects.postgresql import JSONB, TSVECTOR
from common.model.base import Base

class CompanyData(Base):
    __tablename__ = "company_data"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_name = Column(String(255), nullable=False)
    company_name_normalized = Column(String(255), nullable=True)
    cin = Column(String(100), nullable=False, unique=True)
    entity_type = Column(String(100), nullable=True)
    registration_number = Column(String(100), nullable=True)
    current_status = Column(String(100), nullable=True)
    incorporation_date = Column(Date, nullable=True)
    company_age = Column(String(50), nullable=True)
    registrar_of_companies = Column(String(255), nullable=True)
    director_names = Column(Text, nullable=True)
    director_din = Column(Text, nullable=True)
    director_appointment_date = Column(Date, nullable=True)
    address = Column(Text, nullable=True)
    last_agm_date = Column(Date, nullable=True)
    latest_revenue = Column(BigInteger, nullable=True)
    latest_revenue_date = Column(Date, nullable=True)
    latest_balance_sheet_date = Column(Date, nullable=True)
    authorized_capital = Column(BigInteger, nullable=True)
    paid_up_capital = Column(BigInteger, nullable=True)
    main_activity_group_code = Column(String(50), nullable=True)
    description_of_main_activity = Column(Text, nullable=True)
    business_activity_code = Column(String(50), nullable=True)
    description_of_business_activity = Column(Text, nullable=True)
    data_source = Column(String(100), nullable=True)
    scraped_at = Column(DateTime, nullable=True)
    raw_data = Column(JSONB, nullable=True)
    search_vector = Column(TSVECTOR, nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime, nullable=False, server_default=func.now())
    updated_at = Column(DateTime, nullable=False, server_default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime, nullable=True)
