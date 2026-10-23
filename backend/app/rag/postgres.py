"""Independent psycopg adapter; never imports Igor's ORM, sessions or main.py."""
from datetime import date
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from pgvector.psycopg import register_vector

from app.rag.hybrid import fuse, validate_query
from app.rag.index import content_version, prepare

LOCK_ID = 71423001


class PgVectorStore:
    def __init__(self, dsn: str, encoder):
        # psycopg accepts postgresql://; SQLAlchemy's postgresql+psycopg:// is normalized.
        self.dsn = dsn.replace("postgresql+psycopg://", "postgresql://", 1)
        self.encoder = encoder

    @staticmethod
    def initialize(dsn):
        with psycopg.connect(dsn.replace("postgresql+psycopg://", "postgresql://", 1), connect_timeout=10) as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            connection.execute(Path(__file__).with_name("schema.sql").read_text())

    def connect(self):
        conn = psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10)
        try:
            register_vector(conn)
            conn.commit()  # Type discovery reads catalogs; end that transaction first.
        except Exception:
            conn.close()
            raise
        return conn

    def _check_model(self, connection):
        row = connection.execute("SELECT model_key FROM dima_rag_state WHERE singleton").fetchone()
        if row and row["model_key"] != self.encoder.identity:
            raise ValueError("Версия embedding-модели не совпадает с индексом; нужна полная переиндексация")

    @property
    def version(self):
        with self.connect() as connection:
            self._check_model(connection)
            row = connection.execute("SELECT corpus_version FROM dima_rag_state WHERE singleton").fetchone()
            return row["corpus_version"] if row else "empty"

    def upsert(self, rows, *, kind, parent_ids=None):
        # Lock includes embedding preparation, so replicas do not embed the same update twice.
        # Import is an explicit job with its own connection; never a startup hook.
        with self.connect() as connection:
            connection.execute("SELECT pg_advisory_xact_lock(%s)", (LOCK_ID,))
            self._check_model(connection)
            previous = connection.execute("SELECT * FROM dima_rag_records WHERE kind=%s", (kind,)).fetchall()
            indexed = prepare(rows, previous, self.encoder)
            if parent_ids is None:
                connection.execute("DELETE FROM dima_rag_records WHERE kind=%s", (kind,))
            else:
                connection.execute("DELETE FROM dima_rag_records WHERE kind=%s AND parent_id=ANY(%s)",
                                   (kind, list(parent_ids)))
            with connection.cursor() as cursor:
                cursor.executemany("""INSERT INTO dima_rag_records
                    (id, kind, parent_id, text, published_at, topics, metadata, model_key, fingerprint, embedding)
                    VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    [(r["id"], r["kind"], r["parent_id"], r["text"], r["published_at"], r["topics"],
                      Jsonb(r["metadata"]), r["model_key"], r["fingerprint"], r["embedding"]) for r in indexed])
            state_rows = connection.execute("SELECT id,kind,parent_id,text,published_at,topics,metadata,model_key,fingerprint FROM dima_rag_records").fetchall()
            connection.execute("""INSERT INTO dima_rag_state(singleton,model_key,corpus_version) VALUES(true,%s,%s)
                ON CONFLICT(singleton) DO UPDATE SET model_key=excluded.model_key,corpus_version=excluded.corpus_version""",
                (self.encoder.identity, content_version(state_rows)))
        return len(indexed)

    def search(self, query, limit=5, *, as_of=None, kind="bank", min_similarity=0.65):
        validate_query(query, limit)
        if kind not in {"bank", "news"} or not -1 <= min_similarity <= 1:
            raise ValueError("Неверные параметры поиска")
        vector = self.encoder.query(query)
        filters = """kind=%s AND published_at<=%s AND model_key=%s
            AND (%s='news' OR (metadata->>'reviewed'='true' AND metadata->>'origin'='bank'))"""
        params = (kind, as_of or date.today(), self.encoder.identity, kind)
        with self.connect() as connection:
            # Both rankings see the same snapshot if an importer commits concurrently.
            connection.execute("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY")
            self._check_model(connection)
            semantic = connection.execute(f"""SELECT id,text,metadata,topics FROM dima_rag_records
                WHERE {filters} AND 1-(embedding <=> %s::vector)>=%s
                ORDER BY embedding <=> %s::vector, id LIMIT 40""",
                (*params, vector, min_similarity, vector)).fetchall()
            lexical = connection.execute(f"""SELECT id,text,metadata,topics FROM dima_rag_records
                WHERE {filters} AND (search_ru @@ plainto_tsquery('russian', %s)
                    OR search_simple @@ plainto_tsquery('simple', %s))
                ORDER BY ts_rank_cd(search_ru, plainto_tsquery('russian', %s))
                       + ts_rank_cd(search_simple, plainto_tsquery('simple', %s)) DESC, id LIMIT 40""",
                (*params, query, query, query, query)).fetchall()
        return fuse(semantic, lexical, query, limit, bank=kind == "bank")
