import asyncio
import os
import sys

import inject

# Add src to path
sys.path.insert(0, os.path.abspath("src"))

from sqlalchemy.ext.asyncio import AsyncEngine

from app.dependency import configure_dependency
from common.model.base import Base


async def create_tables():
    print("Connecting to database and creating tables...")
    if not inject.is_configured():
        inject.configure(configure_dependency)

    engine = inject.instance(AsyncEngine)
    try:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        print("Tables created successfully.")
    except Exception as e:
        print(f"Error creating tables: {e}")


if __name__ == "__main__":
    asyncio.run(create_tables())
