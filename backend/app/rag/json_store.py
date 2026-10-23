"""Single-process development store with REAL E5 vectors, not a production DB."""
from datetime import date
import json
from pathlib import Path

from app.rag.hybrid import eligible, fuse, local_rank, validate_query
from app.rag.index import content_version, prepare


class JsonVectorStore:
    def __init__(self, path: Path, encoder):
        self.path, self.encoder = Path(path), encoder

    def read(self):
        if not self.path.exists():
            return []
        data = json.loads(self.path.read_text(encoding="utf-8"))
        if data["model_key"] != self.encoder.identity:
            raise ValueError("Индекс создан другой версией модели; создайте новый файл и переиндексируйте")
        return data["rows"]

    @property
    def version(self):
        return content_version(self.read())

    def upsert(self, rows, *, kind, parent_ids=None):
        from app.rag.ingest import atomic_json
        previous = self.read()
        indexed = prepare(rows, previous, self.encoder)
        # Bank import is a corpus snapshot; news updates replace only supplied news.
        retained = [r for r in previous if r["kind"] != kind or
                    (parent_ids is not None and r["parent_id"] not in parent_ids)]
        merged = retained + indexed
        if len({r["id"] for r in merged}) != len(merged):
            raise ValueError("Повторяющиеся ID в индексе")
        atomic_json(self.path, {"version": 1, "model_key": self.encoder.identity, "rows": merged})
        return len(indexed)

    def search(self, query, limit=5, *, as_of=None, kind="bank", min_similarity=0.65):
        validate_query(query, limit)
        if kind not in {"bank", "news"} or not -1 <= min_similarity <= 1:
            raise ValueError("Неверные параметры поиска")
        rows = [r for r in self.read() if eligible(r, kind, as_of or date.today())]
        if not rows:
            return []
        semantic, lexical = local_rank(rows, self.encoder.query(query), query, min_similarity=min_similarity)
        return fuse(semantic, lexical, query, limit, bank=kind == "bank")
