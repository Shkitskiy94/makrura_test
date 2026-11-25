#!/bin/sh

alembic -c alembic/alembic.ini upgrade head &&
exec uv run python -m src.run

