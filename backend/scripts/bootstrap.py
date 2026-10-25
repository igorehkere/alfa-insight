"""Prepare a new deployment; preserve an existing database on subsequent runs."""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[2]


def run(*arguments: str, optional: bool = False) -> None:
    try:
        subprocess.run([sys.executable, *arguments], cwd=ROOT, check=True,
                       timeout=180 if optional else None)
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired):
        if not optional:
            raise
        print("RSS недоступны: запуск продолжается. Повторите загрузку позже.", flush=True)


def seed_companies(data_dir: Path, dsn: str) -> None:
    import psycopg
    from scripts.import_csv import import_data

    tables = ("companies", "source_company_rows", "payments", "person_company_links")
    with psycopg.connect(dsn) as connection:
        counts = {table: connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
                  for table in tables}
    if counts["companies"]:
        print("Компании уже загружены; существующие данные сохранены.", flush=True)
    elif any(counts.values()):
        raise RuntimeError("Связанные таблицы заполнены при пустой companies. Автоимпорт остановлен для сохранения данных.")
    else:
        import_data(data_dir, dsn)


def main() -> None:
    if os.getenv("RAG_SEARCH_MODE", "fts") != "fts":
        raise RuntimeError("Этот способ развёртывания рассчитан на RAG_SEARCH_MODE=fts")
    dsn = os.environ["DATABASE_URL"].replace("postgresql+psycopg://", "postgresql://", 1)
    run("backend/scripts/init_db.py")
    run("-m", "app.agent.persistence", "init-db")
    run("-m", "app.rag.cli", "init-db", "--store", "fts")
    seed_companies(ROOT / "data/raw", dsn)

    from app.agent.persistence import counts
    if not counts()["news"] and os.getenv("FETCH_NEWS_ON_INIT", "true").lower() == "true":
        run("backend/scripts/fetch_news.py", optional=True)

    # Upsert the reviewed corpus on every deployment so image updates reach the index.
    run("-m", "app.rag.cli", "index-bank", "--store", "fts",
        "--input", "backend/app/rag/data/reviewed_corpus.json")
    run("-m", "app.rag.cli", "index-db-news", "--store", "fts")
    print("База и поисковые индексы готовы.", flush=True)


if __name__ == "__main__":
    main()
