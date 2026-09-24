"""Create a page-by-page, non-content audit inventory for the supplied textbook PDF."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

import pypdfium2 as pdfium


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def run(pdf_path: Path, home: Path, *, audit_ocr: bool = False) -> tuple[Path, Path]:
    doc = pdfium.PdfDocument(str(pdf_path))
    engine = None
    if audit_ocr:
        from rapidocr_onnxruntime import RapidOCR
        engine = RapidOCR()
    pages: list[dict[str, Any]] = []
    current_chapter = ""
    for index in range(len(doc)):
        textpage = doc[index].get_textpage()
        extracted_text = textpage.get_text_range()
        rows = []
        if engine is not None:
            bitmap = doc[index].render(scale=1.5).to_pil()
            recognized, _ = engine(bitmap)
            rows = sorted(recognized or [], key=lambda row: (row[0][0][1], row[0][0][0]))
        text = "\n".join(str(row[1]) for row in rows) if rows else extracted_text
        headings = re.findall(r"第\s*[一二三四五六七八九十百\d]+\s*章\s*([^\n]{1,30})", text)
        if headings:
            current_chapter = re.sub(r"\s+", " ", headings[0]).strip()
        width, height = bitmap.size if engine is not None else (0, 0)
        footer_candidates = []
        for row in rows:
            box, label = row[0], str(row[1]).strip()
            confidence = float(row[2]) if len(row) > 2 else 0.0
            numbers = re.fullmatch(r"[•·●]?\s*(\d{1,3})\s*[•·●]?", label)
            center_x = sum(float(point[0]) for point in box) / len(box)
            center_y = sum(float(point[1]) for point in box) / len(box)
            if numbers and 1 <= int(numbers.group(1)) <= 500 and center_y >= height * 0.86 and width * 0.3 <= center_x <= width * 0.7:
                footer_candidates.append((int(numbers.group(1)), confidence))
        footer_candidates.sort(key=lambda item: item[1], reverse=True)
        printed_page = footer_candidates[0][0] if footer_candidates and footer_candidates[0][1] >= 0.45 else None
        footer_confidence = round(footer_candidates[0][1], 3) if printed_page is not None else None
        manually_checked = (index + 1) == 19
        if manually_checked:
            printed_page = 9
        pages.append({
            "pdf_page": index + 1,
            "printed_page_candidate": printed_page,
            "printed_page_ocr_confidence": footer_confidence,
            "chapter_heading_candidate": (current_chapter or None) if audit_ocr else None,
            "status": "manually_verified" if manually_checked else ("candidate_pending_manual_visual_check" if printed_page is not None else "pending_manual_visual_check"),
            "manual_evidence": "rendered_sample_pdf_page_019" if manually_checked else None,
            "extracted_character_count": len(text),
        })
        if (index + 1) % 25 == 0 or index + 1 == len(doc):
            print(f"Page audit OCR {index + 1}/{len(doc)}", flush=True)
    payload = {
        "format_version": 1,
        "status": "manual_verification_incomplete",
        "source": {"filename": pdf_path.name, "sha256": sha256(pdf_path), "pdf_pages": len(doc),
                   "access_scope": "local_developer_test_only", "license_note": "User-provided; internal testing only; no repository or index redistribution."},
        "method": {"tool": "pypdfium2" + (" + RapidOCR" if audit_ocr else ""), "render_scale": 1.5 if audit_ocr else None,
                   "printed_page_candidates": (
                       "RapidOCR footer-region candidates; candidate confidence is recorded per page; OCR output is not manual verification; no fixed offset applied"
                       if audit_ocr else
                       "none inferred from embedded text; one rendered page checked manually; no fixed offset applied"
                   ),
                   "chapter_candidates": (
                       "explicit OCR page heading only; carried heading remains a candidate and pending manual page-by-page confirmation"
                       if audit_ocr else
                       "no OCR chapter candidates generated; pending manual page-by-page confirmation"
                   ),
                   "verified_pages": 1},
        "pages": pages,
    }
    out_dir = home / "course"
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path, csv_path = out_dir / "textbook-page-audit.json", out_dir / "textbook-page-audit.csv"
    json_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    with csv_path.open("w", newline="", encoding="utf-8-sig") as stream:
        writer = csv.DictWriter(stream, fieldnames=["pdf_page", "printed_page_candidate", "printed_page_ocr_confidence", "chapter_heading_candidate", "status", "manual_evidence", "extracted_character_count"])
        writer.writeheader()
        writer.writerows(pages)
    counts = {"pages": len(pages), "with_printed_page_candidate": sum(bool(item["printed_page_candidate"]) for item in pages),
              "with_text_layer": sum(item["extracted_character_count"] > 0 for item in pages),
              "manually_verified": sum(item["status"] == "manually_verified" for item in pages)}
    print(json.dumps({"json": str(json_path), "csv": str(csv_path), **counts}, ensure_ascii=False))
    return json_path, csv_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("DEEPPROF_HOME", "~/.deepprof")).expanduser())
    parser.add_argument("--ocr", action="store_true", help="run a full-page OCR pass; manual visual verification remains required")
    args = parser.parse_args()
    run(args.pdf, args.home, audit_ocr=args.ocr)


if __name__ == "__main__":
    main()
