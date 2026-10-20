.PHONY: dev install import

PYTHON := .venv/bin/python

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install -r backend/requirements.txt

dev:
	$(PYTHON) -m uvicorn app.main:app --app-dir backend --reload --env-file .env

import:
	$(PYTHON) backend/scripts/import_csv.py --data-dir data/raw