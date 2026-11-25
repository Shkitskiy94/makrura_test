"""Импорт из Excel с двумя вкладками (по ТЗ):

Вкладка 1 — «Проекты»: список проектов.
| Название проекта | Ключевые фразы | Описание | Внешний id |
Ключевые фразы — через точку с запятой («фраза 1; фраза 2»).

Вкладка 2 — «Группы поиска»: искомые ресурсы.
| Площадка | URL группы |
Площадка: vk / telegram / ok / mymir / rutube (в т.ч. разговорные: вк, телеграм,
tg, одноклассники, мой мир, рутуб).

Любой проект автоматически ищется во всех группах — связи project_sources
создаёт app.services.linking.sync_all_links после импорта.

Обратная совместимость: если второй вкладкой является старый лист «Проекты и
группы» (колонки Площадка+URL или legacy-колонки ссылок по площадкам), он
обрабатывается как список групп; колонка «Ключевые фразы» распознаётся и на
первой вкладке старого однолистового формата.
"""
from __future__ import annotations

import logging
import re
import io
from dataclasses import dataclass, field
from typing import Any

from openpyxl import load_workbook
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import insert

from app.models.entities import ProjectSource
from app.models.entities import (
    Platform,
    Project,
    ProjectKeyword,
    SearchSource,
    SourceType,
)
from app.services.linking import link_all_projects_to_source, link_all_sources_to_project  # noqa: F401

log = logging.getLogger(__name__)

# --- маппинг заголовков колонок -> поля ---------------------------------------

PROJECT_SHEET_FIELDS: dict[str, list[str]] = {
    "title": ["название проекта", "название", "проект", "project", "title"],
    "keywords": ["ключевые фразы", "ключевые слова", "фразы", "ключи",
                 "keywords", "search terms", "keywords phrases"],
    "description": ["описание", "примечание", "notes", "description", "comment", "комментарий"],
    "external_id": ["внешний id", "id проекта", "внешний_id", "external id", "external_id"],
}

SOURCE_SHEET_FIELDS: dict[str, list[str]] = {
    "platform": ["площадка", "платформа", "platform", "соцсеть", "сеть"],
    "url": ["url группы", "url", "ссылка", "ccылка группы", "ссылка группы",
            "группа url", "url group", "group url", "link", "адрес", "url канала",
            "канал", "группа", "ссылка канала"],
    "source_type": ["тип", "тип группы", "type", "source type"],
    "title": ["название группы", "название канала", "название ресурса", "группа название"],
}

# legacy: колонки URL по площадкам на листе проектов (старый формат файлов)
LEGACY_URL_COLUMNS: dict[str, tuple[Platform, SourceType]] = {
    "группа вк": (Platform.VK, SourceType.GROUP),
    "группа vk": (Platform.VK, SourceType.GROUP),
    "vk": (Platform.VK, SourceType.GROUP),
    "вконтакте": (Platform.VK, SourceType.GROUP),
    "vk group": (Platform.VK, SourceType.GROUP),
    "ссылка вк": (Platform.VK, SourceType.GROUP),
    "группа_вк": (Platform.VK, SourceType.GROUP),
    "telegram": (Platform.TELEGRAM, SourceType.CHANNEL),
    "телеграм": (Platform.TELEGRAM, SourceType.CHANNEL),
    "telegram-канал": (Platform.TELEGRAM, SourceType.CHANNEL),
    "телеграм-канал": (Platform.TELEGRAM, SourceType.CHANNEL),
    "tg канал": (Platform.TELEGRAM, SourceType.CHANNEL),
    "канал tg": (Platform.TELEGRAM, SourceType.CHANNEL),
    "telegram channel": (Platform.TELEGRAM, SourceType.CHANNEL),
    "ссылка telegram": (Platform.TELEGRAM, SourceType.CHANNEL),
    "t.me": (Platform.TELEGRAM, SourceType.CHANNEL),
    "одноклассники": (Platform.OK, SourceType.GROUP),
    "ok": (Platform.OK, SourceType.GROUP),
    "группа ok": (Platform.OK, SourceType.GROUP),
    "ok.ru": (Platform.OK, SourceType.GROUP),
    "ссылка ok": (Platform.OK, SourceType.GROUP),
    "мой мир": (Platform.MYMIR, SourceType.GROUP),
    "mymir": (Platform.MYMIR, SourceType.GROUP),
    "my mail": (Platform.MYMIR, SourceType.GROUP),
    "ссылка мой мир": (Platform.MYMIR, SourceType.GROUP),
    "rutube": (Platform.RUTUBE, SourceType.GROUP),
    "рутуб": (Platform.RUTUBE, SourceType.GROUP),
    "ссылка rutube": (Platform.RUTUBE, SourceType.GROUP),
}

