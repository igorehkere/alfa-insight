"""PostgreSQL full-text retrieval for the MVP, without embeddings or pgvector."""
from datetime import date
from pathlib import Path
import re

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from app.rag.hybrid import fuse, validate_query
from app.rag.records import content_version

LOCK_ID = 71423002


class PgFTSStore:
    def __init__(self, dsn):
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)

    def connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10,
                               options="-c statement_timeout=15000 -c lock_timeout=10000")

    @classmethod
    def initialize(cls, dsn):
        with cls(dsn).connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            connection.execute(Path(__file__).with_name("fts_schema.sql").read_text())

    @property
    def version(self):
        with self.connect() as connection:
            row = connection.execute("SELECT corpus_version FROM dima_rag_fts_state WHERE singleton").fetchone()
            return "fts-v1:" + (row["corpus_version"] if row else "empty")

    def upsert(self, rows, *, kind, parent_ids=None):
        rows = list(rows)
        if kind not in {"bank", "news"} or any(row["kind"] != kind for row in rows):
            raise ValueError("Неверный тип записей поиска")
        if len({row["id"] for row in rows}) != len(rows):
            raise ValueError("Повторяющиеся ID фрагментов")
        if parent_ids is not None:
            parent_ids = set(parent_ids)
            if any(row["parent_id"] not in parent_ids for row in rows):
                raise ValueError("Фрагмент не входит в заменяемые документы")
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            if parent_ids is None:
                connection.execute("DELETE FROM dima_rag_fts_records WHERE kind=%s", (kind,))
            else:
                connection.execute("DELETE FROM dima_rag_fts_records WHERE kind=%s AND parent_id=ANY(%s)",
                                   (kind, list(parent_ids)))
            with connection.cursor() as cursor:
                cursor.executemany("""INSERT INTO dima_rag_fts_records
                    (id,kind,parent_id,text,published_at,topics,metadata) VALUES (%s,%s,%s,%s,%s,%s,%s)""",
                    [(row["id"], kind, row["parent_id"], row["text"], row["published_at"],
                      row["topics"], Jsonb(row["metadata"])) for row in rows])
            stored = connection.execute("SELECT id,kind,parent_id,text,published_at,topics,metadata FROM dima_rag_fts_records").fetchall()
            connection.execute("""INSERT INTO dima_rag_fts_state(singleton,corpus_version) VALUES (true,%s)
                ON CONFLICT(singleton) DO UPDATE SET corpus_version=excluded.corpus_version""",
                (content_version(stored),))
        return len(rows)

    def search(self, query, limit=5, *, as_of=None, kind="bank"):
        validate_query(query, limit)
        if kind not in {"bank", "news"}:
            raise ValueError("Неверный тип поиска")
        with self.connect() as connection:
            def retrieve(expression, function):
                # function is selected below from two hard-coded SQL functions.
                return connection.execute(f"""SELECT id,text,topics,metadata FROM dima_rag_fts_records
                WHERE kind=%s AND published_at<=%s
                  AND (%s='news' OR (metadata->>'reviewed'='true' AND metadata->>'origin'='bank'))
                  AND (search_ru @@ {function}('russian', %s)
                    OR search_simple @@ {function}('simple', %s))
                ORDER BY ts_rank_cd(search_ru, {function}('russian', %s))
                       + ts_rank_cd(search_simple, {function}('simple', %s)) DESC, id LIMIT 40""",
                    (kind, as_of or date.today(), kind, expression, expression, expression, expression)).fetchall()
            rows = retrieve(query, "plainto_tsquery")
            if not rows:
                # An extra word such as "кредит" must not hide all rate excerpts.
                terms = re.findall(r"[а-яёa-z0-9]+", query.lower())[:12]
                if len(terms) > 1:
                    rows = retrieve(" OR ".join(terms), "websearch_to_tsquery")
        return fuse([], rows, query, limit, bank=kind == "bank")
