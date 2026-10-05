"""Retain the old report and stopped evidence before retiring active placeholders."""
from pathlib import Path
import json
import shutil
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.nomiracl_checkpoints import sha,write_json
OUT=ROOT/'docs/experiments/bkt-rag-improvement-20261001'
PRIVATE=ROOT.parent/'开发者测试.deepprof/experiments/m3-abc-research/bkt-rag-improvement-20261001'

def main():
    archive=PRIVATE/'archive-before-nomiracl-20261004';archive.mkdir(exist_ok=True)
    extra=[OUT/'data/nomiracl-evaluation-status.json',OUT/'data/rag-ablation-status.csv',OUT/'figures/make_figures.R',
           OUT/'data/coverage.csv',*[OUT/'figures'/f'figure-01-data-coverage.{s}' for s in ['svg','pdf','png']]]
    for source in extra:
        target=archive/source.relative_to(OUT)
        if source.exists() and not target.exists():
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(source,target)
    old=json.loads((archive/'archive-manifest.json').read_text(encoding='utf-8')) if (archive/'archive-manifest.json').exists() else None
    entries={str(p.relative_to(archive)).replace('\\','/'):{'sha256':sha(p),'bytes':p.stat().st_size}
             for p in sorted(archive.rglob('*')) if p.is_file() and p.name!='archive-manifest.json'}
    if old and any(entries.get(k)!=v for k,v in old['files'].items()):raise ValueError('archive_changed')
    assert any(k.endswith('M3-BKT-RAG-改进实验报告.md') for k in entries)
    assert any(k.endswith('M3-BKT-RAG-改进实验报告.pdf') for k in entries)
    if not old or old['files']!=entries:write_json(archive/'archive-manifest.json',{'status':'verified_retained','files':entries,
                       'note':'Previous report, placeholders, original runtime and stopped-batch evidence retained; original experiment evidence untouched.'})
    retired=[]
    for name in ['figure-04-rag-ablation-status','figure-05-retrieval-quality-and-latency']:
        for suffix in ['svg','pdf','png']:
            source=OUT/'figures'/f'{name}.{suffix}'
            if not source.exists():continue
            copies=[p for p in archive.rglob(source.name) if p.is_file()]
            if not copies or any(sha(p)!=sha(source) for p in copies):raise ValueError('unverified_archive_copy')
            assert source.resolve().is_relative_to(OUT.resolve())
            retired.append({'path':str(source),'sha256':sha(source)});source.unlink()
    source=OUT/'data/rag-ablation-status.csv'
    if source.exists():
        assert sha(source)==entries['data/rag-ablation-status.csv']['sha256']
        source.unlink()
    proof=OUT/'validation/historical-placeholder-cleanup.json'
    prior=json.loads(proof.read_text(encoding='utf-8')) if proof.exists() else {}
    write_json(proof,{'status':'completed','archive_manifest':str(archive/'archive-manifest.json'),
                               'archive_manifest_sha256':sha(archive/'archive-manifest.json'),'retired_active_figures':prior.get('retired_active_figures',[])+retired,
                               'external_model_requests':0,'historical_experiment_source_deleted':False})
    print(json.dumps({'archived_files':len(entries),'retired_active_files':len(retired)}))
if __name__=='__main__':main()
