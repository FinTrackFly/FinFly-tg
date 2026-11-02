"""Тесты используют отдельную БД в памяти и никогда не запускают бота."""

import os

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

# Конфигурация задаётся до импорта модулей БД.
os.environ["DB_URL"] = "sqlite+aiosqlite:///:memory:"
os.environ["BOT_TOKEN"] = "123456:TEST_ONLY_NO_NETWORK"
os.environ["GIGACHAT_CREDENTIALS"] = "test-only"

from finfly.db import engine as db_engine
from finfly.db import requests
from finfly.db.models import Base


@pytest_asyncio.fixture
async def database(monkeypatch):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    sessions = async_sessionmaker(engine, expire_on_commit=False)
    monkeypatch.setattr(db_engine, "engine", engine)
    monkeypatch.setattr(db_engine, "session_maker", sessions)
    monkeypatch.setattr(requests, "session_maker", sessions)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    yield sessions
    await engine.dispose()


@pytest.fixture(autouse=True)
def fixed_analysis_clock(monkeypatch):
    from datetime import datetime
    from finfly.services import stats

    monkeypatch.setattr(stats, "utc_now", lambda: datetime(2026, 11, 1, 12))
    stats._throttle.clear()
