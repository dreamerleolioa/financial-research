.PHONY: install run run-api test

PYTHON := ./venv/bin/python
PIP := ./venv/bin/pip

install:
	$(PIP) install -r requirements.txt

run:
	PYTHONPATH=src $(PYTHON) -m ai_stock_sentinel.main

run-api:
	PYTHONPATH=src $(PYTHON) -m uvicorn ai_stock_sentinel.api:app --reload

test:
	PYTHONPATH=src $(PYTHON) -m pytest -q
