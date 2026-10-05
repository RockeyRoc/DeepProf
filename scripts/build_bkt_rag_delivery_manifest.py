"""Hash the final BKT/RAG report, figures, tables, and source lineage."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/experiments/bkt-rag-improvement-20261001"
DATA = OUT / "data"
PRIVATE = ROOT.parent / "开发者测试.deepprof/experiments/m3-abc-research/bkt-rag-improvement-20261001"
BASE = ROOT / "docs/experiments/m3-abc-research/m3-abc-bkt-20260928T134542Z-a216df"
REPAIR = ROOT / "docs/experiments/m3-abc-research/m3-abc-ragfix-20261001T120000Z-d1a9f4"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def artifact(path: Path, label: str) -> dict[str, Any]:
    return {"label": label, "path": str(path.resolve()), "sha256": sha256(path), "bytes": path.stat().st_size}


def main() -> int:
    data_manifest_path = DATA / "figure-data-manifest.json"
    data_manifest = json.loads(data_manifest_path.read_text(encoding="utf-8"))
    data_manifest.setdefault('prior_source_lineage_sha256',dict(data_manifest.get('sources',{})))
    for bucket in ['artifacts','outputs']:
        for name in list(data_manifest.get(bucket,{})):
            if any(s in name for s in ['figure-04-rag-ablation-status','figure-05-retrieval-quality-and-latency','rag-ablation-status.csv']):
                del data_manifest[bucket][name]
    for name in list(data_manifest.get('sources',{})):
        path=Path(name)
        if not path.is_absolute():path=ROOT/path
        if path.is_file():data_manifest['sources'][name]=sha256(path)
    current_summary=json.loads((DATA/'fulltext-rag-summary.json').read_text(encoding='utf-8'))
    active=ROOT/'docs/experiments/m3-abc-research'/current_summary['batch_id']
    for path in [active/'frozen-config.json',active/'evaluation-summary.json',active/'provider-request-budget.json',
                 active/'preflight-diagnosis.json',Path(current_summary['configuration']['ocr_path'])]:
        if path.is_file():data_manifest.setdefault('sources',{})[str(path)]=sha256(path)
    for path in [*sorted(DATA.glob('*.csv')),*sorted(DATA.glob('*.json')),*sorted((OUT/'figures').glob('figure-*'))]:
        if path.is_file() and path!=data_manifest_path:data_manifest.setdefault('artifacts',{})[str(path.relative_to(OUT)).replace('\\','/')]=artifact(path,path.name)
    for name in list(data_manifest.get('outputs',{})):
        path=OUT/name if name.startswith('figures/') else DATA/name
        if path.is_file():data_manifest['outputs'][name]={'sha256':sha256(path),'bytes':path.stat().st_size}
    data_manifest['active_fulltext_batch']=current_summary['batch_id']
    data_manifest_path.write_text(json.dumps(data_manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    outputs: dict[str, Any] = {}

    fixed_files = [
        OUT / "M3-BKT-RAG-改进实验报告.md",
        OUT / "M3-BKT-RAG-改进实验报告.pdf",
        OUT / "NoMIRACL-复现说明.md",
        OUT / "validation/nomiracl-source-preflight.json",
        OUT / "figures/make_figures.R",
        OUT / "validation/validation-summary.json",
        ROOT / "scripts/export_bkt_rag_figure_data.py",
        ROOT / "scripts/build_bkt_rag_experiment_report.py",
        ROOT / "scripts/build_bkt_rag_human_package.py",
        ROOT / "scripts/validate_bkt_rag_annotations.py",
        ROOT / "scripts/build_bkt_rag_delivery_manifest.py",
        OUT / "tools/rag-requirements.lock",
        OUT / "tools/models/model-snapshots.json",
        PRIVATE / "human-annotation/package-manifest.json",
        PRIVATE / "inter-rater-results.json",
        PRIVATE / "course-retrieval/course-rag-method-candidates.csv",
        BASE / "experiment-config.json",
        BASE / "main/summary.json",
        BASE / "rag-ablation/retrieval-on_constraint-on/summary.json",
        REPAIR / "repair-state.json",
        REPAIR / "provider-request-budget.json",
        REPAIR / "prompt-guard-evidence.json",
        ROOT / "scripts/ocr_full_textbook.py",
        ROOT / "scripts/review_fulltext_ocr.py",
        ROOT / "scripts/build_fulltext_review_v3.py",
        ROOT / "scripts/record_v3_preflight_stop.py",
        ROOT / "scripts/record_fulltext_run_evidence.py",
        ROOT / "scripts/resume_fulltext_frozen_batch.py",
        ROOT / "scripts/reconcile_fulltext_question_scope.py",
        ROOT / "scripts/annotate_fulltext_retrieval_v2.py",
        ROOT / "scripts/reconcile_fulltext_retrieval_spotcheck.py",
        ROOT / "scripts/reconcile_fulltext_no_citation_locators.py",
        ROOT / "scripts/annotate_fulltext_citations_codex.py",
        ROOT / "scripts/local_citation_judgments.py",
        ROOT / "scripts/review_citation_sources_local.py",
        ROOT / "scripts/reconcile_fulltext_question_spans.py",
        ROOT / "scripts/verify_bkt_rag_delivery_manifest.py",
        ROOT / "scripts/fulltext_report_section.py",
        ROOT / "scripts/render_bkt_rag_report.py",
        ROOT / "scripts/build_fulltext_validation_summary.py",
        ROOT / "scripts/run_m3_fulltext_rag.py",
        ROOT / "scripts/annotate_fulltext_rag.py",
        ROOT / "scripts/summarize_fulltext_rag.py",
        ROOT / "scripts/export_fulltext_rag_figure_data.py",
        ROOT / "scripts/export_fulltext_ablation_comparisons.py",
        ROOT / "scripts/record_fulltext_configuration_stop.py",
        ROOT / "scripts/audit_fulltext_figure_geometry.py",
        ROOT / "scripts/validate_fulltext_delivery.py",
        ROOT / "tests/test_fulltext_rag.py",
        OUT / "figures/make_fulltext_figures.R",
        OUT / "figures/make_fulltext_ablation_figures.R",
        OUT / "figures/make_nomiracl_figures.R",
        ROOT / "scripts/run_nomiracl_zh_reranker.py",
        ROOT / "scripts/nomiracl_checkpoints.py",
        ROOT / "scripts/nomiracl_inference_worker.py",
        ROOT / "scripts/nomiracl_report_section.py",
        ROOT / "scripts/validate_nomiracl_full.py",
        ROOT / "scripts/export_nomiracl_figures.py",
        ROOT / "scripts/archive_rag_placeholders.py",
        ROOT / "scripts/publish_nomiracl_full.py",
        ROOT / "scripts/verify_nomiracl_resume.py",
        ROOT / "tests/test_nomiracl_full.py",
    ]
    for path in fixed_files:
        if path.is_file():
            try:
                key = str(path.relative_to(ROOT)).replace("\\", "/")
            except ValueError:
                key = f"private/{path.name}"
            outputs[key] = artifact(path, path.name)

    for path in sorted(DATA.glob("*.csv")) + sorted(DATA.glob("*.json")):
        key = str(path.relative_to(ROOT)).replace("\\", "/")
        outputs[key] = artifact(path, path.name)
    for path in sorted((OUT / "figures").glob("figure-*")):
        if path.is_file():
            key = str(path.relative_to(ROOT)).replace("\\", "/")
            outputs[key] = artifact(path, path.name)
    for path in [OUT/'validation/fulltext-validation.json',
                 OUT/'validation/pdf-preview-fulltext/render-manifest.json',
                 *sorted((OUT/'validation').glob('v*-final-tests.xml')),
                 *sorted((OUT/'validation/fulltext-figures').glob('*'))]:
        if path.is_file():
            outputs[str(path.relative_to(ROOT)).replace('\\','/')]=artifact(path,path.name)

    manifest = {
        "schema_version": "deepprof-bkt-rag-delivery-manifest-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "campaign_id": "bkt-rag-improvement-20261001",
        "report_updated_at_local": "2026-10-05",
        "source_lineage_sha256": data_manifest.get("sources", {}),
        "artifacts": outputs,
        "limitations": [
            "The private package manifest hashes reviewer workbooks and local-only source excerpts without copying them into the public experiment directory.",
            "Earlier RAG batches stopped at configuration guards and remain archived historical diagnostics; Algebra I full-master evaluation remains incomplete.",
            "NoMIRACL evaluates all available fixed judged candidates, not whole-corpus Recall or generated-answer correctness; 95 absent qrels including 35 positives are disclosed, with no query exclusion.",
            "Full-textbook OCR covers 347 pages; AI layout review is not character-perfect code/formula certification or personnel review.",
            "Current full-text results are derived from its frozen batch, request ledger and evaluation summary. Stopped batches provide historical diagnostics; missing semantic judgments and zero denominators remain N/A.",
        ],
    }
    summary=json.loads((DATA/'fulltext-rag-summary.json').read_text(encoding='utf-8'))
    current=ROOT/'docs/experiments/m3-abc-research'/summary['batch_id']
    fulltext_private=ROOT.parent/'开发者测试.deepprof/experiments/m3-abc-research'/summary['batch_id']
    ocr_file=Path(summary['configuration']['ocr_path'])
    ocr_private=ocr_file.parent
    for path in sorted(current.glob('*.json')):
        outputs[str(path.relative_to(ROOT)).replace('\\','/')]=artifact(path,path.name)
    if (current/'README.md').exists():
        outputs[str((current/'README.md').relative_to(ROOT)).replace('\\','/')]=artifact(current/'README.md','Current stop and continuation conditions')
    private_files=[fulltext_private/'frozen-config.json',fulltext_private/'cases.json',
                   fulltext_private/'index/fulltext.sqlite',fulltext_private/'index/index-manifest.json',
                   fulltext_private/'planned-matrix.csv',fulltext_private/'request-authorization.json',
                   fulltext_private/'service-cleanup-proof.json',
                   ocr_file,ocr_private/'textbook-full-ocr.md',ocr_private/'review-manifest.json',ocr_private/'screening.json']
    for sub in ['reproduction/requests','reproduction/prompt-source','reproduction/annotation-requests','reproduction/annotation-responses','reproduction/evaluator2','ai-annotation','runs']:
        private_files.extend(p for p in (fulltext_private/sub).rglob('*') if p.is_file())
    manifest['fulltext_private_lineage']={str(p):artifact(p,p.name) for p in private_files if p.is_file()}
    textbook=json.loads(ocr_file.read_text(encoding='utf-8'))
    manifest['textbook_source_pdf_sha256']=textbook['source']['source_sha256']
    manifest['textbook_page_image_sha256']={str(p['pdf_page']):p['image_sha256'] for p in textbook['pages']}
    manifest['fulltext_status']=json.loads((current/'batch-state.json').read_text(encoding='utf-8'))
    manifest['fulltext_budget']=summary['budget']
    manifest['fulltext_annotation_counts']=summary['annotation_counts']
    manifest['fulltext_historical_batches']={p.parent.name:{'status':json.loads(p.read_text(encoding='utf-8')),'sha256':sha256(p)}
        for p in sorted((ROOT/'docs/experiments/m3-abc-research').glob('m3-fulltext-rag-*/batch-state.json')) if p.parent!=current}
    retained={}
    for bid in manifest['fulltext_historical_batches']:
        folder=fulltext_private.parent/bid
        cfg_path=folder/'frozen-config.json'
        if not cfg_path.exists():continue
        frozen=json.loads(cfg_path.read_text(encoding='utf-8'))
        files=[cfg_path,folder/'cases.json',folder/'index/fulltext.sqlite',folder/'index/index-manifest.json',
               *sorted((folder/'reproduction/requests').glob('*.json'))]
        retained[bid]={'frozen_index_content_sha256':frozen['index_sha256'],
                      'files':{str(p):artifact(p,p.name) for p in files if p.is_file()}}
    manifest['retained_fulltext_private_lineage']=retained
    manifest['fulltext_validation']=json.loads((OUT/'validation/fulltext-validation.json').read_text(encoding='utf-8'))
    for path in [OUT/'validation/nomiracl-tests.xml',OUT/'validation/nomiracl-validation.json',
                 OUT/'validation/historical-placeholder-cleanup.json',OUT/'validation/local-process-cleanup.json',
                 *sorted((OUT/'validation/nomiracl-figures').glob('*'))]:
        if path.is_file():outputs[str(path.relative_to(ROOT)).replace('\\','/')]=artifact(path,path.name)
    nomiracl=DATA/'nomiracl-zh-evaluation.json'
    if nomiracl.exists():
        evaluation=json.loads(nomiracl.read_text(encoding='utf-8'));assert evaluation['status']=='completed'
        run=PRIVATE/'nomiracl-zh-full-20261004-v2'
        files=[p for p in run.rglob('*') if p.is_file() and not p.name.endswith('.tmp')]
        ds_path=PRIVATE/'public-data/nomiracl-zh-manifest.json'
        ds=json.loads(ds_path.read_text(encoding='utf-8'))
        files.extend([ds_path,*[Path(ds['local_path'])/r['path'] for r in ds['files']]])
        manifest['nomiracl_private_lineage']={str(p):artifact(p,p.name) for p in sorted(files)}
        manifest['nomiracl_status']=evaluation['status'];manifest['nomiracl_counts']=evaluation['counts']
        manifest['nomiracl_validation']=json.loads((OUT/'validation/nomiracl-validation.json').read_text(encoding='utf-8'))
    archive=PRIVATE/'archive-before-nomiracl-20261004'
    manifest['retired_report_archive']={str(p):artifact(p,p.name) for p in sorted(archive.rglob('*')) if p.is_file()}
    target = OUT / "delivery-manifest.json"
    target.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"manifest": str(target), "sha256": sha256(target),
        "artifact_count": len(outputs), "lineage_source_count": len(manifest["source_lineage_sha256"])}, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
