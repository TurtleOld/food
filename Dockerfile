FROM python:3.13-slim

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

RUN useradd --create-home --uid 1000 app

WORKDIR /app

RUN chown app:app /app

COPY --chown=app:app pyproject.toml uv.lock ./

USER app

RUN uv sync --frozen --no-dev

COPY --chown=app:app . .

ENV PATH="/app/.venv/bin:$PATH"

EXPOSE 8000

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]
