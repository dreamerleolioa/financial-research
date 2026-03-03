.PHONY: install run

PYTHON := ./venv/bin/python
PIP := ./venv/bin/pip

install:
	$(PIP) install -r requirements.txt

run:
	PYTHONPATH=src $(PYTHON) -m ai_stock_sentinel.main
