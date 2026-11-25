"""Авторизация API: статический токен в заголовке X-Auth-Token.

Токен хранится в env (API_TOKEN), по умолчанию 'hide_me_on_preprod'.
Проверяется для всех ручек /api/private/v1/*.
"""
import secrets

from fastapi import Header, HTTPException

from settings import get_settings


async def require_token(x_auth_token: str | None = Header(default=None)) -> None:
    expected = get_settings().API_TOKEN
    if not x_auth_token or not secrets.compare_digest(x_auth_token, expected):
        raise HTTPException(401, "Требуется валидный заголовок X-Auth-Token")
