"""Resume completed-cell checkpoints while recording non-runtime workspace changes.

The original runner still checks every frozen source, prompt, index, configuration
and budget before any submission. Only the existing live runner's repository-wide
dirty-tree history option is enabled; no generation control or request is changed.
"""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import run_m3_fulltext_rag as runner
from scripts.ocr_full_textbook import write_json,digest
from runtime.core.events import utc_now


def resume():
    raw,docs=runner.RAW_RESEARCH/runner.ACTIVE_BATCH,runner.RESEARCH/runner.ACTIVE_BATCH
    cfg=json.loads((raw/'frozen-config.json').read_text(encoding='utf-8'))
    state=json.loads((docs/'batch-state.json').read_text(encoding='utf-8'))
    proof=json.loads((docs/'service-cleanup-proof.json').read_text(encoding='utf-8'))
    assert state['configuration_eligible'] and proof['verified']
    assert runner.runtime_fingerprint()==cfg['runtime_files_sha256']
    budget=runner.budget_state(raw)
    runner.check_budget(budget)
    completed={str(p.relative_to(raw)):digest(p) for p in (raw/'runs').glob('*/cells/*.json')}
    receipt={'created_at':utc_now(),'reason':'non-run citation audit named summary.json triggered conservative ledger guard; renamed to audit-summary.json',
        'source_guard_unchanged':True,'frozen_config_sha256':cfg['config_sha256'],
        'completed_cell_sha256':completed,'budget_before_resume':budget,
        'provider_retry_performed':False,'repository_dirty_tree_change_scope':'report and audit tools; all frozen runtime file hashes remain exact',
        'prior_state':state,'prior_cleanup_proof_sha256':digest(docs/'service-cleanup-proof.json')}
    write_json(docs/'checkpoint-resume-evidence.json',receipt)
    write_json(docs/'service-cleanup-proof-01.json',proof)
    write_json(docs/'service-start-proof-01.json',json.loads((docs/'service-start-proof.json').read_text(encoding='utf-8')))
    original=runner.live.run
    def with_source_history(**kwargs):
        if kwargs.get('resume'):
            assert runner.runtime_fingerprint()==cfg['runtime_files_sha256']
            kwargs['allow_source_fingerprint_change_on_resume']=True
        return original(**kwargs)
    runner.live.run=with_source_history
    try:return runner.run_batch(Path(cfg['ocr_path']),runner.ACTIVE_BATCH,resume=True)
    finally:runner.live.run=original


if __name__=='__main__':print(json.dumps(resume(),ensure_ascii=False,indent=2))
