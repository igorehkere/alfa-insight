"""Shared rank fusion for JSON demo and PostgreSQL. Scores are not probabilities."""
from collections import Counter
from datetime import date
from app.rag.search import tokens, topics


def validate_query(query, limit):
    if not query.strip() or len(query) > 500 or not 1 <= limit <= 10:
        raise ValueError("Нужен запрос длиной 1–500 символов и limit 1–10")


def fuse(semantic, lexical, query, limit=5, *, bank=True):
    scores, rows = Counter(), {}
    for ranking in (semantic, lexical):
        for rank, row in enumerate(ranking, 1):
            key = row["id"]
            rows[key] = row
            scores[key] += 1 / (60 + rank)
    found = []
    for key, score in scores.items():
        row = rows[key]
        score += 0.001 * len(topics(query) & set(row.get("topics", [])))
        found.append({**row["metadata"], "text": row["text"], "score": round(score, 8), "_id": key})
    found.sort(key=lambda row: (-row["score"], row["_id"]))
    result, counts = [], Counter()
    for row in found:
        parent = row.get("document_id") if bank else row.get("news_id")
        if counts[parent] >= (2 if bank else 1):
            continue
        counts[parent] += 1
        row.pop("_id")
        result.append(row)
        if len(result) == limit:
            break
    return result


def eligible(row, kind, as_of):
    if row["kind"] != kind or date.fromisoformat(row["published_at"]) > as_of:
        return False
    return kind != "bank" or (row["metadata"].get("reviewed") is True and row["metadata"].get("origin") == "bank")


def local_rank(rows, vector, query, *, min_similarity=0.65):
    import numpy as np
    terms = set(tokens(query))
    semantic, lexical = [], []
    for row in rows:
        score = float(np.dot(row["embedding"], vector))
        shared = len(terms & set(tokens(row["text"])))
        if score >= min_similarity:
            semantic.append((score, row))
        if shared:
            lexical.append((shared, row))
    semantic.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    lexical.sort(key=lambda pair: (-pair[0], pair[1]["id"]))
    return [r for _, r in semantic[:40]], [r for _, r in lexical[:40]]