# разговорные названия площадок -> Platform
PLATFORM_ALIASES: dict[str, Platform] = {
    "vk": Platform.VK, "вк": Platform.VK, "вконтакте": Platform.VK,
    "vk.com": Platform.VK,
    "telegram": Platform.TELEGRAM, "телеграм": Platform.TELEGRAM,
    "телеграмм": Platform.TELEGRAM, "tg": Platform.TELEGRAM, "t.me": Platform.TELEGRAM,
    "ok": Platform.OK, "одноклассники": Platform.OK, "ok.ru": Platform.OK,
    "mymir": Platform.MYMIR, "мой мир": Platform.MYMIR, "my mail": Platform.MYMIR,
    "rutube": Platform.RUTUBE, "рутуб": Platform.RUTUBE, "rutube.ru": Platform.RUTUBE,
}

DEFAULT_SOURCE_TYPE: dict[Platform, SourceType] = {
    Platform.VK: SourceType.GROUP,
    Platform.TELEGRAM: SourceType.CHANNEL,
    Platform.OK: SourceType.GROUP,
    Platform.MYMIR: SourceType.GROUP,
    Platform.RUTUBE: SourceType.GROUP,
}

SOURCE_TYPE_ALIASES: dict[str, SourceType] = {
    "group": SourceType.GROUP, "группа": SourceType.GROUP, "сообщество": SourceType.GROUP,
    "channel": SourceType.CHANNEL, "канал": SourceType.CHANNEL,
    "album": SourceType.ALBUM, "альбом": SourceType.ALBUM, "медиа": SourceType.ALBUM,
    "user": SourceType.USER, "пользователь": SourceType.USER,
}


def _norm_header(value: object) -> str:
    s = str(value or "").strip().lower()
    return re.sub(r"\s+", " ", s)


def normalize_platform(value: str) -> Platform | None:
    """Приводит значение ячейки «Площадка» к Platform (или None, если не распознано)."""
    v = _norm_header(value).rstrip(".")
    if v in PLATFORM_ALIASES:
        return PLATFORM_ALIASES[v]
    for alias, plat in PLATFORM_ALIASES.items():
        if alias in ("ok", "vk", "tg"):
            continue
        if alias in v:
            return plat
    return None


def split_keywords(raw: str | None) -> list[str]:
    """«фраза 1; фраза 2, фраза 3» -> список непустых фраз (без дублей)."""
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


@dataclass
class ImportResult:
    created: int = 0             # новых проектов
    updated: int = 0             # обновлено существующих (по title)
    skipped: int = 0             # пустые/некорректные строки
    sources_created: int = 0     # созданных групп поиска
    keywords_created: int = 0    # добавленных ключевых фраз
    links_created: int = 0       # новых связей «проект ↔ группа»
    errors: list[str] = field(default_factory=list)
    total_rows: int = 0

    def as_dict(self) -> dict:
        return {
            "created": self.created,
            "updated": self.updated,
            "skipped": self.skipped,
            "sources_created": self.sources_created,
            "keywords_created": self.keywords_created,
            "links_created": self.links_created,
            "errors": self.errors,
            "total_rows": self.total_rows,
        }


# --- чтение листов ------------------------------------------------------------

def _read_sheet(ws) -> tuple[list[str], list[list[str]]]:
    """Возвращает заголовки (первая непустая строка) и данные (значения ячеек)."""
    rows = ws.iter_rows(values_only=True)
    header: list[str] = []
    offset = 0
    for row in rows:
        offset += 1
        if any(str(c).strip() for c in row if c is not None):
            header = [_norm_header(c) for c in row]
            break
    data: list[list[str]] = []
    for row in rows:
        data.append([str(c).strip() if c is not None else "" for c in row])
    return header, data


def _map_columns(header: list[str], fields: dict[str, list[str]]) -> dict[str, int]:
    idx: dict[str, int] = {}
    for i, h in enumerate(header):
        if not h:
            continue
        for name, aliases in fields.items():
            if name in idx:
                continue
            if h in [a.strip() for a in aliases]:
                idx[name] = i
                break
    return idx


def _find_sheets(wb) -> tuple[object | None, object | None]:
    """Определяет лист проектов и лист групп по заголовкам (по названию — нет)."""
    proj_ws = src_ws = None
    for ws in wb.worksheets:
        header, _ = _read_sheet(ws)
        names = set(header)
        proj_fields = _map_columns(header, PROJECT_SHEET_FIELDS)
        src_fields = _map_columns(header, SOURCE_SHEET_FIELDS)
        has_title = "title" in proj_fields
        has_kw = "keywords" in proj_fields
        has_legacy_urls = bool(names & set(LEGACY_URL_COLUMNS))
        is_src = "platform" in src_fields and "url" in src_fields
        if is_src and not (has_title or has_legacy_urls):
            if src_ws is None:
                src_ws = ws
        elif has_title and not is_src:
            if proj_ws is None:
                proj_ws = ws
        elif has_legacy_urls and not is_src:
            # старый однолистовой «Проекты и группы»: проекты + ссылки по площадкам
            if proj_ws is None:
                proj_ws = ws
    return proj_ws, src_ws


