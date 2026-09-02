FROM python:3.13-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY --from=ghcr.io/astral-sh/uv:0.11.12 /uv /uvx /bin/
WORKDIR /app

COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

COPY src ./src
RUN uv sync --frozen --no-dev

RUN useradd --create-home appuser && mkdir -p /data && chown appuser:appuser /data
USER appuser

ENV SOA_DATABASE_URL=sqlite+aiosqlite:////data/stack_overflow.db
EXPOSE 8000
CMD ["/app/.venv/bin/uvicorn", "stack_overflow_analyzer.main:app", "--host", "0.0.0.0", "--port", "8000"]
