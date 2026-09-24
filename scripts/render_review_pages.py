"""Render explicitly selected source pages to the local review directory."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path

import pypdfium2 as pdfium


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("pages", nargs="+", type=int, help="one-based PDF page numbers")
    parser.add_argument("--home", type=Path, default=Path(os.environ.get("DEEPPROF_HOME", "~/.deepprof")).expanduser())
    parser.add_argument("--scale", type=float, default=2.5)
    args = parser.parse_args()
    doc = pdfium.PdfDocument(str(args.pdf))
    source_digest = hashlib.sha256(args.pdf.read_bytes()).hexdigest()
    output = args.home / "course" / "source-pages" / source_digest[:12]
    output.mkdir(parents=True, exist_ok=True)
    manifest = output / "render-manifest.json"
    prior_rows: dict[int, dict[str, object]] = {}
    if manifest.exists():
        try:
            prior = json.loads(manifest.read_text(encoding="utf-8"))
            if prior.get("source_pdf_sha256") == source_digest:
                prior_rows = {int(row["pdf_page"]): row for row in prior.get("pages", [])}
        except (OSError, json.JSONDecodeError, KeyError, TypeError, ValueError):
            prior_rows = {}
    for number in sorted(set(args.pages)):
        if number < 1 or number > len(doc):
            parser.error(f"page {number} is outside 1..{len(doc)}")
        path = output / f"pdf-page-{number:03d}.png"
        doc[number - 1].render(scale=args.scale).to_pil().save(path)
        prior_rows[number] = {
            "pdf_page": number,
            "path": str(path),
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "manual_review_status": prior_rows.get(number, {}).get("manual_review_status", "pending"),
            "source_pdf": args.pdf.name,
            "source_pdf_sha256": source_digest,
        }
    manifest.write_text(json.dumps({"source_pdf": args.pdf.name, "render_scale": args.scale,
        "source_pdf_sha256": source_digest,
        "pages": [prior_rows[key] for key in sorted(prior_rows)]}, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"pages_rendered": len(set(args.pages)), "manifest": str(manifest)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
