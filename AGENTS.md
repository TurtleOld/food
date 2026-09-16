## Commands

- Install deps: `pip install -r requirements.txt`
- Install dev tooling: `pip install -r requirements-dev.txt` (adds mypy and django-stubs)
- Run tests: `python manage.py test`
- Lint and format: `python -m ruff check .` / `python -m ruff format --check .`
- Typecheck: `python -m mypy .`
- Run dev environment: `docker compose up --build` (app at http://localhost:8000)
- Deploy prod: copy `.env.example` to `.env`, fill it in, then `docker compose -f docker-compose.prod.yml up -d --build`

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles map to same-named labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: one `CONTEXT.md` plus `docs/adr/` at the repo root. See `docs/agents/domain.md`.
