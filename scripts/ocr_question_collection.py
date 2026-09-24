"""Create a review-only local OCR candidate through DeepProf's MarkItDown skill core."""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from config.paths import deepprof_home
from library.markitdown_converter import convert_local


def run(pdf_path: Path, output: Path, first: int, last: int | None, scale: float) -> dict[str, object]:
    result = convert_local(str(pdf_path), first_page=first, last_page=last,
                           force_ocr=True, render_scale=scale)
    source_metadata = Path(str(result["metadata_path"]))
    payload = source_metadata.read_text(encoding="utf-8")
    output.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=output.name + ".", suffix=".tmp", dir=output.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8", newline="\n") as stream:
            stream.write(payload)
        os.replace(temporary, output)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    print(f"OCR {result['page_count']} pages; manual review required.", flush=True)
    print(f"Markdown: {result['markdown_path']}", flush=True)
    print(f"Metadata: {output}", flush=True)
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf", type=Path)
    parser.add_argument("--output", type=Path,
                        default=deepprof_home() / "course" / "question-bank-ocr.json")
    parser.add_argument("--first-page", type=int, default=1)
    parser.add_argument("--last-page", type=int)
    parser.add_argument("--scale", type=float, default=2.0)
    args = parser.parse_args()
    run(args.pdf, args.output, args.first_page, args.last_page, args.scale)


if __name__ == "__main__":
    main()
