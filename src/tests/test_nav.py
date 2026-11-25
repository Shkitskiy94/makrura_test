"""Подсветка активной кнопки в навбаре."""
import re


def active_links(html: str):
    return [href for href, cls in re.findall(r'<a href="([^"]+)" class="(active)?"', html) if cls == "active"]


async def test_nav_active_projects(client):
    r = await client.get("/")
    assert r.status_code == 200
    assert active_links(r.text) == ["/"]


async def test_nav_active_sources(client):
    r = await client.get("/sources")
    assert r.status_code == 200
    assert active_links(r.text) == ["/sources"]


async def test_nav_active_findings(client):
    r = await client.get("/findings")
    assert r.status_code == 200
    assert active_links(r.text) == ["/findings"]
