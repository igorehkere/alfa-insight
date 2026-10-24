"""Storage-independent import. Text changes trigger embeddings; metadata changes do not."""
from app.rag.embeddings import checked_vectors, fingerprint
from app.rag.records import bank_records, news_records, content_version


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
