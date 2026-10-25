.PHONY: dev install import parse-news init-agent-db init-rag-db index-bank index-news db-init db-seed db-setup db-check
DATA_DIR ?= data/raw
RAG_STORE ?= postgres
PYTHON := .venv/bin/python

ifneq (,$(wildcard .env))
include .env
export
endif

install:
	python3 -m venv .venv
	$(PYTHON) -m pip install -r backend/requirements.txt

dev:
	$(PYTHON) -m uvicorn app.agent.api:create_app \
		--factory \
		--app-dir backend \
		--reload \
		--env-file .env

import:
	$(PYTHON) backend/scripts/import_csv.py --data-dir data/raw

parse-news:
	PYTHONPATH=backend $(PYTHON) backend/scripts/fetch_news.py

init-agent-db:
	PYTHONPATH=backend $(PYTHON) -m app.agent.persistence init-db

init-rag-db:
	PYTHONPATH=backend $(PYTHON) -m app.rag.cli init-db --store ${RAG_STORE}

index-bank:
	PYTHONPATH=backend $(PYTHON) -m app.rag.cli index-bank \
		--store postgres \
		--input backend/app/rag/data/bank_corpus.json

index-news:
	PYTHONPATH=backend $(PYTHON) -m app.rag.cli index-db-news \
		--store postgres

# Создать таблицы без удаления существующих данных.
db-init:
	PYTHONPATH=backend $(PYTHON) backend/scripts/init_db.py
	$(MAKE) init-agent-db
	$(MAKE) init-rag-db

db-seed:
	$(PYTHON) backend/scripts/import_csv.py --data-dir $(DATA_DIR)

db-setup:
	$(MAKE) db-init
	$(MAKE) db-seed

db-check:
	PYTHONPATH=backend $(PYTHON) -c 'from app.agent.persistence import counts; print(counts())'
