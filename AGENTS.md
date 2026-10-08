## Commands

- Install deps and tools: `make install` (`uv sync`; creates `.venv`)
- Run tests: `make test` (with coverage, gate 90%: `make coverage`)
- Lint and format check: `make lint` (`make format` to apply)
- Typecheck: `make typecheck`
- Lint + typecheck + tests: `make check`
- Run dev environment: `make up` (app at http://localhost:8000)
- Deploy prod: copy `.env.example` to `.env`, fill it in, then `make deploy`
- Add a dependency: `uv add <package>` / `uv add --dev <package>`

## Workflow

- One ticket, one branch, one PR. Write the PR description in Russian; the `pr` skill shapes its body.
- PR title and every commit follow Conventional Commits (`feat(scope): …`, `fix: …`): PRs are squash-merged and release-please builds versions and the changelog from these messages.
- A PR is ready when `make check` and `make coverage` are green.

## Conventions

- Django views are class-based (CBV, prefer Django generic views); do not add function-based views.
- Views stay thin: domain logic lives in plain functions in domain modules (e.g. `day_summary` in `core/diary.py`), so it is unit-testable without HTTP.
- Name code after the `CONTEXT.md` glossary: one English identifier per term (Продукт → `Product`, Запись дневника → `DiaryEntry`, Штрихкод → `Barcode`), never a synonym from its _Avoid_ list.
- Comments explain _why_: a constraint, a workaround, a non-obvious decision. Code that reads plainly carries no comment; make unclear code clearer by renaming or extracting before reaching for a comment.
- Docstrings are Google style, in Russian, on public modules, classes and functions whose purpose the name does not already tell (`ruff` checks the format, not the presence).
- Delete dead code instead of commenting it out; git keeps the history.
- Tests assert behaviour through the HTTP seam (`django.test.Client`): responses, headers, database state. They never assert template, partial or internal function names; htmx target `id`s are a contract and may be asserted.

## Agent skills

### Issue tracker

Issues and specs live as markdown files under `.scratch/`. See `docs/agents/issue-tracker.md`.

### Triage labels

Five canonical triage roles map to same-named labels. See `docs/agents/triage-labels.md`.

### Domain docs

Single-context layout: one `CONTEXT.md` plus `docs/adr/` at the repo root. See `docs/agents/domain.md`.
