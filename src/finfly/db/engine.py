from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from finfly.core.config import DB_URL
from finfly.db.models import Base

engine = create_async_engine(DB_URL)
session_maker = async_sessionmaker(engine, expire_on_commit=False)


async def init_db() -> None:
    """Создаёт таблицы, если их ещё нет."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
