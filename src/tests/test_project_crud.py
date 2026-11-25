"""Редактирование (название/описание) и удаление проекта."""


async def _create(client, title="Проект А", keywords=None):
    r = await client.post("/api/private/v1/projects", json={"title": title, "keywords": keywords or []})
    assert r.status_code == 201, r.text
    return r.json()["id"]


async def test_patch_title_and_description(client):
    pid = await _create(client, "Старое название")
    r = await client.patch(f"/api/private/v1/projects/{pid}",
                           json={"title": "Новое название", "description": "комментарий"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["title"] == "Новое название"
    assert body["description"] == "комментарий"
    # страница отображает новое название
    html = (await client.get(f"/projects/{pid}")).text
    assert "Новое название" in html and "комментарий" in html


async def test_patch_title_duplicate_rejected(client):
    await _create(client, "Занятее")
    pid2 = await _create(client, "Другое")
    r = await client.patch(f"/api/private/v1/projects/{pid2}", json={"title": "занятее"})
    assert r.status_code == 409


async def test_patch_empty_title_rejected(client):
    pid = await _create(client)
    r = await client.patch(f"/api/private/v1/projects/{pid}", json={"title": "   "})
    assert r.status_code == 422


async def test_delete_project_cascades(client):
    # группа существует до проекта -> проект подвязан ко всем группам
    await client.post("/api/private/v1/sources", json={"platform": "vk", "url": "https://vk.com/g1"})
    pid = await _create(client, "К удалению", keywords=["фраза 1", "фраза 2"])
    r = await client.delete(f"/api/private/v1/projects/{pid}")
    assert r.status_code == 204
    assert (await client.get(f"/api/private/v1/projects/{pid}")).status_code == 404
    # фразы и связи удалены каскадом — группы целы
    srcs = (await client.get("/api/private/v1/sources")).json()
    assert len(srcs) == 1
    # на главной кнопка удаления есть
    assert "btn-del-project" in (await client.get("/")).text


async def test_delete_project_404(client):
    assert (await client.delete("/api/private/v1/projects/99999")).status_code == 404


async def test_edit_form_on_project_page(client):
    pid = await _create(client)
    html = (await client.get(f"/projects/{pid}")).text
    assert 'id="edit-form"' in html and 'id="delete-btn"' in html
