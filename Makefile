.PHONY: install db-up db-down migrate seed dev test check-db

install:
	uv sync --dev

db-up:
	docker compose up -d postgres

db-down:
	docker compose down

migrate:
	uv run alembic upgrade head

seed:
	uv run python -m scripts.seed

# run_api.py selects the Selector event loop required by async Psycopg on Windows.
dev:
	uv run python run_api.py

test:
	uv run pytest -q

check-db:
	uv run python -m scripts.check_database
