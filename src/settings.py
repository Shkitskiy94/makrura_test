"""Централизованные настройки приложения (Pydantic v2 + pydantic-settings)."""
from functools import lru_cache

from pydantic import Field, computed_field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):

    APP_NAME: str
    DEBUG: bool
    API_TOKEN: str

    # --- База данных (docker-compose MySQL) ---
    MYSQL_DATABASE: str
    MYSQL_USER: str
    MYSQL_PASSWORD: str
    MYSQL_HOST: str
    MYSQL_PORT: int

    @computed_field
    @property
    def DATABASE_URL(self) -> str:
        return (
            f"mysql+aiomysql://{self.MYSQL_USER}:{self.MYSQL_PASSWORD}"
            f"@{self.MYSQL_HOST}:{self.MYSQL_PORT}/{self.MYSQL_DATABASE}"
            f"?charset=utf8mb4"
        )

    # --- HTTP-сервер (Granian) ---
    SERVER_BIND: str
    SERVER_PORT: int
    SERVER_WORKERS: int

    # --- Логирование ---
    LOG_LEVEL: str
    LOG_PRETTY: bool

    # --- Планировщик ---
    ENABLE_SCHEDULER: bool
    SCHEDULER_TZ: str
    CHECK_TIMES: str

    # --- Поиск / площадки ---
    VK_API_TOKEN: str
    VK_API_VERSION: str
    HTTP_TIME_OUT: float
    SEARCH_RATE_LIMIT_SECONDS: float
    FUZZY_MATCH_THRESHOLD: int = Field(ge=0, le=100)
    VIDEO_MIN_DURATION_SEC: int

    # --- Файлы ---
    UPLOAD_DIR: str
    EXPORT_DIR: str

    @property
    def times(self) -> list[tuple[int, int]]:
        result: list[tuple[int, int]] = []
        for part in self.CHECK_TIMES.split(","):
            part = part.strip()
            if not part:
                continue
            try:
                hh, mm = part.split(":")
                h, m = int(hh), int(mm)
            except ValueError as e:
                raise ValueError(f"Invalid CHECK_TIMES entry: {part!r}") from e
            if not (0 <= h < 24 and 0 <= m < 60):
                raise ValueError(f"Invalid time in CHECK_TIMES: {part!r}")
            result.append((h, m))
        return result


@lru_cache
def get_settings() -> Settings:
    return Settings()
