"""JSON API (FastAPI + Pydantic v2), полностью асинхронный.

Все ручки — под префиксом /api/private/v1 и требуют заголовок
'X-Auth-Token' (токен из env API_TOKEN).

Модель: проекты (название + ключевые фразы) ищутся во ВСЕХ группах поиска.
Группа создаётся отдельно (площадка + URL) и автоматически подвязывается
ко всем активным проектам; проект — ко всем существующим группам.
"""
import hashlib
import re
from pathlib import Path

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import delete, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from app.auth import require_token
from app.db import get_db
from app.models.entities import (
    Finding,
    FindingStatus,
    Platform,
    Project,
    ProjectKeyword,
    ProjectSource,
    SearchSource,
    SourceType,
)
from app.services.importer import import_projects_from_workbook
from app.services.linking import link_all_projects_to_source, link_all_sources_to_project
from app.services.search import run_all_checks
from app.api.pages import sort_spec


router = APIRouter(tags=["api"], dependencies=[Depends(require_token)])

ALLOWED_EXTENSIONS = {".xlsx", ".xlsm"}


def _split_phrases(raw: str | None) -> list[str]:
    """«фраза 1; фраза 2» -> список непустых уникальных фраз."""
    parts = re.split(r"[;\n\r]+", raw or "")
    seen: set[str] = set()
    out: list[str] = []
    for p in parts:
        phrase = " ".join(p.split())
        key = phrase.casefold()
        if phrase and key not in seen:
            seen.add(key)
            out.append(phrase)
    return out


# --- схемы ---------------------------------------------------------------------

class KeywordIn(BaseModel):
    phrase: str = Field(min_length=1, max_length=512)

    @field_validator("phrase")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = " ".join(v.split())
        if not v:
            raise ValueError("Ключевая фраза не может быть пустой")
        return v


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=512)
    description: str | None = None
    external_id: str | None = None
    keywords: list[str] = []   # ключевые фразы (добавляются к названию при поиске)

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Название не может быть пустым")
        return v


class ProjectOut(BaseModel):
    id: int
    title: str
    description: str | None = None
    external_id: str | None = None
    is_active: bool
    keywords: list[str] = []

    model_config = {"from_attributes": True}


