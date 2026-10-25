import os
from pathlib import Path
import psycopg
from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[2]
SCHEMA_PATH = ROOT / "backend/sql/schema.sql"
load_dotenv(ROOT / ".env")


def main():
    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("Не задан DATABASE_URL")

    database_url = database_url.replace(
        "postgresql+psycopg://",
        "postgresql://",
        1,
    )
    schema = SCHEMA_PATH.read_text(encoding="utf-8")

    # Создание таблиц компаний, платежей и связей.
    with psycopg.connect(database_url) as conn:
        conn.execute(schema)

    from app.connect import engine
    from app.models import News
    News.__table__.create(bind=engine, checkfirst=True)
    print("Таблицы приложения готовы")


if __name__ == "__main__":
    main()