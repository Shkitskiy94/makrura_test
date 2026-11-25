"""Автоматическая подвязка: любой активный проект ищется во всех группах поиска.

Связи «проект ↔ группа» (project_sources) создаются автоматически:
- при создании/импорте проекта — он подвязывается ко всем существующим группам;
- при создании/импорте группы — она подвязывается ко всем активным проектам.

Вызывать внутри той же транзакции, что и создание сущностей, затем commit().
Все функции асинхронные (AsyncSession).
"""
from __future__ import annotations

import logging

from sqlalchemy import insert, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.entities import Project, ProjectSource, SearchSource

log = logging.getLogger(__name__)


async def link_all_projects_to_source(db: AsyncSession, source_id: int) -> int:
    """Подвязывает одну группу поиска ко всем активным проектам. Возвращает число новых связей."""
    existing = set(
        (await db.execute(
            select(ProjectSource.project_id).where(ProjectSource.source_id == source_id)
        )).scalars()
    )
    active_project_ids = [
        pid
        for pid in (await db.execute(
            select(Project.id).where(Project.is_active.is_(True))
        )).scalars()
        if pid not in existing
    ]
    if active_project_ids:
        await db.execute(
            insert(ProjectSource),
            [{"project_id": pid, "source_id": source_id} for pid in active_project_ids],
        )
    return len(active_project_ids)


async def link_all_sources_to_project(db: AsyncSession, project_id: int) -> int:
    """Подвязывает проект ко всем существующим группам поиска. Возвращает число новых связей."""
    existing = set(
        (await db.execute(
            select(ProjectSource.source_id).where(ProjectSource.project_id == project_id)
        )).scalars()
    )
    source_ids = [
        sid
        for sid in (await db.execute(select(SearchSource.id))).scalars()
        if sid not in existing
    ]
    if source_ids:
        await db.execute(
            insert(ProjectSource),
            [{"project_id": project_id, "source_id": sid} for sid in source_ids],
        )
    return len(source_ids)
