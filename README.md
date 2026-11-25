# Makrura

Внутренний инструмент автоматизированного поиска пиратского контента в соцсетях
(ВКонтакте, Telegram, Одноклассники, Мой Мир, Rutube) по ТЗ «Requirements Specification».

## Стек

| Компонент      | Технология                   |
|----------------|------------------------------|
| Язык           | Python 3.12+ (в проде — 3.14) |
| Веб-фреймворк  | FastAPI                      |
| Шаблоны        | Jinja2 (простой внутренний UI) |
| БД             | MySQL 8.x                    |
| ORM            | SQLAlchemy 2.x               |
| Миграции       | Alembic                      |
| Драйвер MySQL  | PyMySQL (синхронный)         |
| Планировщик    | APScheduler (2 запуска проверок в день) |
| Excel          | openpyxl (загрузка/выгрузка) |
| HTTP-клиент    | httpx                        |
| Нечёткий поиск | rapidfuzz                    |
| Валидация      | Pydantic v2 + pydantic-settings |
| Сервер         | Granian                             |
| Запуск         | Docker / docker compose      |

## Быстрый старт (Docker)

```bash
cp .env.local        # при необходимости отредактировать
чтобы поставить в env необходимо выполнить команду export $(grep -v "^#" .env.local | xargs)
docker make image clean run   # миграции применяются автоматически при старте

uv lock  - собрать зависимости
uv sync - синхронизировать
```

Интерфейс: http://localhost:8000 , Swagger-документация: http://localhost:8000/swagger


Тесты (используют SQLite in-memory):

```bash
PYTEST_DISABLE_PLUGIN_AUTOLOAD=1 pytest -q
```

Миграции:

```bash
alembic revision --autogenerate -m "comment"
alembic upgrade head
```

## Структура

```
Makefile               # команды: install / run / test / lint / build / up / down
build/                 # Dockerfile, docker-compose.yml, скрипты запуска и деплоя
config/config.yaml     # конфигурация приложения
docs/                  # документация (Requirements Specification, example.env)
src/                   # исходный код Python-пакета
  app/                 # FastAPI-приложение (main, config, db, api, models, services, templates, static)
  alembic/             # env.py + versions/ (миграции БД)
  tests/               # pytest (SQLite in-memory)
werf.yaml              # сборка и деплой через werf
werf-giterminism.yaml  # ограничения giterminism для werf
sonar-project.properties
pyproject.toml / uv.lock
```
