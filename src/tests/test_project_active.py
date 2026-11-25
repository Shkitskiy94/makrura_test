"""Тесты: читаемые ключевые фразы в таблице, переключение активности проекта."""


async def _mk_project_with_keywords(client):
    r = await client.post("/api/private/v1/projects", json={
        "title": "Пример: Название сериала",
        "keywords": ["сезон 2", "serial online", "смотреть сериал"],
    })
    assert r.status_code == 201
    return r.json()["id"]


async def test_index_shows_readable_keywords(client):
    """В таблице проектов фразы отображаются текстом, а не repr объектов."""
    pid = await _mk_project_with_keywords(client)
    html = (await client.get("/")).text
    assert "сезон 2; serial online; смотреть сериал" in html
    assert "ProjectKeyword object" not in html
    # колонка «открыть» не переносится
    assert 'class="col-open"' in html
    assert f"/projects/{pid}" in html


async def test_patch_project_toggle_active(client):
    pid = await _mk_project_with_keywords(client)
    sid = (await client.post("/api/private/v1/sources",
                             json={"platform": "vk", "url": "https://vk.com/g1"})).json()["id"]

    # активный проект подвязан к группе
    links = (await client.get(f"/api/private/v1/projects/{pid}/sources")).json()
    assert any(s["id"] == sid for s in links)

    # деактивировали — связи удалены, поиск останавливается
    r = await client.patch(f"/api/private/v1/projects/{pid}", json={"is_active": False})
    assert r.status_code == 200
    assert r.json()["is_active"] is False
    assert (await client.get(f"/api/private/v1/projects/{pid}/sources")).json() == []

    # активировали снова — проект подвязан ко всем группам
    r = await client.patch(f"/api/private/v1/projects/{pid}", json={"is_active": True})
    assert r.status_code == 200
    assert r.json()["is_active"] is True
    links = (await client.get(f"/api/private/v1/projects/{pid}/sources")).json()
    assert any(s["id"] == sid for s in links)


async def test_patch_project_new_source_links_on_reactivate(client):
    pid = await _mk_project_with_keywords(client)
    await client.patch(f"/api/private/v1/projects/{pid}", json={"is_active": False})
    # группа создана, пока проект неактивен — связей нет
    sid = (await client.post("/api/private/v1/sources",
                             json={"platform": "telegram", "url": "https://t.me/c1"})).json()["id"]
    assert (await client.get(f"/api/private/v1/projects/{pid}/sources")).json() == []
    # реактивация — подвязка ко всем (в т.ч. новым) группам
    await client.patch(f"/api/private/v1/projects/{pid}", json={"is_active": True})
    links = (await client.get(f"/api/private/v1/projects/{pid}/sources")).json()
    assert any(s["id"] == sid for s in links)


async def test_patch_project_404_and_title_guard(client):
    assert (await client.patch("/api/private/v1/projects/99999",
                               json={"is_active": False})).status_code == 404
    pid = await _mk_project_with_keywords(client)
    other = (await client.post("/api/private/v1/projects",
                               json={"title": "Другой фильм"})).json()["id"]
    # пустое название — 422
    assert (await client.patch(f"/api/private/v1/projects/{pid}",
                               json={"title": "   "})).status_code == 422
    # дубликат названия — 409
    assert (await client.patch(f"/api/private/v1/projects/{other}",
                               json={"title": "Пример: Название сериала"})).status_code == 409


async def test_index_has_active_toggle_markup(client):
    await _mk_project_with_keywords(client)
    html = (await client.get("/")).text
    assert "toggle-active" in html and ("is_active" in html or "PATCH" in html)
