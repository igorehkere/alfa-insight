"""Explicit imports/search. Safe defaults use a local JSON index; no paid LLM."""
import argparse
import json
import os
from datetime import date
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["download-model", "init-db", "index-bank", "index-news", "index-db-news", "search"])
    parser.add_argument("--store", choices=["json", "postgres"], default="json")
    parser.add_argument("--index", type=Path, default=Path(".dima-data/vector-index.json"))
    parser.add_argument("--input", type=Path)
    parser.add_argument("--query")
    parser.add_argument("--kind", choices=["bank", "news"], default="bank")
    parser.add_argument("--as-of", type=date.fromisoformat, default=date.today())
    args = parser.parse_args()
    if args.command == "init-db":
        from app.rag.postgres import PgVectorStore
        PgVectorStore.initialize(os.environ["RAG_DATABASE_URL"])
        print("Созданы только dima_rag_* и расширение vector")
        return
    if args.command == "download-model":
        os.environ["RAG_LOCAL_FILES_ONLY"] = "0"  # Only this explicit command may download.
    from app.rag.embeddings import get_encoder
    encoder = get_encoder()
    if args.command == "download-model":
        print(encoder.identity)
        return
    if args.store == "json":
        from app.rag.json_store import JsonVectorStore
        store = JsonVectorStore(args.index, encoder)
    else:
        from app.rag.postgres import PgVectorStore
        store = PgVectorStore(os.environ["RAG_DATABASE_URL"], encoder)
    if args.command == "search":
        if not args.query:
            parser.error("Нужен --query")
        print(json.dumps(store.search(args.query, kind=args.kind, as_of=args.as_of), ensure_ascii=False, indent=2))
        return
    if args.command == "index-db-news":
        from app.agent.persistence import list_news
        from app.rag.index import news_records
        items = list_news(limit=200)
        dated = [item for item in items if item.get("published_at")]
        count = store.upsert(news_records(dated, encoder), kind="news", parent_ids={item["id"] for item in dated})
        print(json.dumps({"indexed": count, "skipped_undated": len(items) - len(dated), "version": store.version}))
        return
    if not args.input:
        parser.error("Нужен --input")
    if args.command == "index-bank":
        from app.rag.search import read_corpus
        from app.rag.index import bank_records
        rows = bank_records(read_corpus(args.input), encoder)
        count = store.upsert(rows, kind="bank")
    else:
        from app.rag.index import news_records
        data = json.loads(args.input.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data = data.get("news", data.get("items"))
        if not isinstance(data, list):
            parser.error("Нужен список новостей или JSON с news/items")
        rows = news_records(data, encoder)
        count = store.upsert(rows, kind="news", parent_ids={str(n["id"]) for n in data})
    print(json.dumps({"indexed": count, "version": store.version, "model": encoder.identity}))


if __name__ == "__main__":
    main()
