"""HTML-страницы (Jinja2) — простой внутренний интерфейс.

Асинхронный слой: AsyncSession + await db.execute(...).
Сортировки на страницах: ?sort=id|title (по умолчанию id), направление ?dir=asc|desc.
"""
import io
import math

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload
from openpyxl import Workbook

from pathlib import Path

from app.db import get_db
from app.models.entities import (
    Finding,
    Platform,
    Project,
    ProjectKeyword,
    ProjectSource,
    SearchSource,
)
from settings import get_settings

router = APIRouter()
# Шаблоны ищем относительно пакета app — не зависит от рабочей директории
templates = Jinja2Templates(directory=str(Path(__file__).resolve().parent.parent / "templates"))

PAGE_SIZE = 50  # строк в таблице на страницу


def paginate(total: int, page: int) -> dict:
    """Общая пагинация для шаблонов: страницы «пред. / номера / след.»."""
    pages = max(1, math.ceil(total / PAGE_SIZE)) if total else 1
    window = [p for p in range(max(1, page - 2), min(pages, page + 2) + 1)]
    return {
        "page": page, "pages": pages, "page_size": PAGE_SIZE,
        "total": total, "window": window,
        "has_prev": page > 1, "has_next": page < pages,
    }


def collate_ru(expr):
    """Безопасная сортировка текста: стандартная binary-collation в MySQL, без изменений в SQLite.

    Ранее использовалась 'utf8mb4_ru_ai', но она доступна только в MariaDB 11.4+ / MySQL 8.0.42+,
    и на большинстве версий MySQL падает с ошибкой 1273 Unknown collation. Возвращаем обычный
    порядок сортировки (без COLLATE), чтобы работало везде.
    """
    return expr


def sort_spec(sort: str, direction: str, default_field: str, fields: dict):
    """Возвращает (ключ сортировки SQLAlchemy, фактические sort/dir для шаблонов).

    Применяется ровно одна сортировка; неизвестные значения -> дефолт."""
    field = sort if sort in fields else default_field
    order = direction.lower() if direction.lower() in ("asc", "desc") else "asc"
    col = fields[field]
    if field != "id":  # текстовые поля — с русской collation
        col = collate_ru(col)
    return (col.desc() if order == "desc" else col.asc()), field, order


