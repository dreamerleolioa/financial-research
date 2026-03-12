.PHONY: install run run-api test

install:
	uv sync

run:
	uv run python -m ai_stock_sentinel.main

run-api:
	set -a && . ./.env && set +a && PYTHONPATH=src uv run uvicorn ai_stock_sentinel.api:app --reload

test:
	uv run pytest -q
