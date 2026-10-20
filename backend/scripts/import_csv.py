from __future__ import annotations

import argparse
import csv
import os
from pathlib import Path

import psycopg
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATA_DIR = Path("data/raw")

COMPANY_COLUMNS = (
    "SYNTHETIC_U", "ORG_TYPE", "OKFS_NAME", "COMPANY_AGE_YEARS_GRADATION",
    "ECO_MARK_LABEL", "MAINOKVED_CODE", "MAINOKVED_NAME", "NEGATIVEMARK",
    "AMOUNT_WORKERS_FNS_GRADATION", "AMOUNT_WORKERS_SPARK_GRADATION",
    "CL_TYPE", "CL_PRODUCT_RKO_MARK", "CL_PRODUCT_GAR_MARK",
    "CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "CL_PRODUCT_DEP_MARK",
    "CL_PRODUCT_ZPP_MARK", "FTS_FLAG", "FTS_COUNTRY_NAME", "FACTORING_MARK",
    "REVENUE_COMPANY", "GROUP_ID", "CITY", "REGION",
)

IMPORTS = (
    (
        "df_Uid_data_hack.csv",
        "source_company_rows",
        COMPANY_COLUMNS,
        tuple(f'"{column}"' for column in COMPANY_COLUMNS),
    ),
    (
        "df_Fid_Uid_link_hack.csv",
        "person_company_links",
        ("SYNTHETIC_F", "CITY_NAME", "RC_NAME", "SYNTHETIC_U"),
        ("person_id", "city_name", "region_name", "company_id"),
    ),
    (
        "df_Uid_payment_hack.csv",
        "payments",
        ("BENEFICIARY", "PAYER", "PAY_AMT", "PAY_CNT"),
        ("beneficiary_id", "payer_id", "amount", "payment_count"),
    ),
)

INDEX_SQL = (
    'CREATE INDEX source_company_uid_idx ON source_company_rows ("SYNTHETIC_U")',
    'CREATE INDEX companies_industry_idx ON companies (industry_code)',
    'CREATE INDEX companies_group_idx ON companies (group_id)',
    'CREATE INDEX person_company_uid_idx ON person_company_links (company_id)',
    'CREATE INDEX person_company_person_idx ON person_company_links (person_id)',
    'CREATE INDEX payments_payer_idx ON payments (payer_id)',
    'CREATE INDEX payments_beneficiary_idx ON payments (beneficiary_id)',
)
INDEX_NAMES = (
    "source_company_uid_idx", "companies_industry_idx", "companies_group_idx",
    "person_company_uid_idx", "person_company_person_idx", "payments_payer_idx",
    "payments_beneficiary_idx",
)


def validate_headers(data_dir: Path) -> None:
    """Смотрит содержимое файла и проверяет имена полей перед созданием изменений БД."""
    for filename, _, headers, _ in IMPORTS:
        path = data_dir / filename
        if not path.is_file():
            raise FileNotFoundError(f"Не найден CSV: {path}")
        with path.open("r", encoding="utf-8-sig", newline="") as stream:
            actual = tuple(next(csv.reader(stream, delimiter=";"), ()))
        if actual != headers:
            raise ValueError("Получены некорректные столбцы")


def copy_csv(conn: psycopg.Connection, path: Path, table: str, columns: tuple[str, ...]) -> None:
    statement = (
        f"COPY {table} ({', '.join(columns)}) FROM STDIN "
        "WITH (FORMAT CSV, HEADER TRUE, DELIMITER ';', ENCODING 'UTF8')"
    )
    with conn.cursor() as cursor:
        with cursor.copy(statement) as copy:
            with path.open("rb") as file:
                while chunk := file.read(1024 * 1024):
                    copy.write(chunk)


def import_data(data_dir: Path, dsn: str) -> dict[str, int]:
    validate_headers(data_dir)
    schema = (ROOT / "sql" / "schema.sql").read_text(encoding="utf-8")
    with psycopg.connect(dsn, autocommit=True) as conn:
        with conn.transaction():
            conn.execute(schema)
            # Создаем вторичные индексы заново после копирования для ускорения.
            for name in INDEX_NAMES:
                conn.execute(f"DROP INDEX IF EXISTS {name}")
            conn.execute(
                "TRUNCATE TABLE payments, person_company_links, companies, "
                "source_company_rows RESTART IDENTITY"
            )

            for filename, table, _, dest_columns in IMPORTS:
                print(f"Импорт {filename} → {table} ...", flush=True)
                copy_csv(conn, data_dir / filename, table, dest_columns)

            conn.execute('''
                WITH grouped AS (
                    SELECT
                        "SYNTHETIC_U" AS company_id,
                        COUNT(*)::integer AS source_row_count,
                        COUNT(DISTINCT to_jsonb(s) - 'row_id') AS variants,
                        MIN(row_id) AS first_row_id
                    FROM source_company_rows s
                    GROUP BY "SYNTHETIC_U"
                )
                INSERT INTO companies (
                    company_id, profile, source_row_count, is_ambiguous,
                    industry_code, industry_name, region, group_id, revenue_raw
                )
                SELECT
                    g.company_id,
                    CASE WHEN g.variants = 1 THEN to_jsonb(s) - 'row_id' END,
                    g.source_row_count,
                    g.variants > 1,
                    CASE WHEN g.variants = 1 THEN s."MAINOKVED_CODE" END,
                    CASE WHEN g.variants = 1 THEN s."MAINOKVED_NAME" END,
                    CASE WHEN g.variants = 1 THEN s."REGION" END,
                    CASE WHEN g.variants = 1 THEN s."GROUP_ID" END,
                    CASE WHEN g.variants = 1
                        THEN NULLIF(TRIM(s."REVENUE_COMPANY"), '')::NUMERIC END
                FROM grouped g
                JOIN source_company_rows s ON s.row_id = g.first_row_id
            ''')

            for statement in INDEX_SQL:
                conn.execute(statement)

            counts = {}
            for name in ("source_company_rows", "companies",
                         "person_company_links", "payments"):
                counts[name] = conn.execute(f"SELECT COUNT(*) FROM {name}").fetchone()[0]
            counts["ambiguous_companies"] = conn.execute(
                "SELECT COUNT(*) FROM companies WHERE is_ambiguous"
            ).fetchone()[0]
    return counts


def main() -> None:
    parser = argparse.ArgumentParser(description="Load Alfa Hack anonymized CSV into PostgreSQL")
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    args = parser.parse_args()
    if not args.database_url:
        parser.error("Необходимо указать DATABASE_URL в .env")

    result = import_data(args.data_dir, args.database_url)
    print("Готово! Записей по таблицам:")
    for table, count in result.items():
        print(f"  {table}: {count:,}".replace(",", " "))


if __name__ == "__main__":
    main()