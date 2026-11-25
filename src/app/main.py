"""Фабрика FastAPI-приложения и жизненный цикл."""
from contextlib import asynccontextmanager
from pathlib import Path

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from settings import get_settings
from app.api.routes import router as api_router
from app.api.pages import router as pages_router
from app.db import init_engine
from app.services.search import run_all_checks


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()

    init_engine(settings.DATABASE_URL)

    scheduler: AsyncIOScheduler | None = None
    if settings.ENABLE_SCHEDULER:
          # отложенный импорт

        scheduler = AsyncIOScheduler(timezone=settings.SCHEDULER_TZ)
        for hour, minute in settings.times:
            scheduler.add_job(
                run_all_checks,
                trigger="cron",
                hour=hour,
                minute=minute,
                id=f"check_{hour:02d}{minute:02d}",
                misfire_grace_time=3600,
            )
        scheduler.start()
    app.state.settings = settings
    app.state.scheduler = scheduler
    yield
    if scheduler:
        scheduler.shutdown(wait=False)


def create_app() -> FastAPI:
    # Swagger-документация доступна по /swagger (вместо стандартного /docs)
    app = FastAPI(
        title="Makrura",
        lifespan=lifespan,
        docs_url="/swagger",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # Пути к шаблонам/статике — относительно пакета app, чтобы работало
    # из любой рабочей директории (в т.ч. внутри контейнера и при pip install .)
    import app as _app_pkg
    _pkg_dir = Path(_app_pkg.__file__).parent
    app.include_router(api_router, prefix="/api/private/v1")
    app.include_router(pages_router)
    app.mount("/static", StaticFiles(directory=str(_pkg_dir / "static")), name="static")
    return app


app = create_app()
