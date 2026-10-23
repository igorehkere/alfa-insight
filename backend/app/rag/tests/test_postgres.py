"""Opt-in integration tests against a DISPOSABLE dima_rag_test database only."""
import os
import unittest
from datetime import date
from urllib.parse import urlparse

from app.rag.index import bank_records, news_records
from app.rag.search import read_corpus, DEFAULT_CORPUS
from app.rag.tests.test_rag import FakeEncoder

DSN = os.getenv("DIMA_TEST_DATABASE_URL")


@unittest.skipUnless(DSN, "Set DIMA_TEST_DATABASE_URL for a disposable pgvector DB")
class PostgresTests(unittest.TestCase):
    def setUp(self):
        if urlparse(DSN).path != "/dima_rag_test":
            raise ValueError("Integration tests require a disposable database named dima_rag_test")
        from app.rag.postgres import PgVectorStore
        self.encoder = FakeEncoder()
        PgVectorStore.initialize(DSN)
        self.store = PgVectorStore(DSN, self.encoder)
        # The name guard above is required before touching any test data.
        with self.store.connect() as connection:
            connection.execute("DELETE FROM dima_rag_records")
            connection.execute("DELETE FROM dima_rag_state")
        self.corpus = read_corpus(DEFAULT_CORPUS)

    def test_sql_search_filters_upsert_and_revision(self):
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        self.assertEqual(self.encoder.passage_count, 1)
        rows = self.store.search("ключевая ставка", as_of=date(2026,10,20))
        self.assertEqual(rows[0]["page_number"], 1)
        self.assertEqual(self.encoder.query_count, 1)
        self.assertFalse(self.store.search("ставка", as_of=date(2026,9,1)))
        before = self.store.version
        self.corpus.chunks[0].reviewed = False
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        self.assertNotEqual(before, self.store.version)
        self.assertEqual(self.encoder.passage_count, 1)
        self.assertFalse(self.store.search("ставка", as_of=date(2026,10,20)))
        news = [{"id":"test-news","title":"Кредит и ставка","body":"Новый текст", "published_at":"2026-10-20"}]
        self.store.upsert(news_records(news, self.encoder), kind="news", parent_ids={"test-news"})
        result = self.store.search("кредит", kind="news", as_of=date(2026,10,20))
        self.assertEqual(result[0]["news_id"], "test-news")
        self.encoder.identity = "different-model"
        with self.assertRaises(ValueError):
            self.store.search("ставка")


if __name__ == "__main__":
    unittest.main()
