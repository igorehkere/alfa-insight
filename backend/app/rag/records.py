"""Retrieval records without importing an embedding model or vector libraries."""
from datetime import date
import hashlib
import json

from app.agent.tools import news_from_record
from app.rag.ingest import split_page
from app.rag.search import topics


class TextSplitter:
    split_passage = staticmethod(split_page)


def bank_records(corpus, splitter):
    documents = {doc.id: doc for doc in corpus.documents}
    rows = []
    for chunk in corpus.chunks:
        doc = documents[chunk.document_id]
        for number, text in enumerate(splitter.split_passage(chunk.text)):
            identifier = f"{chunk.id}:s{number}"
            rows.append({"id": identifier, "kind": "bank", "parent_id": doc.id,
                "text": text, "published_at": doc.published_at.isoformat(), "topics": sorted(topics(text)),
                "metadata": {"chunk_id": identifier, "document_id": doc.id, "document_title": doc.title,
                    "page_number": chunk.page_number, "published_at": doc.published_at.isoformat(),
                    "source_url": doc.source_url, "reviewed": chunk.reviewed, "origin": doc.origin,
                    "extraction_method": chunk.extraction_method}})
    return rows


def news_records(records, splitter):
    rows = []
    for record in records:
        item = news_from_record(record)
        if not item["published_at"]:
            raise ValueError("Для индексации новости нужна дата публикации; неизвестную дату не выдумываем")
        published = date.fromisoformat(item["published_at"][:10]).isoformat()
        for number, text in enumerate(splitter.split_passage(item["title"] + "\n\n" + item["body"])):
            rows.append({"id": f"news:{item['id']}:s{number}", "kind": "news", "parent_id": item["id"],
                "text": text, "published_at": published, "topics": sorted(topics(text)),
                "metadata": {"news_id": item["id"], "title": item["title"], "source_url": item["source_url"],
                    "published_at": item["published_at"], "source": item["source"],
                    "provenance": item["provenance"], "body_is_summary": item["body_is_summary"]}})
    return rows


def content_version(rows):
    payload = [{k: v for k, v in row.items() if k != "embedding"} for row in sorted(rows, key=lambda r: r["id"])]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
