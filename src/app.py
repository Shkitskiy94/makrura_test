"""Точка входа приложения: переэкспортирует FastAPI-приложение."""
from app.main import app, create_app

__all__ = ["app", "create_app"]
