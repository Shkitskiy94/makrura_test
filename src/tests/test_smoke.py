"""Базовые сценарии: CRUD проектов, группы поиска, ключевые фразы, импорт Excel."""
import io as _io
import openpyxl
from sqlalchemy import func, select

from app.models.entities import Project, ProjectSource, ProjectKeyword, SearchSource
from app.services.importer import import_projects_from_workbook


async def _create(client, title, **kw):
    r = await client.post("/api/private/v1/projects", json={"title": title, **kw})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_swagger(client):
    assert (await client.get("/swagger")).status_code == 200


async def test_create_and_list_projects(client):
    await _create(client, "Тест фильм")
    r = await client.get("/api/private/v1/projects")
    assert r.status_code == 200
    assert any(p["title"] == "Тест фильм" for p in r.json())


async def test_index_page(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert "Проекты" in r.text


async def test_findings_page(client):
    assert (await client.get("/findings")).status_code == 200


def test_scheduler_disabled_in_tests(monkeypatch):
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")


def _two_sheet_workbook():

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Проекты"
    ws.append(["Название проекта", "Ключевые фразы", "Описание", "Внешний id"])
    ws.append(["Тестовый сериал", "сериал онлайн; season 1", "опус", "X-1"])
    ws.append(["", "", "пустая строка", ""])
    ws2 = wb.create_sheet("Группы поиска")
    ws2.append(["Площадка", "URL группы"])
    ws2.append(["vk", "https://vk.com/test_group"])
    ws2.append(["telegram", "https://t.me/test_ch"])
    buf = _io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


async def test_excel_import(client, db_session):
    """Загрузка Excel (2 вкладки) -> проекты + группы + фразы; повтор импорт идемпотентен."""

    data = _two_sheet_workbook()

    res = await import_projects_from_workbook(data, db_session)
    assert res.created == 1
    assert res.skipped == 1
    assert res.sources_created == 2
    assert res.keywords_created == 2
    assert res.links_created == 2          # 1 проект × 2 группы

    proj = (await db_session.execute(
        select(Project).filter_by(title="Тестовый сериал"))).scalar_one()
    assert proj.external_id == "X-1"
    assert (await db_session.scalar(select(func.count(SearchSource.id)))) == 2

    # повторный импорт того же файла — без дублей и без изменений
    res2 = await import_projects_from_workbook(data, db_session)
    assert res2.created == 0
    assert res2.updated == 0
    assert res2.sources_created == 0
    assert res2.keywords_created == 0
    assert res2.links_created == 0
    assert (await db_session.scalar(select(func.count(Project.id)))) == 1


async def test_excel_import_via_http(client, tmp_path):
    """Тот же сценарий через эндпоинт POST /api/.../projects/import (2 вкладки)."""
    xlsx = tmp_path / "via_http.xlsx"
    xlsx.write_bytes(_two_sheet_workbook())

    with open(xlsx, "rb") as fh:
        r = await client.post(
            "/api/private/v1/projects/import",
            files={"file": ("via_http.xlsx", fh,
                            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
        )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["created"] == 1
    assert body["sources_created"] == 2
    assert body["keywords_created"] == 2
    assert body["links_created"] == 2
    titles = [p["title"] for p in (await client.get("/api/private/v1/projects")).json()]
    assert "Тестовый сериал" in titles


async def test_template_download(client):
    r = await client.get("/projects-template.xlsx")
    assert r.status_code == 200
    assert "spreadsheetml" in r.headers["content-type"]


async def test_sources_are_global(client):
    """Группы — глобальный справочник: создаются отдельно и действуют для всех проектов."""
    p1 = await _create(client, "Проект 1")
    p2 = await _create(client, "Проект 2")

    r = await client.post("/api/private/v1/sources",
                          json={"platform": "vk", "url": "https://vk.com/global_group"})
    assert r.status_code == 201
    sid = r.json()["id"]

    # обоим проектам подвязана одна и та же группа
    for pid in (p1, p2):
        srcs = (await client.get(f"/api/private/v1/projects/{pid}/sources")).json()
        assert [s["id"] for s in srcs] == [sid]

    # новый проект автоматически получает все существующие группы
    p3 = await _create(client, "Проект 3")
    assert len((await client.get(f"/api/private/v1/projects/{p3}/sources")).json()) == 1

    # дубль группы (с нормализацией / на конце) -> 409
    dup = await client.post("/api/private/v1/sources",
                            json={"platform": "vk", "url": "https://vk.com/global_group/"})
    assert dup.status_code == 409

    # удалить группу — исчезает у всех проектов
    assert (await client.delete(f"/api/private/v1/sources/{sid}")).status_code == 204
    for pid in (p1, p2, p3):
        assert (await client.get(f"/api/private/v1/projects/{pid}/sources")).json() == []


async def test_project_keywords_api(client):
    """Ключевые фразы: создание вместе с проектом, добавление, дедупликация."""
    r = await client.post("/api/private/v1/projects", json={
        "title": "Сериал К", "keywords": ["сезон 2", "serial k online", " сезон 2 "]})
    assert r.status_code == 201
    pid = r.json()["id"]
    assert r.json()["keywords"] == ["сезон 2", "serial k online"]  # дубль убран, пробелы нормализованы

    r2 = await client.post(f"/api/private/v1/projects/{pid}/keywords", json={"phrase": "смотреть сериал к"})
    assert r2.status_code == 201
    assert "смотреть сериал к" in r2.json()["keywords"]

    # регистронезависимый дубль -> 409
    assert (await client.post(f"/api/private/v1/projects/{pid}/keywords",
                              json={"phrase": "СЕЗОН 2"})).status_code == 409

    kws = (await client.get(f"/api/private/v1/projects/{pid}")).json()["keywords"]
    assert len(kws) == 3


async def test_sources_page(client):
    """Страница «Группы поиска»: форма создания + список."""
    await client.post("/api/private/v1/sources", json={"platform": "ok", "url": "https://ok.ru/group/777"})
    page = await client.get("/sources")
    assert page.status_code == 200
    assert "Создать группу поиска" in page.text
    assert "ok.ru/group/777" in page.text


async def test_project_page_shows_all_groups_and_keywords(client):
    """На странице проекта — его ключевые фразы и все группы поиска."""
    pid = await _create(client, "Проект П", keywords=["фраза а"])
    await client.post("/api/private/v1/sources", json={"platform": "vk", "url": "https://vk.com/page_group"})
    page = await client.get(f"/projects/{pid}")
    assert page.status_code == 200
    assert "vk.com/page_group" in page.text
    assert "фраза а" in page.text
    assert "Ключевые фразы" in page.text
