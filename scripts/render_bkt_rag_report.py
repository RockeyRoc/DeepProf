"""Render every report page and hash the current PDF and preview images."""
from pathlib import Path
import sys
import fitz
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.ocr_full_textbook import digest,write_json

def main():
    out=ROOT/'docs/experiments/bkt-rag-improvement-20261001'
    pdf=out/'M3-BKT-RAG-改进实验报告.pdf'
    target=out/'validation/pdf-preview-fulltext'
    target.mkdir(parents=True,exist_ok=True)
    doc=fitz.open(pdf);images={}
    for n,page in enumerate(doc,1):
        image=target/f'{n:02d}.png'
        page.get_pixmap(matrix=fitz.Matrix(1.6,1.6)).save(image)
        images[image.name]=digest(image)
    write_json(target/'render-manifest.json',{'pdf_sha256':digest(pdf),'pages':len(doc),
        'image_sha256':images,'visual_review_status':'pending','reviewed_pages':[]})
    print({'pdf_pages':len(doc),'image_count':len(images)})
    doc.close()

if __name__=='__main__':main()