# --- запись в БД ---------------------------------------------------------------

def _clean(value: str | None) -> str | None:
    v = (value or "").strip()
    return v or None


async def _get_or_create_project(
    title: str,
    existing: dict[str, Project],
    db: AsyncSession,
    result: ImportResult,
    extra: dict[str, str | None],
) -> Project:
    """Находит проект по названию (регистронезависимо) или создаёт новый."""
    key = title.strip().casefold()
    project = existing.get(key)
    if project is None:
        # Регистронезависимый поиск делаем на стороне Python: LOWER() в SQLite
        # не работает с кириллицей, а коллация MySQL может отличаться.
        for cand in (await db.execute(select(Project))).scalars():
            if cand.title.strip().casefold() == key:
                project = cand
                break
    if project is None:
        project = Project(title=title, **{k: v for k, v in extra.items() if v})
        db.add(project)
        await db.flush()
        result.created += 1
    else:
        changed = False
        for k, v in extra.items():
            if v is not None and getattr(project, k) != v:
                setattr(project, k, v)
                changed = True
        if changed:
            result.updated += 1
    existing[key] = project
    return project


def _add_keywords(project: Project, phrases: list[str], db: AsyncSession,
                  result: ImportResult, cache: set[tuple[int, str]]) -> None:
    for phrase in phrases:
        norm = ProjectKeyword.normalize_phrase(phrase)
        if not norm or norm == ProjectKeyword.normalize_phrase(project.title):
            continue  # название и так участвует в поиске
        key = (project.id, norm)
        if key in cache:
            continue
        kw = ProjectKeyword(project_id=project.id, phrase=phrase)
        kw.compute_phrase_hash()
        db.add(kw)
        cache.add(key)
        result.keywords_created += 1


async def _add_source(platform: Platform, url: str, db: AsyncSession, result: ImportResult,
                cache: set[tuple[str, str]], source_type: SourceType | None = None,
                title: str | None = None) -> None:
    """Добавляет группу поиска (площадка+URL) в глобальный справочник, без дублей."""
    norm_url = url.strip().rstrip("/")
    key = (platform.value, norm_url.lower())
    if key in cache:
        return
    src = SearchSource(
        platform=platform,
        url=norm_url,
        source_type=source_type or DEFAULT_SOURCE_TYPE[platform],
        title=title,
    )
    src.compute_url_hash()
    db.add(src)
    await db.flush()
    cache.add(key)
    result.sources_created += 1


async def _sync_all_links_async(db: AsyncSession) -> int:
    """Асинхронная полная синхронизация связей «активный проект × каждая группа».

    Идемпотентна: добавляет только недостающие связи. Возвращает их число.
    """

    active_ids = list((await db.execute(
        select(Project.id).where(Project.is_active.is_(True)))).scalars())
    source_ids = list((await db.execute(select(SearchSource.id))).scalars())
    if not active_ids or not source_ids:
        return 0
    have = set((await db.execute(
        select(ProjectSource.project_id, ProjectSource.source_id))).all())
    new_pairs = [
        {"project_id": pid, "source_id": sid}
        for pid in active_ids for sid in source_ids
        if (pid, sid) not in have
    ]
    if new_pairs:
        await db.execute(insert(ProjectSource), new_pairs)
    log.info("_sync_all_links_async: added %d links", len(new_pairs))
    return len(new_pairs)


