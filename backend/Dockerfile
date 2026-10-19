FROM python:3.14-slim-bookworm

RUN pip install --no-cache-dir uv==0.10.9
ENV UV_COMPILE_BYTECODE=1 UV_LINK_MODE=copy PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH" PYTHONPATH=/app/src
WORKDIR /app
COPY pyproject.toml uv.lock ./
RUN uv sync --locked --no-dev --no-install-project
COPY alembic.ini ./
COPY alembic ./alembic
COPY src ./src
RUN groupadd --gid 10001 app && useradd --uid 10001 --gid app --no-create-home app
USER 10001:10001
EXPOSE 8080
CMD ["sh", "-c", "exec uvicorn ai_stock_sentinel.api:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
