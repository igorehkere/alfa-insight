from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import tempfile
from datetime import date
from pathlib import Path

from app.rag.models import Chunk, Corpus, Document, PageExtraction
from app.rag.search import read_corpus


class ExtractionError(RuntimeError):
    pass


def normalize(text: str) -> str:
    # Keep paragraph boundaries, decimal separators, dates and minus signs.
    return "\n\n".join(re.sub(r"[ \t]+", " ", p).strip() for p in text.split("\n\n") if p.strip())


def split_page(text: str, size: int = 1200, overlap: int = 150) -> list[str]:
    if not 0 <= overlap < size or size < 200:
        raise ValueError("Неверные параметры разбиения")
    text = normalize(text)
    result, start = [], 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            cut = max(text.rfind("\n\n", start + size // 2, end), text.rfind(". ", start + size // 2, end))
            if cut > start:
                end = cut + 1
        result.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return [part for part in result if part]


def assess_quality(text: str) -> list[str]:
    """Heuristics flag failures; they never certify financial correctness."""
    issues = []
    visible = [c for c in text if not c.isspace()]
    if len(visible) < 40:
        issues.append("too_little_text")
    if visible and sum(c.isalpha() for c in visible) / len(visible) < 0.35:
        issues.append("low_letter_ratio_or_numeric_table")
    if "\ufffd" in text or any(ord(c) < 32 and c not in "\n\r\t" for c in text):
        issues.append("damaged_characters")
    return issues


def extract_pdf(path: Path, *, ocr: bool = False, language: str = "rus+eng",
                dpi: int = 300, image_dir: Path | None = None) -> list[dict]:
    try:
        import pymupdf
    except ImportError as exc:
        raise ExtractionError("Для PDF нужен PyMuPDF из окружения backend") from exc
    pages = []
    with pymupdf.open(path) as doc:
        if doc.needs_pass:
            raise ExtractionError("PDF защищён паролем")
        for number, page in enumerate(doc, 1):
            text, method = page.get_text(sort=True), "text"
            # A text header must not hide a scanned table or slide under it.
            image_area = sum(abs(r.width * r.height) for info in page.get_image_info() for r in [pymupdf.Rect(info["bbox"])])
            has_large_image = image_area > abs(page.rect.width * page.rect.height) * 0.25
            needs_ocr = len(text.strip()) < 30 or has_large_image
            if needs_ocr:
                if not ocr:
                    raise ExtractionError(f"Страница {number}: нужен OCR изображений. Укажите --ocr и установите rus+eng")
                try:
                    tp = page.get_textpage_ocr(language=language, dpi=dpi, full=True,
                        tessdata=os.getenv("TESSDATA_PREFIX"))
                    text, method = page.get_text(textpage=tp, sort=True), "ocr"
                except Exception as exc:
                    raise ExtractionError(f"Страница {number}: OCR недоступен; установите Tesseract и языковые данные {language}") from exc
            issues = assess_quality(text)
            if len(page.get_drawings()) > 20:
                issues.append("possible_chart_or_table")
            image_path = None
            if image_dir is not None and issues:
                image_dir.mkdir(parents=True, exist_ok=True)
                target = image_dir / f"page-{number}.png"
                page.get_pixmap(dpi=150, alpha=False).save(target)
                image_path = str(target.resolve())
            pages.append({"page_number": number, "text": normalize(text), "method": method,
                          "issues": issues, "image_path": image_path})
    return pages


def atomic_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    name = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as stream:
            name = stream.name
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(name, path)
    finally:
        if name and os.path.exists(name):
            os.unlink(name)


def import_pdf(pdf: Path, output: Path, *, document_id: str, title: str,
               published_at: date, source_url: str | None = None, ocr: bool = False,
               dpi: int = 300, language: str = "rus+eng", image_dir: Path | None = None) -> Corpus:
    if not 150 <= dpi <= 400:
        raise ValueError("OCR DPI должен быть в пределах 150–400")
    digest = hashlib.sha256(pdf.read_bytes()).hexdigest()
    old = read_corpus(output) if output.exists() else Corpus(documents=[], chunks=[])
    profile = f"pymupdf-v2:ocr={ocr}:dpi={dpi}:language={language}"
    if any(d.id == document_id and d.sha256 == digest and d.title == title and d.extraction_profile == profile
           and d.published_at == published_at and d.source_url == source_url
           and d.filename == pdf.name for d in old.documents):
        return old  # No repeated OCR, and already reviewed excerpts are preserved.
    # Extract before replacing anything: failure preserves an existing valid corpus.
    pages = extract_pdf(pdf, ocr=ocr, dpi=dpi, language=language, image_dir=image_dir)
    document = Document(id=document_id, title=title, filename=pdf.name,
                        published_at=published_at, sha256=digest,
                        source_url=source_url, page_count=len(pages), extraction_profile=profile)
    chunks = [Chunk(id=f"{document_id}:{digest[:12]}:p{page['page_number']}:c{i}",
                    document_id=document_id, page_number=page["page_number"], chunk_index=i,
                    text=text, extraction_method=page["method"], reviewed=False)
              for page in pages for i, text in enumerate(split_page(page["text"]))]
    corpus = Corpus(documents=[d for d in old.documents if d.id != document_id] + [document],
                    chunks=[c for c in old.chunks if c.document_id != document_id] + chunks,
                    pages=[p for p in old.pages if p.document_id != document_id] + [
                        PageExtraction(document_id=document_id, page_number=p["page_number"], method=p["method"],
                            quality_ok=not p.get("issues"), issues=p.get("issues", []), image_path=p.get("image_path"))
                        for p in pages])
    atomic_json(output, corpus.model_dump(mode="json"))
    return corpus


def main() -> None:
    parser = argparse.ArgumentParser(description="Локальный импорт PDF; без API и токенов")
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--id", required=True)
    parser.add_argument("--title", required=True)
    parser.add_argument("--date", type=date.fromisoformat, required=True)
    parser.add_argument("--source-url")
    parser.add_argument("--ocr", action="store_true")
    parser.add_argument("--dpi", type=int, default=300)
    parser.add_argument("--language", default="rus+eng")
    parser.add_argument("--image-dir", type=Path)
    args = parser.parse_args()
    try:
        corpus = import_pdf(args.pdf, args.output, document_id=args.id, title=args.title,
                            published_at=args.date, source_url=args.source_url, ocr=args.ocr,
                            dpi=args.dpi, language=args.language, image_dir=args.image_dir)
    except (ExtractionError, OSError, ValueError) as exc:
        parser.exit(2, f"{exc}\n")
    print(f"Документов: {len(corpus.documents)}, фрагментов: {len(corpus.chunks)}. Новые фрагменты требуют reviewed=true после сверки.")


if __name__ == "__main__":
    main()
