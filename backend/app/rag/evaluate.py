import json
from datetime import date
from pathlib import Path

from app.rag.search import JsonBankSearch


def main():
    cases = json.loads(Path(__file__).with_name("data").joinpath("search_cases.json").read_text(encoding="utf-8"))
    search, output = JsonBankSearch(), []
    for case in cases:
        rows = search.search(case["query"], as_of=date.fromisoformat(case["as_of"]))
        expected = case["expected_document_id"]
        passed = any(row["document_id"] == expected and row["page_number"] == case.get("expected_page", 1) for row in rows) if expected else not rows
        output.append({"query": case["query"], "passed": passed, "found": [row["chunk_id"] for row in rows]})
    print(json.dumps({"cases": output, "passed": sum(row["passed"] for row in output), "total": len(output),
                      "notice": "Проверка маленького корпуса, не оценка качества на всех документах."}, ensure_ascii=False, indent=2))
    if not all(row["passed"] for row in output):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
