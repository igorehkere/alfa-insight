from __future__ import annotations

import hashlib
import json
import math
import re
from collections import Counter
from datetime import date
from pathlib import Path

from app.rag.models import Corpus

DEFAULT_CORPUS = Path(__file__).with_name("data") / "bank_corpus.json"
STOP = {"и", "в", "во", "на", "по", "о", "об", "для", "как", "это", "с", "из", "а", "the", "of", "and"}
GROUPS = {
    "rates": ("ставк", "кредит", "финансирован", "рефинансирован", "rate", "loan"),
    "inflation": ("инфляц", "подорожан", "inflation"),
    "currency": ("валют", "рубл", "доллар", "currency"),
    "commodities": ("сырь", "металл", "сталь", "золот", "commodit", "gold"),
    "construction": ("строител", "строй", "construction"),
    "trade": ("импорт", "экспорт", "тамож", "логист", "import", "export"),
}


def tokens(text: str) -> list[str]:
    # Deliberately small normalizer, not a Russian morphological analyzer.
    return [t if len(t) < 6 else t[:6] for t in re.findall(r"[\w]+", text.lower().replace("ё", "е")) if t not in STOP]


def topics(text: str) -> set[str]:
    lower = text.lower().replace("ё", "е")
    words = re.findall(r"[a-zа-я]+", lower)
    # Match word starts: "поставки" must not be mistaken for "ставки".
    return {tag for tag, roots in GROUPS.items() if any(word.startswith(root) for word in words for root in roots)}


def read_corpus(path: Path = DEFAULT_CORPUS) -> Corpus:
    return Corpus.model_validate_json(Path(path).read_text(encoding="utf-8"))


class JsonBankSearch:
    def __init__(self, path: Path = DEFAULT_CORPUS):
        self.path = Path(path)
        self.corpus = read_corpus(self.path)
        self.version = hashlib.sha256(self.path.read_bytes()).hexdigest()

    def search(self, query: str, limit: int = 5, *, as_of: date | None = None) -> list[dict]:
        if not query.strip() or len(query) > 500 or not 1 <= limit <= 10:
            raise ValueError("Нужен запрос длиной 1–500 символов и limit 1–10")
        as_of = as_of or date.today()
        docs = {d.id: d for d in self.corpus.documents if d.origin == "bank" and d.published_at <= as_of}
        eligible = [c for c in self.corpus.chunks if c.reviewed and c.document_id in docs]
        query_terms = set(tokens(query))
        query_topics = topics(query)
        term_sets = [set(tokens(c.text)) for c in eligible]
        frequencies = Counter(term for terms in term_sets for term in terms)
        found = []
        for chunk, terms in zip(eligible, term_sets):
            shared = query_terms & terms
            # Topic similarity alone must not turn an unrelated page into proof.
            if not shared:
                continue
            lexical = sum(math.log(1 + len(eligible) / (1 + frequencies[t])) for t in shared)
            score = lexical / math.sqrt(max(len(terms), 1)) + 0.1 * len(query_topics & topics(chunk.text))
            doc = docs[chunk.document_id]
            found.append({
                "chunk_id": chunk.id, "document_id": doc.id,
                "document_title": doc.title, "page_number": chunk.page_number,
                "text": chunk.text, "published_at": doc.published_at.isoformat(),
                "source_url": doc.source_url, "score": round(score, 6),
                "reviewed": chunk.reviewed, "origin": doc.origin,
                "extraction_method": chunk.extraction_method,
            })
        found.sort(key=lambda row: (-row["score"], row["chunk_id"]))
        # Avoid one long report filling the entire context.
        selected, per_doc = [], Counter()
        for row in found:
            if per_doc[row["document_id"]] < 2:
                selected.append(row)
                per_doc[row["document_id"]] += 1
            if len(selected) == limit:
                break
        return selected


def search_bank_analytics(query: str, limit: int = 5, *, path: Path = DEFAULT_CORPUS,
                          as_of: date | None = None) -> list[dict]:
    return JsonBankSearch(path).search(query, limit, as_of=as_of)
