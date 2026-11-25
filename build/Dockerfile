FROM gitlab.ctcmedia.ru:4567/gitlab/docker_images:werf-base-python3.13.7-alpine3.22-v1.0.0

ARG isdev
ARG APP_VERSION
ARG BASE_GITLAB_URL
ARG BASE_NEXUS_URL=https://nexus.ctcru.tech
ARG NEXUS_LOCAL_REPO_URI=/repository/pypi-hosted/simple/
ARG LAB_PRIVATE_TOKEN

ENV APP_VERSION=${APP_VERSION}

RUN apk update && \
  apk --no-cache add --virtual .runtime-deps mysql-client mariadb-connector-c && \
  apk --no-cache add --virtual .build-deps gcc musl-dev make mariadb-dev libffi-dev git g++ && \
  apk --no-cache add --virtual jpeg-dev zlib-dev freetype-dev lcms2-dev openjpeg-dev tiff-dev tk-dev tcl-dev curl && \
  pip install uv==0.13.5

ENV PYTHONPATH=/app \
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    UV_PYTHON_DOWNLOADS=never \
    UV_NO_CACHE=1 \
    PATH="/opt/venv/bin:$PATH"

WORKDIR /app

COPY pyproject.toml uv.lock /app/

# 1. Зависимости без проекта
RUN uv sync --group dev --no-install-project --verbose

# 2. Код и инфраструктура
ADD ./src /app/src
ADD ./build /app/build

# 3. Alembic — конфиг и миграции
COPY alembic/alembic.ini /app/alembic/alembic.ini
ADD ./alembic /app/alembic

# 4. Установка самого проекта (editable)
RUN uv sync --group dev --verbose