class SourceCreate(BaseModel):
    platform: Platform
    url: str = Field(min_length=1, max_length=1024)
    source_type: SourceType | None = None

    @field_validator("url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if not v:
            raise ValueError("URL не может быть пустым")
        return v


class SourceOut(BaseModel):
    id: int
    platform: Platform
    url: str
    source_type: SourceType
    title: str | None = None

    model_config = {"from_attributes": True}


class FindingPatch(BaseModel):
    status: FindingStatus


class ProjectPatch(BaseModel):
    is_active: bool | None = None
    title: str | None = None
    description: str | None = None

    @field_validator("title")
    @classmethod
    def _strip_title(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        if not v:
            raise ValueError("Название не может быть пустым")
        return v


def _project_out(p: Project) -> dict:
    return {
        "id": p.id,
        "title": p.title,
        "description": p.description,
        "external_id": p.external_id,
        "is_active": p.is_active,
        "keywords": [k.phrase for k in p.keywords],
    }


# --- проекты -------------------------------------------------------------------

@router.get("/projects", response_model=list[ProjectOut])
async def list_projects(
    db: AsyncSession = Depends(get_db),
    sort: str = Query("title", description="Сортировка: id или title (применяется одна)"),
    dirn: str = Query("asc", alias="dir", description="asc|desc"),
):
    order_col, _s, _d = sort_spec(sort, dirn, "title", {"id": Project.id, "title": Project.title})
    rows = (await db.execute(
        select(Project).options(selectinload(Project.keywords)).order_by(order_col)
    )).scalars().all()
    return [_project_out(p) for p in rows]


@router.post("/projects", response_model=ProjectOut, status_code=201)
async def create_project(body: ProjectCreate, db: AsyncSession = Depends(get_db)):
    """Добавить проект: название + ключевые фразы.

    Группы поиска не выбираются — проект сразу подвязывается ко всем
    существующим группам (любой проект ищется во всех группах).
    """
    title = body.title.strip()
    key = title.casefold()
    all_titles = (await db.execute(select(Project.id, Project.title))).all()
    dup = next((t for _id, t in all_titles if t.strip().casefold() == key), None)
    if dup:
        raise HTTPException(409, f"Проект «{dup}» уже существует")
    project = Project(
        title=title,
        description=body.description or None,
        external_id=body.external_id or None,
    )
    db.add(project)
    await db.flush()
    for phrase in _split_phrases(";".join(body.keywords)):
        kw = ProjectKeyword(project_id=project.id, phrase=phrase)
        kw.compute_phrase_hash()
        db.add(kw)
    await link_all_sources_to_project(db, project.id)
    await db.commit()
    return await _project_out_reloaded(db, project.id)


async def _project_out_reloaded(db: AsyncSession, project_id: int) -> dict:
    """_project_out с гарантированно загруженными keywords (без async lazy-load).

    expire_on_commit=False, поэтому список keywords в памяти может быть
    устаревшим после добавления фраз — делаем explicit reload.
    """
    p = (await db.execute(
        select(Project).options(selectinload(Project.keywords)).where(Project.id == project_id)
    )).scalar_one()
    await db.refresh(p, ["keywords"])
    return _project_out(p)


@router.get("/projects/{project_id}", response_model=ProjectOut)
async def get_project(project_id: int, db: AsyncSession = Depends(get_db)):
    project = (await db.execute(
        select(Project).options(selectinload(Project.keywords)).where(Project.id == project_id)
    )).scalar_one_or_none()
    if project is None:
        raise HTTPException(404, "Проект не найден")
    return _project_out(project)


@router.delete("/projects/{project_id}", status_code=204)
async def delete_project(project_id: int, db: AsyncSession = Depends(get_db)):
    """Удалить проект вместе с его ключевыми фразами, связями и находками.

    Каскад по БД (ondelete=CASCADE): project_keywords, project_sources, findings.
    """
    project = await db.get(Project, project_id)
    if not project:
        raise HTTPException(404, "Проект не найден")
    await db.delete(project)
    await db.commit()


@router.patch("/projects/{project_id}", response_model=ProjectOut)
async def patch_project(project_id: int, body: ProjectPatch, db: AsyncSession = Depends(get_db)):
    """Редактировать проект: активность (и при желании название).

    При деактивации связи с группами поиска удаляются (поиск по проекту
    останавливается); при активации — проект снова подвязывается ко всем
    существующим группам.
    """
    project = (await db.execute(
        select(Project).options(selectinload(Project.keywords)).where(Project.id == project_id)
    )).scalar_one_or_none()
    if project is None:
        raise HTTPException(404, "Проект не найден")

    changed = False
    if body.title is not None and body.title != project.title:
        key = body.title.casefold()
        all_titles = (await db.execute(select(Project.id, Project.title))).all()
        dup = next((t for pid, t in all_titles if pid != project_id and t.strip().casefold() == key), None)
        if dup:
            raise HTTPException(409, f"Проект «{dup}» уже существует")
        project.title = body.title
        changed = True
    if body.description is not None and body.description != (project.description or ""):
        project.description = body.description or None
        changed = True
    if body.is_active is not None and body.is_active != project.is_active:
        project.is_active = body.is_active
        changed = True
        if body.is_active:
            await link_all_sources_to_project(db, project.id)
        else:
            await db.execute(
                delete(ProjectSource).where(ProjectSource.project_id == project.id)
            )
    if changed:
        await db.commit()
    return await _project_out_reloaded(db, project_id)


# --- ключевые фразы проекта ------------------------------------------------------

@router.post("/projects/{project_id}/keywords", response_model=ProjectOut, status_code=201)
async def add_keyword(project_id: int, body: KeywordIn, db: AsyncSession = Depends(get_db)):
    """Добавить ключевую фразу проекта (используется при поиске наравне с названием)."""
    project = (await db.execute(
        select(Project).options(selectinload(Project.keywords)).where(Project.id == project_id)
    )).scalar_one_or_none()
    if project is None:
        raise HTTPException(404, "Проект не найден")
    norm = ProjectKeyword.normalize_phrase(body.phrase)
    if any(ProjectKeyword.normalize_phrase(k.phrase) == norm for k in project.keywords):
        raise HTTPException(409, "Такая ключевая фраза уже добавлена")
    kw = ProjectKeyword(project_id=project.id, phrase=body.phrase)
    kw.compute_phrase_hash()
    db.add(kw)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Такая ключевая фраза уже добавлена")
    return await _project_out_reloaded(db, project_id)


@router.delete("/keywords/{keyword_id}", status_code=204)
async def delete_keyword(keyword_id: int, db: AsyncSession = Depends(get_db)):
    kw = await db.get(ProjectKeyword, keyword_id)
    if not kw:
        raise HTTPException(404, "Ключевая фраза не найдена")
    await db.delete(kw)
    await db.commit()


# --- группы поиска (глобальный справочник: площадка + URL) -----------------------

@router.get("/sources", response_model=list[SourceOut])
async def list_sources(db: AsyncSession = Depends(get_db)):
    """Все группы поиска (применяются ко всем активным проектам)."""
    return (await db.execute(
        select(SearchSource).order_by(SearchSource.platform, SearchSource.id)
    )).scalars().all()


@router.post("/sources", response_model=SourceOut, status_code=201)
async def create_source(body: SourceCreate, db: AsyncSession = Depends(get_db)):
    """Создать группу поиска: площадка (из заданных) + URL.

    Группа сразу подвязывается ко всем активным проектам.
    """
    url_hash = hashlib.md5(SearchSource.normalize_url(body.url).encode()).hexdigest()
    dup = (await db.execute(
        select(SearchSource).where(
            SearchSource.platform == body.platform,
            SearchSource.url_hash == url_hash,
        )
    )).scalar_one_or_none()
    if dup:
        raise HTTPException(409, f"Такая группа уже существует (id={dup.id})")
    stype = body.source_type or (
        SourceType.CHANNEL if body.platform == Platform.TELEGRAM else SourceType.GROUP
    )
    src = SearchSource(platform=body.platform, url=body.url, source_type=stype)
    src.compute_url_hash()
    db.add(src)
    await db.flush()
    await link_all_projects_to_source(db, src.id)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(409, "Такая группа уже существует")
    await db.refresh(src)
    return src


@router.delete("/sources/{source_id}", status_code=204)
async def delete_source(source_id: int, db: AsyncSession = Depends(get_db)):
    src = await db.get(SearchSource, source_id)
    if not src:
        raise HTTPException(404, "Группа не найдена")
    await db.delete(src)
    await db.commit()


@router.get("/projects/{project_id}/sources", response_model=list[SourceOut])
async def list_project_sources(project_id: int, db: AsyncSession = Depends(get_db)):
    """Группы, в которых ищется проект (для активного — все группы)."""
    if not await db.get(Project, project_id):
        raise HTTPException(404, "Проект не найден")
    rows = (await db.execute(
        select(SearchSource)
        .join(ProjectSource, ProjectSource.source_id == SearchSource.id)
        .where(ProjectSource.project_id == project_id)
        .order_by(SearchSource.platform, SearchSource.id)
    )).scalars().all()
    return rows


# --- импорт / проверки / находки ---------------------------------------------------

@router.post("/projects/import")
async def import_projects(file: UploadFile = File(...), db: AsyncSession = Depends(get_db)):
    """Загрузка Excel (вкладки «Проекты» и «Группы поиска») -> запись в БД."""
    ext = Path(file.filename or "").suffix.lower()
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(415, f"Неподдерживаемый формат '{ext}'. Нужен .xlsx или .xlsm")

    data = await file.read()   # читаем весь файл в память (async) — файл небольшой
    result = await import_projects_from_workbook(data, db)
    return result.as_dict()


@router.post("/checks/run")
async def trigger_check(background: BackgroundTasks):
    """Ручной запуск проверки (вне расписания)."""
    background.add_task(run_all_checks)
    return {"ok": True, "queued": True}


@router.patch("/findings/{finding_id}")
async def patch_finding(finding_id: int, body: FindingPatch, db: AsyncSession = Depends(get_db)):
    f = await db.get(Finding, finding_id)
    if not f:
        raise HTTPException(404, "Finding not found")
    f.status = FindingStatus(body.status)
    await db.commit()
    return {"ok": True}
