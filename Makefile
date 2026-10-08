UV ?= $(shell command -v uv 2>/dev/null || echo $$HOME/.local/bin/uv)

.PHONY: install lock lock-check test coverage migrations-check lint format typecheck check up deploy

install:
	$(UV) sync
	$(UV) run pre-commit install --hook-type pre-commit --hook-type commit-msg

lock:
	$(UV) lock

lock-check:
	$(UV) lock --check

test:
	$(UV) run python manage.py test

coverage:
	$(UV) run coverage run manage.py test
	$(UV) run coverage report

migrations-check:
	$(UV) run python manage.py makemigrations --check --dry-run

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .

typecheck:
	$(UV) run mypy .

check: lock-check lint typecheck migrations-check test

up:
	docker compose up --build

deploy:
	docker compose -f docker-compose.prod.yml up -d --build
