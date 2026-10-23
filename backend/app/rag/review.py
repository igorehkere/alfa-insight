"""Explicit human approval of selected pages after comparing to the original PDF."""
import argparse
from pathlib import Path
from app.rag.ingest import atomic_json
from app.rag.search import read_corpus


def approve_pages(path, document_id, pages):
    corpus = read_corpus(path)
    selected = [c for c in corpus.chunks if c.document_id == document_id and c.page_number in pages]
    if {c.page_number for c in selected} != set(pages):
        raise ValueError("Некоторые страницы отсутствуют или не имеют извлечённого текста")
    for chunk in selected:
        chunk.reviewed = True
    atomic_json(path, corpus.model_dump(mode="json"))
    return len(selected)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("corpus", type=Path)
    parser.add_argument("--id", required=True)
    parser.add_argument("--pages", required=True)
    parser.add_argument("--confirm-reviewed", action="store_true", required=True)
    args = parser.parse_args()
    print("Подтверждено фрагментов:", approve_pages(args.corpus, args.id, {int(p) for p in args.pages.split(",")}))


if __name__ == "__main__":
    main()
