"""Resumable, page-preserving OCR of a locally supplied textbook (no publication)."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import shutil
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from library.markitdown_converter import MAX_PAGES, MAX_RENDER_PIXELS


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open('rb') as f:
        for block in iter(lambda: f.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    with temporary.open('w', encoding='utf-8', newline='\n') as stream:
        stream.write(json.dumps(data, ensure_ascii=False, indent=2) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def batches(count: int, size: int = MAX_PAGES) -> list[tuple[int, int]]:
    if count < 1 or not 1 <= size <= MAX_PAGES:
        raise ValueError('invalid_batch_configuration')
    return [(first, min(first + size - 1, count)) for first in range(1, count + 1, size)]


def merge_pages(output: Path, config: dict[str, Any]) -> dict[str, Any]:
    count = config['pdf_pages']
    pages = []
    for number in range(1, count + 1):
        path = output / 'pages' / f'{number:03d}.json'
        row = json.loads(path.read_text(encoding='utf-8'))
        if row['pdf_page'] != number or row['source_sha256'] != config['source_sha256']:
            raise ValueError('page_lineage_mismatch')
        image = output / row['image_path']
        if digest(image) != row['image_sha256']:
            raise ValueError('page_image_hash_mismatch')
        pages.append(row)
    payload = {'schema_version': 'deepprof-fulltext-ocr-v1', 'source': config,
               'status': 'ocr_complete; visual_review_pending', 'pages': pages,
               'coverage': {'expected_pages': count, 'recognized_pages': len(pages),
                            'missing_pages': [], 'duplicate_pages': []},
               'review_provenance': 'ai; not human review'}
    write_json(output / 'textbook-full-ocr.json', payload)
    markdown = '\n\n'.join(f"<!-- PDF page {p['pdf_page']} -->\n\n{p['corrected_text']}" for p in pages)
    (output / 'textbook-full-ocr.md').write_text(markdown + '\n', encoding='utf-8')
    return payload


def run(pdf: Path, output: Path, scale: float = 2.0) -> dict[str, Any]:
    import pypdfium2 as pdfium
    from rapidocr_onnxruntime import RapidOCR
    if not 0.5 <= scale <= 4:
        raise ValueError('render_scale_out_of_bounds')
    pdf = pdf.resolve(strict=True)
    source_hash = digest(pdf)
    with pdfium.PdfDocument(str(pdf)) as document:
        config = {'source_filename': pdf.name, 'source_sha256': source_hash,
                  'pdf_pages': len(document), 'render_scale': scale, 'batch_size': MAX_PAGES,
                  'ocr_engine': 'rapidocr_onnxruntime', 'private_local_only': True}
        manifest = output / 'ocr-config.json'
        if manifest.exists() and json.loads(manifest.read_text(encoding='utf-8')) != config:
            raise ValueError('ocr_resume_configuration_changed')
        write_json(manifest, config)
        # Reuse one engine; bound thread count avoids ONNX oversubscription on Windows.
        engine = RapidOCR(intra_op_num_threads=4, inter_op_num_threads=1)
        for first, last in batches(len(document)):
            for number in range(first, last + 1):
                target = output / 'pages' / f'{number:03d}.json'
                if target.exists():
                    try:
                        row = json.loads(target.read_text(encoding='utf-8'))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        row = None
                    if row:
                        if row.get('source_sha256') != source_hash or row.get('pdf_page') != number:
                            raise ValueError(f'ocr_page_resume_lineage_mismatch:{number}')
                        if ((output / row['image_path']).is_file()
                                and digest(output / row['image_path']) == row.get('image_sha256')):
                            continue
                    backup = output / 'recovery' / target.name
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if not backup.exists():
                        shutil.copy2(target, backup)
                    print(f'Recovering incomplete page {number}', flush=True)
                page = document[number - 1]
                width, height = page.get_size()
                if width * height * scale ** 2 > MAX_RENDER_PIXELS:
                    raise ValueError(f'page_render_exceeds_pixel_limit:{number}')
                bitmap = page.render(scale=scale)
                image = bitmap.to_pil()
                image_path = output / 'images' / f'{number:03d}.png'
                image_path.parent.mkdir(parents=True, exist_ok=True)
                image.save(image_path)
                recognized, timing = engine(image)
                lines = [{'box': [[float(v) for v in point] for point in item[0]],
                          'text': str(item[1]), 'confidence': float(item[2])}
                         for item in recognized or []]
                text = '\n'.join(line['text'] for line in lines)
                # Page footer numbers are candidates only, never a global offset.
                footers = [line for line in lines if min(p[1] for p in line['box']) > image.height * .88
                           and re.fullmatch(r'\d{1,3}', line['text'].strip())]
                footer = max(footers, key=lambda x: x['confidence']) if footers else None
                row = {'pdf_page': number, 'source_sha256': source_hash,
                       'image_path': image_path.relative_to(output).as_posix(),
                       'image_sha256': digest(image_path), 'image_size': list(image.size),
                       'raw_text': text, 'corrected_text': text, 'lines': lines,
                       'ocr_confidence': sum(x['confidence'] for x in lines) / len(lines) if lines else None,
                       'printed_page_candidate': int(footer['text']) if footer else None,
                       'printed_page': None, 'chapter': '', 'section': '',
                       'review_status': 'pending_ai_visual_review', 'review_source': 'ai',
                       'human_review_status': 'not_reviewed', 'corrections': []}
                write_json(target, row)
                bitmap.close()
                page.close()
                print(f'OCR {number}/{len(document)}; lines={len(lines)}; confidence={row["ocr_confidence"]}', flush=True)
            write_json(output / 'batches' / f'{first:03d}-{last:03d}.json',
                       {'first': first, 'last': last, 'source_sha256': source_hash,
                        'pages': [{'pdf_page': n, 'sha256': digest(output / 'pages' / f'{n:03d}.json')}
                                  for n in range(first, last + 1)], 'status': 'ocr_complete'})
        return merge_pages(output, config)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('pdf', type=Path)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--scale', type=float, default=2.0)
    args = parser.parse_args()
    result = run(args.pdf, args.output_dir, args.scale)
    print(json.dumps(result['coverage'], ensure_ascii=False))


if __name__ == '__main__':
    main()
