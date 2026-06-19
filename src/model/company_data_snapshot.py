from sqlalchemy import BigInteger, Column, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship

from common.model.base import Base


class CompanyDataSnapshot(Base):
    __tablename__ = "company_data_snapshots"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    company_id = Column(BigInteger, ForeignKey("companies.id"), nullable=False)
    provider = Column(String(255), nullable=False)
    payload = Column(JSONB, nullable=False)
    fetched_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, nullable=True, server_default=func.now())
    updated_at = Column(DateTime, nullable=True, server_default=func.now(), onupdate=func.now())

    # Relationship back to Company
    company = relationship("Company", back_populates="snapshots")
