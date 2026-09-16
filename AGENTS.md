## Commands

- Install deps and tools: `make install` (`uv sync`; creates `.venv`)
- Run tests: `make test`
- Lint and format check: `make lint` (`make format` to apply)
- Typecheck: `make typecheck`
- Lint + typecheck + tests: `make check`
- Run dev environment: `make up` (app at http://localhost:8000)
- Deploy prod: copy `.env.example` to `.env`, fill it in, then `make deploy`
- Add a dependency: `uv add <package>` / `uv add --dev <package>`

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles map to same-named labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: one `CONTEXT.md` plus `docs/adr/` at the repo root. See `docs/agents/domain.md`.
