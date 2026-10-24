"""Check local OCR installation without downloading models or calling LLM."""
import argparse
import json
import os
import shutil
import subprocess
from pathlib import Path


def check():
    binary = shutil.which("tesseract")
    report = {"tesseract": binary, "tessdata": os.getenv("TESSDATA_PREFIX"), "languages": [], "pymupdf": False}
    if binary:
        result = subprocess.run([binary, "--list-langs"], capture_output=True, text=True, timeout=10)
        report["languages"] = [line.strip() for line in result.stdout.splitlines() if line.strip() in {"rus", "eng"}]
    elif report["tessdata"]:
        report["languages"] = [lang for lang in ("rus", "eng") if (Path(report["tessdata"]) / f"{lang}.traineddata").is_file()]
    try:
        import pymupdf
        report["pymupdf"] = True
        # Exercise the actual OCR engine too, rather than checking package names only.
        with pymupdf.open() as document:
            page = document.new_page()
            page.insert_text((72, 72), "Equipment financing")
            textpage = page.get_textpage_ocr(language="rus+eng", full=True, tessdata=os.getenv("TESSDATA_PREFIX"))
            report["ocr_smoke"] = bool(page.get_text(textpage=textpage).strip())
    except Exception:
        report["ocr_smoke"] = False
    report["ok"] = report["pymupdf"] and report.get("ocr_smoke", False) and set(report["languages"]) == {"rus", "eng"}
    return report


def main():
    argparse.ArgumentParser(description=__doc__).parse_args()
    report = check()
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["ok"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
