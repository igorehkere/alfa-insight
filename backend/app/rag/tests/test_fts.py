"""Real PostgreSQL FTS checks, opt-in and restricted to dima_rag_test."""
import copy
import os
from datetime import date
from pathlib import Path
import unittest
from urllib.parse import urlparse

from app.rag.fts import PgFTSStore
from app.rag.records import TextSplitter, bank_records, news_records
from app.rag.search import read_corpus

DSN = os.getenv("DIMA_TEST_FTS_DATABASE_URL")
CORPUS = Path(__file__).resolve().parents[1]/"data"/"reviewed_corpus.json"


@unittest.skipUnless(DSN, "Set DIMA_TEST_FTS_DATABASE_URL to disposable dima_rag_test")
class FTSTests(unittest.TestCase):
    def setUp(self):
        if urlparse(DSN).path != "/dima_rag_test":
            raise ValueError("FTS tests require a disposable database named dima_rag_test")
        PgFTSStore.initialize(DSN)
        self.store = PgFTSStore(DSN)
        with self.store.connect() as connection:
            connection.execute("DELETE FROM dima_rag_fts_records")
            connection.execute("DELETE FROM dima_rag_fts_state")
        self.corpus = read_corpus(CORPUS)
        self.rows = bank_records(self.corpus, TextSplitter())

    def index(self):
        self.store.upsert(self.rows, kind="bank")

    def test_real_morphology_document_pages_and_empty_results(self):
        self.index()
        for query, doc in [("ключевой ставки", "alfa-rate-2026-09-11"),
                           ("ключевая ставка кредит", "alfa-rate-2026-09-11"),
                           ("ослабление рубля", "alfa-inflation-2026-09-02"),
                           ("продаж автомобилей", "alfa-macro-7m26-2026-09-07")]:
            with self.subTest(query=query):
                rows = self.store.search(query, as_of=date(2026,10,24))
                self.assertTrue(any(row["document_id"] == doc and row["page_number"] == 1 for row in rows), rows)
                self.assertTrue(all(row["reviewed"] and row["origin"] == "bank" for row in rows))
        self.assertEqual(self.store.search("астероиды"), [])
        historical = self.store.search("ключевая ставка", as_of=date(2026,9,1))
        self.assertTrue(historical)  # Newly reviewed July/August reports are now available.
        self.assertTrue(all(row["published_at"] <= "2026-09-01" for row in historical))
        self.assertFalse(any(row["document_id"] == "alfa-rate-2026-09-11" for row in historical))
        self.assertEqual(self.store.search("ключевая ставка", as_of=date(2020,1,1)), [])

    def test_reimport_is_idempotent_and_review_changes_version(self):
        self.index()
        old = self.store.version
        self.index()
        self.assertEqual(self.store.version, old)
        with self.store.connect() as connection:
            self.assertEqual(connection.execute("SELECT count(*) AS n FROM dima_rag_fts_records").fetchone()["n"], len(self.rows))
        for row in self.rows:
            row["metadata"]["reviewed"] = False
        self.index()
        self.assertNotEqual(self.store.version, old)
        self.assertEqual(self.store.search("ключевая ставка"), [])

    def test_fixture_origin_is_not_bank_evidence(self):
        for row in self.rows:
            row["metadata"]["origin"] = "fixture"
        self.index()
        self.assertEqual(self.store.search("ключевая ставка"), [])

    def test_update_removes_stale_chunks_and_preserves_other_kinds(self):
        self.index()
        news = [{"id":"n1","title":"Ставка","body":"Условия кредита","published_at":"2026-10-20"},
                {"id":"n2","title":"Бензин","body":"Цены на топливо","published_at":"2026-10-20"}]
        self.store.upsert(news_records(news, TextSplitter()), kind="news", parent_ids={"n1","n2"})
        before = self.store.version
        news[0]["body"] = "Изменённые условия финансирования"
        self.store.upsert(news_records(news[:1], TextSplitter()), kind="news", parent_ids={"n1"})
        self.assertNotEqual(self.store.version, before)
        self.assertEqual(self.store.search("топливо", kind="news")[0]["news_id"], "n2")
        self.assertTrue(self.store.search("ключевая ставка"))
        self.store.upsert([], kind="bank")
        self.assertEqual(self.store.search("ключевая ставка"), [])
        self.assertTrue(self.store.search("топливо", kind="news"))

    def test_invalid_batches_preserve_existing_index(self):
        self.index()
        version = self.store.version
        for rows, kwargs in [([self.rows[0], self.rows[0]], {}),
                             (self.rows, {"parent_ids": {"other"}})]:
            with self.assertRaises(ValueError):
                self.store.upsert(rows, kind="bank", **kwargs)
            self.assertEqual(self.store.version, version)

    def test_queries_are_parameterized_and_bounded(self):
        self.index()
        self.store.search("'); DROP TABLE dima_rag_fts_records; --")
        self.assertTrue(self.store.search("ключевая ставка"))
        for query in ["", " "*20, "x"*501]:
            with self.assertRaises(ValueError):
                self.store.search(query)


class ReviewedCorpusTests(unittest.TestCase):
    def test_expanded_documents_retain_reviewed_page_excerpts_and_original_ids(self):
        corpus = read_corpus(CORPUS)
        self.assertGreaterEqual(len(corpus.documents), 3)
        self.assertGreaterEqual(len(corpus.chunks), 10)
        self.assertTrue(all(c.reviewed and c.extraction_method == "manual_transcription" for c in corpus.chunks))
        self.assertTrue(all(d.origin == "bank" for d in corpus.documents))

    def test_original_corpus_is_a_compatible_subset_and_pdf_hashes_match(self):
        import hashlib
        import pymupdf
        corpus = read_corpus(CORPUS)
        old = read_corpus(CORPUS.with_name("reviewed_corpus.v1.json"))
        self.assertEqual(len(corpus.documents), 8)
        self.assertEqual(len(corpus.chunks), 46)
        current = {chunk.id: chunk.model_dump() for chunk in corpus.chunks}
        self.assertTrue(all(current[c.id] == c.model_dump() for c in old.chunks))
        originals = Path(__file__).resolve().parents[4] / "analitics"
        for document in corpus.documents:
            path = originals / document.filename
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), document.sha256)
            with pymupdf.open(path) as pdf:
                self.assertEqual(len(pdf), document.page_count)

    def test_new_topics_and_historical_steel_are_traceable(self):
        from app.rag.search import JsonBankSearch
        search = JsonBankSearch(CORPUS)
        for query, document in [("потребление", "alfa-consumption-2026-08-05"),
                                ("плавающая ставка заемщика", "alfa-fi-strategy-2026-07-22"),
                                ("РусГидро", "alfa-bond-2026-09-14"),
                                ("плоского проката", "alfa-steel-2021-04-15")]:
            with self.subTest(query=query):
                self.assertTrue(any(row["document_id"] == document for row in search.search(query)))
