UV ?= $(shell command -v uv 2>/dev/null || echo $$HOME/.local/bin/uv)

.PHONY: install lock test lint format typecheck check up deploy

install:
	$(UV) sync

lock:
	$(UV) lock

test:
	$(UV) run python manage.py test

lint:
	$(UV) run ruff check .
	$(UV) run ruff format --check .

format:
	$(UV) run ruff format .

typecheck:
	$(UV) run mypy .

check: lint typecheck test

up:
	docker compose up --build

deploy:
	docker compose -f docker-compose.prod.yml up -d --build
