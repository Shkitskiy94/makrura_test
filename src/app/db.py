"""Инфраструктура БД: async engine, async session, Base.

Весь слой данных асинхронный (SQLAlchemy 2.x asyncio API + aiomysql),
чтобы приложение обслуживало нескольких пользователей одновременно
и не блокировало event loop на запросах к MySQL.
"""
from collections.abc import AsyncGenerator

from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from settings import get_settings


class Base(DeclarativeBase):
    pass


def _make_engine(database_url: str):
    """Движок с учётом idle-таймаута MySQL (pool_pre_ping/recycle)."""
    return create_async_engine(
        database_url,
        pool_pre_ping=True,   # MySQL закрывает idle-соединения — перепроверяем перед выдачей
        pool_recycle=3600,
        future=True,
    )


_engine = None
_SessionLocal: async_sessionmaker[AsyncSession] | None = None


def init_engine(database_url: str) -> None:
    """Создаёт engine/session-factory (вызывается один раз при старте приложения;
    в тестах — с sqlite+aiosqlite)."""
    global _engine, _SessionLocal
    if _engine is None:
        _engine = _make_engine(database_url)
        _SessionLocal = async_sessionmaker(
            bind=_engine, autoflush=False, expire_on_commit=False
        )


def get_engine():
    init_engine(_settings_url())
    return _engine


def _settings_url() -> str:

    return get_settings().DATABASE_URL


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """Dependency для FastAPI: выдать асинхронную сессию на время запроса."""
    init_engine(_settings_url())
    assert _SessionLocal is not None
    async with _SessionLocal() as session:
        yield session


def new_session() -> AsyncSession:
    """Сессия вне HTTP-контекста (фоновые задачи, планировщик)."""
    init_engine(_settings_url())
    assert _SessionLocal is not None
    return _SessionLocal()
