"""Поисковые сервисы по площадкам. Заглушки MVP: интерфейс зафиксирован, наполнение далее.

Всё асинхронное: фоновые проверки (APScheduler / BackgroundTasks) работают
в том же event loop, не блокируя HTTP-запросы других пользователей.
"""
import hashlib
import logging
from datetime import datetime

from sqlalchemy import select

from app.db import new_session
from app.models.entities import Finding, Platform, Project, ProjectSource, SearchSource

log = logging.getLogger(__name__)


def normalize_url(url: str) -> str:
    return url.strip().rstrip("/").lower()


def url_hash(url: str) -> str:
    return hashlib.md5(normalize_url(url).encode()).hexdigest()


async def save_findings(db, project: Project, candidates: list[dict]) -> int:
    """Сохраняет новые находки с защитой от дубликатов. Возвращает число новых записей."""
    new_count = 0
    for c in candidates:
        h = url_hash(c["url"])
        exists = (await db.execute(
            select(Finding).where(Finding.project_id == project.id, Finding.url_hash == h)
        )).scalar_one_or_none()
        if exists:
            exists.last_checked_at = datetime.now()
            continue
        db.add(
            Finding(
                project_id=project.id,
                platform=Platform(c["platform"]),
                url=c["url"],
                url_hash=h,
                post_id=c.get("post_id"),
                author=c.get("author"),
                snippet=c.get("snippet"),
                matched_title=c.get("matched_title"),
                match_score=c.get("match_score"),
                duration_sec=c.get("duration_sec"),
            )
        )
        new_count += 1
    await db.commit()
    return new_count


async def search_vk(project: Project) -> list[dict]:
    """TODO: vk.search via httpx (async) + VK API (нужен токен).

    Искать по project.search_terms (название + ключевые фразы) во всех
    группах проекта (таблица project_sources).
    """
    return []


async def search_telegram(project: Project) -> list[dict]:
    """TODO: поиск по telegram-каналам (парсинг t.me/s/<channel> или MTProto)."""
    return []


async def sources_for_project(db, project_id: int) -> list[SearchSource]:
    """Группы поиска проекта: для активного — все группы из справочника."""
    return list(
        (await db.execute(
            select(SearchSource)
            .join(ProjectSource, ProjectSource.source_id == SearchSource.id)
            .where(ProjectSource.project_id == project_id)
            .order_by(SearchSource.platform, SearchSource.id)
        )).scalars()
    )


async def run_all_checks() -> None:
    """Задача для APScheduler: пройтись по всем активным проектам.

    Каждый проект ищется по всем подвязанным группам поиска
    (все активные проекты связаны со всеми группами), по строкам
    «название + ключевые фразы» (Project.search_terms).
    """
    log.info("run_all_checks: start")
    async with new_session() as db:
        projects = (await db.execute(
            select(Project).where(Project.is_active.is_(True))
        )).scalars().all()
        total_new = 0
        for p in projects:
            terms = p.search_terms          # название + ключевые фразы
            sources = await sources_for_project(db, p.id)  # все группы поиска
            log.debug("project=%s terms=%s sources=%s", p.title, terms, len(sources))
            candidates = await search_vk(p) + await search_telegram(p)
            total_new += await save_findings(db, p, candidates)
        log.info("run_all_checks: done, projects=%s new_findings=%s", len(projects), total_new)