async def import_projects_from_workbook(source: Any, db: AsyncSession) -> ImportResult:
    """Импортирует две вкладки Excel (проекты, группы поиска) в БД.

    Идемпотентно: проекты — по названию, группы — по (площадка, нормализованный URL),
    фразы — по нормализованному тексту. После импорта связи «проект × группа»
    синхронизируются: все активные проекты ищутся во всех группах.
    """
    result = ImportResult()

    # source — байты загруженного файла (или путь для офлайн-скриптов)
    if isinstance(source, (bytes, bytearray)):

        wb = load_workbook(filename=io.BytesIO(bytes(source)), read_only=True, data_only=True)
    else:
        wb = load_workbook(filename=source, read_only=True, data_only=True)
    proj_ws, src_ws = _find_sheets(wb)
    if proj_ws is None and src_ws is None:
        wb.close()
        result.errors.append(
            "В файле не найдено ни одной распознанной вкладки. "
            "Нужны: «Проекты» (колонка «Название проекта», опц. «Ключевые фразы») "
            "и «Группы поиска» (колонки «Площадка», «URL группы»)."
        )
        return result

    proj_header, proj_data = _read_sheet(proj_ws) if proj_ws is not None else ([], [])
    src_header, src_data = _read_sheet(src_ws) if src_ws is not None else ([], [])
    wb.close()

    proj_cols = _map_columns(proj_header, PROJECT_SHEET_FIELDS)
    legacy_cols = {
        i: LEGACY_URL_COLUMNS[h] for i, h in enumerate(proj_header)
        if h in LEGACY_URL_COLUMNS
    }
    src_cols = _map_columns(src_header, SOURCE_SHEET_FIELDS)

    if "title" not in proj_cols and proj_ws is not None and not legacy_cols:
        result.errors.append(
            f"На вкладке «{proj_ws.title}» не найдена обязательная колонка "
            f"«Название проекта». Заголовки: {[h for h in proj_header if h]}"
        )
    if src_ws is not None and not {"platform", "url"} <= set(src_cols):
        result.errors.append(
            f"На вкладке «{src_ws.title}» не найдены колонки «Площадка»/«URL группы». "
            f"Заголовки: {[h for h in src_header if h]}"
        )
        src_data = []
    if result.errors:
        return result

    existing_projects: dict[str, Project] = {}
    kw_cache: set[tuple[int, str]] = set()
    src_cache: set[tuple[str, str]] = set()

    # существующие ключевые фразы — в кэш дедупликации (по нормализованному тексту)
    for k in (await db.execute(select(ProjectKeyword))).scalars():
        kw_cache.add((k.project_id, ProjectKeyword.normalize_phrase(k.phrase)))

    # существующие группы — в кэш дедупликации
    for plat_val, saved_url in (await db.execute(
        select(SearchSource.platform, SearchSource.url)
    )).all():
        src_cache.add((plat_val.value, saved_url.strip().rstrip("/").lower()))

    # --- Вкладка 2: группы поиска --------------------------------------------
    for line_no, row in enumerate(src_data, start=1):
        if all(not c for c in row):
            continue
        result.total_rows += 1
        get = lambda name: row[src_cols[name]] if name in src_cols and src_cols[name] < len(row) else ""
        plat_raw = _clean(get("platform"))
        url = _clean(get("url"))
        platform = normalize_platform(plat_raw) if plat_raw else None
        if platform is None and url:
            platform = normalize_platform(url)  # домен в URL как подсказка
        if platform is None or not url:
            result.skipped += 1
            result.errors.append(
                f"Вкладка «{src_ws.title}», строка {line_no}: не распознана площадка "
                f"'{plat_raw}' или пуст URL — пропущено"
            )
            continue
        stype = None
        raw_type = _clean(get("source_type")) if "source_type" in src_cols else None
        if raw_type:
            stype = SOURCE_TYPE_ALIASES.get(_norm_header(raw_type))
        stitle = _clean(get("title")) if "title" in src_cols else None
        await _add_source(platform, url, db, result, src_cache, source_type=stype, title=stitle)

    # --- Вкладка 1: проекты ----------------------------------------------------
    for line_no, row in enumerate(proj_data, start=1):
        if all(not c for c in row):
            continue
        result.total_rows += 1
        get = lambda name: row[proj_cols[name]] if name in proj_cols and proj_cols[name] < len(row) else ""
        title = _clean(get("title"))
        if not title:
            result.skipped += 1
            result.errors.append(f"Вкладка «{proj_ws.title}», строка {line_no}: пустое название — пропущено")
            continue

        extra = {
            "external_id": _clean(get("external_id")),
            "description": _clean(get("description")),
        }
        project = await _get_or_create_project(title, existing_projects, db, result, extra)

        phrases = split_keywords(get("keywords") if "keywords" in proj_cols else "")
        _add_keywords(project, phrases, db, result, kw_cache)

        # legacy-формат: URL-колонки по площадкам прямо на листе проектов
        for idx, (platform, stype) in legacy_cols.items():
            lurl = _clean(row[idx]) if idx < len(row) else None
            if lurl:
                await _add_source(platform, lurl, db, result, src_cache, source_type=stype)

    # --- подвязка: любой проект ищется во всех группах --------------------------
    result.links_created = await _sync_all_links_async(db)

    await db.commit()
    log.info(
        "Import finished: %d projects created, %d updated, %d skipped, "
        "%d sources, %d keywords, %d links",
        result.created, result.updated, result.skipped,
        result.sources_created, result.keywords_created, result.links_created,
    )
    return result
