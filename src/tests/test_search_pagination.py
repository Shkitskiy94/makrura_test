"""Поиск по проектам/находкам и пагинация таблиц."""


async def _make_projects(client, n, prefix="Сериал"):
    for i in range(n):
        r = await client.post("/api/private/v1/projects", json={"title": f"{prefix} {i:03d}"})
        assert r.status_code in (200, 201), r.text


async def test_search_no_results(client):
    await _make_projects(client, 1)
    r = await client.get("/", params={"q": "нет-такого"})
    assert r.status_code == 200
    assert "Ничего не найдено" in r.text


async def test_pagination(client):
    from app.api.pages import PAGE_SIZE

    await _make_projects(client, PAGE_SIZE + 7)  # 57 проектов → 2 страницы
    r = await client.get("/")
    assert r.status_code == 200
    assert 'class="pagination"' in r.text
    assert "стр. 1 из 2" in r.text
    assert "Всего проектов: 57" in r.text
    # первая страница: только PAGE_SIZE строк таблицы
    assert r.text.count("открыть →") == PAGE_SIZE

    r2 = await client.get("/", params={"page": 2})
    assert "стр. 2 из 2" in r2.text
    assert r2.text.count("открыть →") == 7


async def test_page_out_of_range_shows_empty_but_200(client):
    await _make_projects(client, 2)
    r = await client.get("/", params={"page": 99})
    assert r.status_code == 200