@router.get("/", response_class=HTMLResponse)
async def index(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str = Query("", description="Поиск по названию и ключевым фразам"),
    page: int = Query(1, ge=1),
    sort: str = Query("id", description="Сортировка: id или title (применяется одна)"),
    dirn: str = Query("asc", alias="dir", description="asc|desc"),
):
    """Главная: поиск + сортировка + пагинация списка проектов. Группы — на своей странице."""
    query = select(Project).options(selectinload(Project.keywords))
    count_query = select(func.count(Project.id))
    q_clean = q.strip()
    if q_clean:
        like = f"%{q_clean}%"
        # ищем по названию ИЛИ по любой ключевой фразе проекта;
        # подзапрос без correlate, чтобы не схлопнуть дубли через GROUP BY
        kw_ids = (
            select(ProjectKeyword.project_id)
            .where(ProjectKeyword.phrase.ilike(like))
            .subquery()
        )
        cond = or_(Project.title.ilike(like),
                   Project.id.in_(select(kw_ids.c.project_id)))
        query = query.where(cond)
        count_query = count_query.where(cond)

    total = (await db.execute(count_query)).scalar_one()
    pages_meta = paginate(total, page)
    order_col, sort_used, dir_used = sort_spec(
        sort, dirn, "id", {"id": Project.id, "title": Project.title})
    projects = (await db.execute(
        query.order_by(order_col)
        .offset((pages_meta["page"] - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )).scalars().all()

    sources_total = (await db.execute(select(func.count(SearchSource.id)))).scalar_one()
    return templates.TemplateResponse(
        request, "index.html",
        {"projects": projects, "sources_total": sources_total,
         "platforms": [p.value for p in Platform],
         "q": q, "sort": sort_used, "dir": dir_used, "api_token": get_settings().API_TOKEN, **pages_meta},
    )


@router.get("/projects/{project_id}", response_class=HTMLResponse)
async def project_page(project_id: int, request: Request, db: AsyncSession = Depends(get_db)):
    """Страница проекта: ключевые фразы + все группы поиска (проект ищется во всех)."""
    project = (await db.execute(
        select(Project).options(selectinload(Project.keywords)).where(Project.id == project_id)
    )).scalar_one_or_none()
    if project is None:
        return HTMLResponse("Проект не найден", status_code=404)
    findings_count = (await db.execute(
        select(func.count(Finding.id)).where(Finding.project_id == project_id)
    )).scalar_one()
    sources = (await db.execute(
        select(SearchSource).order_by(SearchSource.platform, SearchSource.id)
    )).scalars().all()
    return templates.TemplateResponse(
        request, "project.html",
        {"project": project, "platforms": [p.value for p in Platform],
         "findings_count": findings_count, "sources": sources, "api_token": get_settings().API_TOKEN,},
    )


@router.get("/sources", response_class=HTMLResponse)
async def sources_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str = Query("", description="Поиск по URL группы"),
    page: int = Query(1, ge=1),
    sort: str = Query("id", description="Сортировка: id или platform (применяется одна)"),
    dirn: str = Query("asc", alias="dir", description="asc|desc"),
):
    """Группы поиска: создать (площадка + URL), удалить. Действуют для всех проектов.

    Поиск + сортировка + пагинация — аналогично странице проектов."""
    query = select(SearchSource)
    count_query = select(func.count(SearchSource.id))
    q_clean = q.strip()
    if q_clean:
        cond = SearchSource.url.ilike(f"%{q_clean}%")
        query = query.where(cond)
        count_query = count_query.where(cond)

    total = (await db.execute(count_query)).scalar_one()
    pages_meta = paginate(total, page)
    order_col, sort_used, dir_used = sort_spec(
        sort, dirn, "id", {"id": SearchSource.id, "platform": SearchSource.platform})
    sources = (await db.execute(
        query.order_by(order_col, SearchSource.id)
        .offset((pages_meta["page"] - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )).scalars().all()
    project_counts = dict(
        (await db.execute(
            select(ProjectSource.source_id, func.count(ProjectSource.project_id))
            .group_by(ProjectSource.source_id)
        )).all()
    )
    active_projects = (await db.execute(
        select(func.count(Project.id)).where(Project.is_active.is_(True))
    )).scalar_one()
    return templates.TemplateResponse(
        request, "sources.html",
        {"sources": sources, "platforms": [p.value for p in Platform],
         "project_counts": project_counts, "active_projects": active_projects,
         "q": q, "sort": sort_used, "dir": dir_used, "api_token": get_settings().API_TOKEN,
         **pages_meta},
    )


@router.get("/findings", response_class=HTMLResponse)
async def findings_page(
    request: Request,
    db: AsyncSession = Depends(get_db),
    q: str = Query("", description="Поиск по URL, автору или названию проекта"),
    page: int = Query(1, ge=1),
    sort: str = Query("id", description="Сортировка: id или title (название проекта)"),
    dirn: str = Query("desc", alias="dir", description="asc|desc"),
):
    query = select(Finding).options(selectinload(Finding.project))
    count_query = select(func.count(Finding.id))
    q_clean = q.strip()
    if q_clean:
        like = f"%{q_clean}%"
        proj_ids = (
            select(Project.id)
            .where(Project.title.ilike(like))
            .subquery()
        )
        cond = or_(Finding.url.ilike(like), Finding.author.ilike(like),
                   Finding.project_id.in_(select(proj_ids.c.id)))
        query = query.where(cond)
        count_query = count_query.where(cond)

    total = (await db.execute(count_query)).scalar_one()
    pages_meta = paginate(total, page)
    order_col, sort_used, dir_used = sort_spec(
        sort, dirn, "id", {"id": Finding.id, "title": Finding.project_id})
    rows = (await db.execute(
        query.order_by(order_col)
        .offset((pages_meta["page"] - 1) * PAGE_SIZE)
        .limit(PAGE_SIZE)
    )).scalars().all()
    return templates.TemplateResponse(
        request, "findings.html",
        {"findings": rows, "q": q, "sort": sort_used, "dir": dir_used, "api_token": get_settings().API_TOKEN,
         **pages_meta},
    )


@router.get("/projects-template.xlsx")
def projects_template():
    """Скачать пустой шаблон Excel: вкладка «Проекты» + вкладка «Группы поиска»."""

    wb = Workbook()
    ws = wb.active
    ws.title = "Проекты"
    ws.append(["Название проекта", "Ключевые фразы", "Описание", "Внешний id"])
    ws.append(["Пример: Название сериала", "сезон 2; serial online; смотреть сериал",
               "Фразы через точку с запятой; поиск идёт по названию и фразам", ""])
    ws.append(["Пример: Другой фильм", "", "Можно без ключевых фраз", "X-002"])
    for col, width in zip("ABCD", (40, 50, 40, 15)):
        ws.column_dimensions[col].width = width

    ws2 = wb.create_sheet("Группы поиска")
    ws2.append(["Площадка", "URL группы"])
    ws2.append(["vk", "https://vk.com/example_group"])
    ws2.append(["telegram", "https://t.me/example_channel"])
    ws2.append(["ok", "https://ok.ru/group/123456"])
    ws2.append(["mymir", "https://my.mail.ru/group/example"])
    ws2.append(["rutube", "https://rutube.ru/channel/example"])
    for col, width in zip("AB", (15, 50)):
        ws2.column_dimensions[col].width = width

    buf = io.BytesIO()
    wb.save(buf)
    buf.seek(0)
    return StreamingResponse(
        buf,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="projects_template.xlsx"'},
    )
