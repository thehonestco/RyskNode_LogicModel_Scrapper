from common.adapter.base import FastCRUDRepository
from common.schema.company import CompanyCreate, CompanyUpdate
from model.company import Company


from sqlalchemy import text
from typing import Optional


class CompanyRepository(FastCRUDRepository[Company, CompanyCreate, CompanyUpdate]):
    model = Company

    async def get_company_with_latest_snapshot(self, identifier: str) -> Optional[dict]:
        # Clean identifier
        identifier = identifier.strip().upper()
        if len(identifier) == 15:
            # GSTIN lookup
            query = text("""
                SELECT c.*, s.payload, s.provider, s.fetched_at 
                FROM companies c
                JOIN company_data_snapshots s ON c.id = s.company_id
                WHERE s.payload->'gstRegistrations' @> :gstin_json
                ORDER BY s.fetched_at DESC
                LIMIT 1
            """)
            import json
            result = await self.session.execute(query, {"gstin_json": json.dumps([{"gstin": identifier}])})
        else:
            # CIN lookup
            query = text("""
                SELECT c.*, s.payload, s.provider, s.fetched_at 
                FROM companies c
                LEFT JOIN company_data_snapshots s ON c.id = s.company_id
                WHERE c.cin = :cin
                ORDER BY s.fetched_at DESC
                LIMIT 1
            """)
            result = await self.session.execute(query, {"cin": identifier})

        row = result.mappings().first()
        return dict(row) if row else None
