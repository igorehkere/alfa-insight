"""Behavioral tests: no downloads, no API key, no database needed by default."""
import asyncio
import hashlib
import json
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

import numpy as np

from app.rag.embeddings import DIMENSIONS, checked_vectors
from app.rag.index import bank_records, news_records
from app.rag.ingest import assess_quality, extract_pdf, import_pdf, ExtractionError
from app.rag.json_store import JsonVectorStore
from app.rag.search import read_corpus, DEFAULT_CORPUS
from app.rag.vlm import enrich_pdf, PageContent


class FakeEncoder:
    """Test double for storage behavior; does not measure semantic search quality."""
    identity = "test-only-e5-shape"
    def __init__(self):
        self.passage_count = 0
        self.query_count = 0

    def split_passage(self, text):
        return [text] if text else []

    def passages(self, texts):
        self.passage_count += len(texts)
        v = np.zeros((len(texts), DIMENSIONS), dtype=np.float32)
        v[:, 0] = 1
        return v

    def query(self, text):
        self.query_count += 1
        v = np.zeros(DIMENSIONS, dtype=np.float32)
        v[0] = 1
        return v


class IndexTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.encoder = FakeEncoder()
        self.store = JsonVectorStore(Path(self.temp.name) / "vectors.json", self.encoder)
        self.corpus = read_corpus(DEFAULT_CORPUS)

    def test_import_reuses_vectors_and_reindexes_changed_text(self):
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        old_version = self.store.version
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        self.assertEqual(self.encoder.passage_count, 1)
        self.corpus.chunks[0].reviewed = False
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        self.assertEqual(self.encoder.passage_count, 1)
        self.assertNotEqual(old_version, self.store.version)
        self.corpus.chunks[0].text += " Новое содержание."
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        self.assertEqual(self.encoder.passage_count, 2)

    def test_query_only_embeds_query_and_filters_future_unreviewed(self):
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        found = self.store.search("ключевая ставка", as_of=date(2026, 10, 20))
        self.assertEqual(found[0]["page_number"], 1)
        self.assertEqual(self.encoder.passage_count, 1)
        self.assertEqual(self.encoder.query_count, 1)
        self.assertFalse(self.store.search("ставка", as_of=date(2026, 9, 1)))
        self.corpus.chunks[0].reviewed = False
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        self.assertFalse(self.store.search("ставка", as_of=date(2026, 10, 20)))

    def test_news_update_preserves_other_articles_and_bank(self):
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        news = [{"id": "n1", "title": "Ставка", "body": "Первый текст", "published_at": "2026-10-20"},
                {"id": "n2", "title": "Инфляция", "body": "Второй текст", "published_at": "2026-10-20"}]
        self.store.upsert(news_records(news, self.encoder), kind="news", parent_ids={"n1", "n2"})
        news[0]["body"] = "Изменённый текст"
        self.store.upsert(news_records(news[:1], self.encoder), kind="news", parent_ids={"n1"})
        self.assertEqual(len(self.store.read()), 3)
        self.assertEqual(self.encoder.passage_count, 4)
        found = self.store.search("ставка", as_of=date(2026, 10, 20), kind="news")
        self.assertEqual(len({r["news_id"] for r in found}), len(found))

    def test_wrong_model_and_invalid_vectors_rejected(self):
        self.store.upsert(bank_records(self.corpus, self.encoder), kind="bank")
        self.encoder.identity = "other-model"
        with self.assertRaises(ValueError):
            self.store.search("ставка")
        for vector in [np.zeros((1, 384)), np.ones((1, 383)), np.full((1, 384), np.nan)]:
            with self.assertRaises(ValueError):
                checked_vectors(vector, 1)

    def test_unknown_news_date_not_invented(self):
        with self.assertRaises(ValueError):
            news_records([{"id": "n", "title": "Новость", "body": "Текст"}], self.encoder)


class ExtractionTests(unittest.TestCase):
    def test_quality_flags_numeric_tables_and_broken_text(self):
        self.assertIn("too_little_text", assess_quality(""))
        self.assertIn("damaged_characters", assess_quality("Текст " * 20 + "\ufffd"))
        self.assertFalse(assess_quality("Содержательный текст банковской аналитики. " * 10))

    def test_real_text_pdf_and_mixed_image_requires_ocr(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "text.pdf"
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((72,72), "A real PDF text layer, long enough to extract without OCR.")
                doc.save(path)
            self.assertEqual(extract_pdf(path)[0]["method"], "text")
            with pymupdf.open(path) as doc:
                # Raster content under a valid text header must not be ignored.
                pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0,0,400,400), False)
                pix.clear_with(255)
                doc[0].insert_image(pymupdf.Rect(20,100,550,750), stream=pix.tobytes("png"))
                mixed = Path(directory) / "mixed.pdf"
                doc.save(mixed)
            with self.assertRaises(ExtractionError):
                extract_pdf(mixed)


class VlmTests(unittest.IsolatedAsyncioTestCase):
    async def test_selected_page_cached_and_stays_unreviewed(self):
        import pymupdf
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            pdf, corpus = root/"doc.pdf", root/"corpus.json"
            with pymupdf.open() as doc:
                page = doc.new_page()
                page.insert_text((72,72), "A real PDF text layer, long enough to extract without OCR.")
                doc.save(pdf)
            import_pdf(pdf, corpus, document_id="d", title="D", published_at=date(2026,1,1))
            class Client:
                cache_identity = "provider-test"
            with patch("app.rag.vlm.extract_page", return_value=(PageContent(text="Распознанный текст. "*10), {"model":"test"})) as remote:
                await enrich_pdf(pdf, corpus, document_id="d", pages=[1], cache_dir=root/"cache", client=Client())
                await enrich_pdf(pdf, corpus, document_id="d", pages=[1], cache_dir=root/"cache", client=Client())
                self.assertEqual(remote.call_count, 1)
            result = read_corpus(corpus)
            self.assertTrue(all(c.extraction_method == "vlm" and not c.reviewed for c in result.chunks))
            with self.assertRaises(ValueError):
                await enrich_pdf(pdf, corpus, document_id="d", pages=[2], cache_dir=root/"cache", client=Client())


if __name__ == "__main__":
    unittest.main()
