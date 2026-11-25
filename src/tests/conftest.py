"""Фикстуры: async-тесты на aiosqlite (in-memory) вместо MySQL.

Весь код приложения асинхронный, поэтому и тесты асинхронные
(asyncio_mode=AUTO в pyproject.toml). TestClient здесь не подходит:
он крутит ASGI-приложение в отдельном потоке/лупе, а фикстура БД живёт
в лупе pytest — SQLAlchemy поднимает MissingGreenlet. Поэтому используем
httpx.AsyncClient + ASGITransport: всё выполняется в одном event loop.

Все вызовы к /api/private/v1/* автоматически получают заголовок
X-Auth-Token (токен из env API_TOKEN).

db_session — AsyncSession поверх того же движка/соединения (StaticPool),
что и приложение: данные, записанные тестом, видят HTTP-запросы и наоборот.
"""
import contextlib

import httpx
import pytest
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db import Base, get_db
import app.models.entities  # noqa: F401

API_TOKEN = "hide_me_on_preprod"


class _NoCloseSession(AsyncSession):
    """Сессия-обёртка: выход из `async with` НЕ закрывает её.

    Нужна для фикстуры db_session: тест работает с одной открытой сессией
    (undo commit после запросов клиента остаётся видимым), а очистку делаем
    явно через real_close в финализаторе фикстуры.
    """

    async def close(self):  # no-op
        pass

    async def real_close(self):
        await super().close()


class AuthClient:
    """Обёртка над AsyncClient: сама добавляет X-Auth-Token к API-вызовам."""

    def __init__(self, inner):
        self._inner = inner

    def _fix(self, url: str) -> dict:
        return {"X-Auth-Token": API_TOKEN} if "/api/" in url else {}

    def request(self, method, url, **kw):
        kw.setdefault("headers", {}).update(self._fix(url))
        return self._inner.request(method, url, **kw)

    def get(self, url, **kw):
        return self.request("GET", url, **kw)

    def post(self, url, **kw):
        return self.request("POST", url, **kw)

    def patch(self, url, **kw):
        return self.request("PATCH", url, **kw)

    def put(self, url, **kw):
        return self.request("PUT", url, **kw)

    def delete(self, url, **kw):
        return self.request("DELETE", url, **kw)

    def __getattr__(self, name):
        return getattr(self._inner, name)


@contextlib.asynccontextmanager
async def make_env():
    """Один общий движок: сессия теста и сессии HTTP-клиента работают с ним."""
    engine = create_async_engine(
        "sqlite+aiosqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    @event.listens_for(engine.sync_engine, "connect")
    def _fk_on(dbapi_conn, _):
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    Session = async_sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)

    from app.main import create_app

    app = create_app()

    async def _override():
        async with Session() as session:
            yield session

    app.dependency_overrides[get_db] = _override
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as ac:
        yield engine, AuthClient(ac)


@pytest.fixture()
async def engine():
    async with make_env() as (eng, _client):
        yield eng


@pytest.fixture()
async def client():
    async with make_env() as (_eng, cl):
        yield cl


@pytest.fixture()
async def env():
    """И движок (для прямой записи в БД), и клиент — в одном shared соединении."""
    async with make_env() as (eng, cl):
        yield eng, cl


@pytest.fixture()
async def db_session(engine):
    """AsyncSession поверх общего движка; коммитидь через await db_session.commit()."""
    maker = async_sessionmaker(bind=engine, class_=_NoCloseSession,
                               expire_on_commit=False, autoflush=False)
    session = maker()
    try:
        yield session
    finally:
        await session.rollback()
        await session.real_close()
