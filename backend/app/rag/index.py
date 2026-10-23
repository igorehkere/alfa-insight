"""Storage-independent import. Text changes trigger embeddings; metadata changes do not."""
from datetime import date
import hashlib
import json

from app.agent.tools import news_from_record
from app.rag.embeddings import checked_vectors, fingerprint
from app.rag.search import topics


def bank_records(corpus, encoder):
    documents = {doc.id: doc for doc in corpus.documents}
    rows = []
    for chunk in corpus.chunks:
        doc = documents[chunk.document_id]
        # Unreviewed data may be stored for workflow purposes, but is never retrieved.
        for number, text in enumerate(encoder.split_passage(chunk.text)):
            identifier = f"{chunk.id}:s{number}"
            rows.append({"id": identifier, "kind": "bank", "parent_id": doc.id,
                "text": text, "published_at": doc.published_at.isoformat(), "topics": sorted(topics(text)),
                "metadata": {"chunk_id": identifier, "document_id": doc.id, "document_title": doc.title,
                    "page_number": chunk.page_number, "published_at": doc.published_at.isoformat(),
                    "source_url": doc.source_url, "reviewed": chunk.reviewed, "origin": doc.origin,
                    "extraction_method": chunk.extraction_method}})
    return rows


def news_records(records, encoder):
    rows = []
    for record in records:
        item = news_from_record(record)
        if not item["published_at"]:
            raise ValueError("Для индексации новости нужна дата публикации; неизвестную дату не выдумываем")
        published = date.fromisoformat(item["published_at"][:10]).isoformat()
        for number, text in enumerate(encoder.split_passage(item["title"] + "\n\n" + item["body"])):
            rows.append({"id": f"news:{item['id']}:s{number}", "kind": "news", "parent_id": item["id"],
                "text": text, "published_at": published, "topics": sorted(topics(text)),
                "metadata": {"news_id": item["id"], "title": item["title"], "source_url": item["source_url"],
                    "published_at": item["published_at"], "source": item["source"],
                    "provenance": item["provenance"], "body_is_summary": item["body_is_summary"]}})
    return rows


def prepare(rows, previous, encoder):
    """No query-time re-embedding of passages; reuse vectors for unchanged text/model."""
    old = {row["id"]: row for row in previous}
    missing = []
    for row in rows:
        row["model_key"] = encoder.identity
        row["fingerprint"] = fingerprint(row["text"], encoder.identity)
        prev = old.get(row["id"])
        if prev and prev["fingerprint"] == row["fingerprint"] and prev["model_key"] == encoder.identity:
            row["embedding"] = checked_vectors([prev["embedding"]], 1)[0].tolist()
        else:
            missing.append(row)
    if missing:
        vectors = checked_vectors(encoder.passages([row["text"] for row in missing]), len(missing))
        for row, vector in zip(missing, vectors):
            row["embedding"] = vector.tolist()
    return rows


def content_version(rows):
    # Exclude vector encoding details, include review changes and provenance.
    payload = [{k: v for k, v in row.items() if k != "embedding"} for row in sorted(rows, key=lambda r: r["id"])]
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()
