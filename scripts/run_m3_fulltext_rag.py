"""Run a new, explicitly authorized full-textbook 120-question four-condition batch."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts import run_m3_live_pilot as live
from scripts import run_m3_abc_research as campaign
from scripts.run_m3_rag_repair import _active_deepseek_profile, _probe_model_metadata
from scripts.ocr_full_textbook import digest, write_json
from evaluation.m3_acceptance import PROMPT_FILES, _prompt_fingerprint
from library.models import DocumentPage, ResourceRecord
from library.chunking import chunk_pages, chunking_version
from library.embeddings import HashingEmbedder, retrieval_index_profile
from runtime.storage.resource_store import SqliteResourceStore
from runtime.core.events import utc_now

SOURCE_HOME = ROOT.parent / '开发者测试.deepprof'
RESEARCH = ROOT / 'docs/experiments/m3-abc-research'
RAW_RESEARCH = SOURCE_HOME / 'experiments/m3-abc-research'
ANNOTATIONS = RAW_RESEARCH / 'bkt-rag-improvement-20261001/human-annotation'
NEW_REQUEST_CEILING = 364
TOTAL_REQUEST_CEILING = 691
FORMAL_BATCH_REQUEST_CEILING = 364  # Four preflight requests plus at most 360 formal generations.
PREFLIGHT_CASES = ('DSDEV-034', 'DSDEV-001', 'DSDEV-016', 'DSDEV-018')
RUNTIME_FILES = sorted(set(PROMPT_FILES) | {
    'api/app.py', 'api/sessions.py', 'graph/education/state.py',
    'graph/education/nodes/__init__.py', 'runtime/service.py',
    'skills/rag/__init__.py',
    'library/service.py', 'library/chunking.py', 'library/embeddings.py',
    'library/hybrid_retrieval.py', 'library/evidence_relevance.py', 'runtime/storage/resource_store.py',
    'tools/retrieval/search_textbook.py', 'scripts/run_m3_live_pilot.py',
    'scripts/run_m3_fulltext_rag.py', 'scripts/annotate_fulltext_rag.py',
    'evaluation/rag_relevance_metrics.py', 'evaluation/rag_citation_audit.py',
})

V3_BATCH = 'm3-fulltext-rag-20261004-v3'
ACTIVE_BATCH = 'm3-fulltext-rag-20261004-v4'


def authorization(batch_id: str) -> dict[str, Any]:
    if batch_id == ACTIVE_BATCH:
        return {'schema_version': 'deepprof-request-authorization-v1', 'batch_id': batch_id,
                'source': 'explicit_user_continue_after_v3_stop_20261004',
                'predecessor_batch': V3_BATCH, 'baseline_cumulative': 488,
                'baseline_fulltext': 161, 'additional_ceiling': 399, 'new_ceiling': 560,
                'cumulative_ceiling': 887,
                'phase_ceilings': {'preflight': 4, 'formal': 360, 'annotation': 35},
                'failures_and_pending_counted': True, 'automatic_retries': 0}
    if batch_id != V3_BATCH:
        return {'additional_ceiling': 364, 'new_ceiling': 364, 'cumulative_ceiling': 691,
                'phase_ceilings': {'preflight': 4, 'formal': 360, 'annotation': 0}}
    return {'schema_version': 'deepprof-request-authorization-v1', 'batch_id': batch_id,
            'source': 'explicit_user_plan_20261004', 'baseline_cumulative': 487,
            'baseline_fulltext': 160, 'additional_ceiling': 400, 'new_ceiling': 560,
            'cumulative_ceiling': 887,
            'phase_ceilings': {'preflight': 4, 'formal': 360, 'annotation': 36},
            'failures_and_pending_counted': True, 'automatic_retries': 0}


def runtime_fingerprint() -> dict[str, str]:
    return {name: digest(ROOT / name) for name in RUNTIME_FILES if (ROOT / name).exists()}


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding='utf-8-sig', newline='') as f:
        return list(csv.DictReader(f))


def make_cases(owner: Path = ANNOTATIONS / 'new-80-question-owner.csv') -> dict[str, Any]:
    _, old = live.live_cases(40)
    cases = [{**case, 'split': 'historical'} for case in old]
    questions = read_csv(owner)
    for row in questions:
        cases.append({'case_id': row['item_id'], 'split': row['split'],
                      'question_family_id': row['question_family_id'],
                      'category': 'new_question_cold_start', 'concept_id': row['concept_id'],
                      'user_turns': [row['question_text']], 'learning_goal': row['concept_name'],
                      'attempt_history': [], 'allowed_variants': [],
                      'm3_case_version': 'ds-fulltext-120-v1'})
    ids = [row['case_id'] for row in cases]
    counts = {split: sum(row['split'] == split for row in cases)
              for split in ('historical', 'development', 'sealed_test')}
    if len(set(ids)) != 120 or counts != {'historical': 40, 'development': 40, 'sealed_test': 40}:
        raise ValueError('fulltext_case_set_must_be_40_40_40_unique')
    families: dict[str, set[str]] = {}
    for row in cases:
        if row.get('question_family_id'):
            families.setdefault(row['question_family_id'], set()).add(row['split'])
    if any(len(values) != 1 for values in families.values()):
        raise ValueError('question_family_crosses_partitions')
    return {'version': 'ds-fulltext-120-v1', 'human_subjects': False,
            'reference_labels_excluded': True, 'counts': counts, 'cases': cases}


def build_index(ocr: Path, database: Path) -> dict[str, Any]:
    payload = json.loads(ocr.read_text(encoding='utf-8'))
    rows = payload['pages']
    count = payload['source']['pdf_pages']
    if count != 347 or [row['pdf_page'] for row in rows] != list(range(1, 348)):
        raise ValueError('complete_ordered_347_page_ocr_required')
    review_hash = digest(ocr)
    proof_path = database.parent / 'index-manifest.json'
    if database.exists():
        proof = json.loads(proof_path.read_text(encoding='utf-8'))
        if proof['ocr_sha256'] != review_hash or proof['database_sha256'] != digest(database):
            raise ValueError('existing_fulltext_index_lineage_changed')
        return proof
    pages = [DocumentPage(page=row['pdf_page'], text=row['corrected_text'],
                          printed_page=row.get('printed_page'), chapter=row.get('chapter', ''),
                          section=row.get('section', ''), reliable=row.get('index_eligible', False))
             for row in rows]
    if any(row.get('review_status') == 'pending_ai_visual_review' for row in rows):
        raise ValueError('fulltext_visual_review_not_complete')
    database.parent.mkdir(parents=True, exist_ok=True)
    store = SqliteResourceStore.open(str(database))
    now = utc_now()
    source_hash = payload['source']['source_sha256']
    record = ResourceRecord(resource_id='res_fulltext_' + review_hash[:20],
                            document_id='doc_fulltext_' + review_hash[:20],
                            course_id='ds.c_language.v1', type='textbook', title='数据结构（C语言版）全文OCR',
                            tags=['fulltext-ocr', 'ai-reviewed'], source_type='import',
                            source_url='local://course/data-structures-c-programming.pdf',
                            license='User supplied; internal research only; no redistribution',
                            content_hash=source_hash, status='active', owner_id='local', visibility='private',
                            created_at=now, updated_at=now)
    # The isolated loopback gateway creates distinct fixture learners. Course material
    # must be readable by them; the database and source artifacts remain local/private.
    record.visibility = 'public'
    embedder = HashingEmbedder()
    chunks = chunk_pages(pages, resource_id=record.resource_id, document_id=record.document_id,
                         chunk_size=800, overlap=120)
    vectors = embedder.embed([chunk.text for chunk in chunks])
    profile = retrieval_index_profile(embedder, chunking_version=chunking_version(800, 120))
    chunk_rows = [{'chunk_id': c.chunk_id, 'document_id': c.document_id, 'resource_id': c.resource_id,
                   'page': c.page, 'printed_page': c.printed_page, 'chapter': c.chapter,
                   'section': c.section, 'ordinal': c.ordinal, 'text': c.text,
                   'reliable': c.reliable, 'vector': vector} for c, vector in zip(chunks, vectors)]
    try:
        store.ingest(record.to_dict(), filename=payload['source']['source_filename'],
                     media_type='application/pdf', page_count=count, chunks=chunk_rows,
                     operation={'action': 'import', 'status': 'success', 'actor_id': 'local',
                                'parameters': {'retrieval_profile': profile, 'chunk_size': 800,
                                               'chunk_overlap': 120, 'chunking_strategy': 'char'}})
    finally:
        store.close()
    proof = {'schema_version': 'deepprof-fulltext-index-v1', 'ocr_sha256': review_hash,
             'source_sha256': source_hash, 'pdf_pages': count,
             'reliable_pages': sum(p.reliable for p in pages), 'chunks': len(chunks),
             'reliable_chunks': sum(c.reliable for c in chunks),
             'database_sha256': digest(database), 'index_snapshot': live._library_index_snapshot(database),
             'retrieval_profile': profile, 'private_local_only': True}
    write_json(proof_path, proof)
    return proof


def budget_state(raw_root: Path) -> dict[str, Any]:
    ledger = campaign._provider_budget_state(RESEARCH, RAW_RESEARCH)
    actual = 0
    for path in RAW_RESEARCH.glob('m3-fulltext-rag-*/runs/*/manifest.json'):
        progress = campaign._raw_run_progress(path.parent)
        actual += progress['actual']
    auth = authorization(raw_root.name)
    frozen_path = raw_root / 'frozen-config.json'
    if frozen_path.exists():
        frozen_budget = json.loads(frozen_path.read_text(encoding='utf-8'))
        if 'authorization' in frozen_budget:
            canonical = dict(frozen_budget)
            signature = canonical.pop('config_sha256', '')
            encoded = json.dumps(canonical, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if hashlib.sha256(encoded.encode()).hexdigest() != signature:
                raise RuntimeError('frozen_budget_config_hash_mismatch')
            if frozen_budget['authorization'] != auth:
                raise RuntimeError('frozen_budget_authorization_changed')
            auth = frozen_budget['authorization']
    phase_calls = {'preflight': 0, 'formal': 0, 'annotation': 0}
    for path in (raw_root / 'runs').glob('*/manifest.json'):
        manifest = json.loads(path.read_text(encoding='utf-8'))
        phase = manifest.get('phase', '')
        kind = phase if phase in {'preflight', 'annotation'} else 'formal'
        phase_calls[kind] += campaign._raw_run_progress(path.parent)['actual']
    return {'cumulative_requests': ledger['provider_calls'], 'new_requests': actual,
            'unresolved_requests': ledger['unresolved_provider_request_reservation'],
            'new_ceiling': auth['new_ceiling'], 'cumulative_ceiling': auth['cumulative_ceiling'],
            'batch_requests': sum(phase_calls.values()), 'additional_ceiling': auth['additional_ceiling'],
            'phase_requests': phase_calls, 'phase_ceilings': auth['phase_ceilings']}


def check_budget(state: dict[str, Any], needed: int = 0, *, phase: str | None = None) -> None:
    if state['unresolved_requests']:
        raise RuntimeError('unresolved_request_requires_reconciliation')
    if (state['new_requests'] + needed > state.get('new_ceiling', NEW_REQUEST_CEILING)
            or state['cumulative_requests'] + needed > state.get('cumulative_ceiling', TOTAL_REQUEST_CEILING)
            or state.get('batch_requests', 0) + needed > state.get('additional_ceiling', FORMAL_BATCH_REQUEST_CEILING)):
        raise RuntimeError('fulltext_provider_request_budget_exhausted')
    if phase and state.get('phase_requests', {}).get(phase, 0) + needed > state['phase_ceilings'][phase]:
        raise RuntimeError('fulltext_phase_request_budget_exhausted:' + phase)


def successful(summary: dict[str, Any], run_dir: Path, expected: int) -> bool:
    cells = [json.loads(p.read_text(encoding='utf-8')) for p in (run_dir / 'cells').glob('*.json')]
    return (len(cells) == expected and summary.get('observed_cells') == expected
            and all(c.get('status') == 'completed' and c.get('application_status') == 'completed'
                    and c.get('evaluation_status') in {'completed', 'not_applicable'}
                    and int(c.get('model_calls') or 0) <= 1 for c in cells))


def validate_wire_archive(directory: Path, expected_count: int) -> None:
    snapshots = list(directory.glob('*.json'))
    if len(snapshots) != expected_count:
        raise RuntimeError('wire_archive_request_count_mismatch')
    for path in snapshots:
        data = json.loads(path.read_text(encoding='utf-8'))
        body = data['body']
        if (body.get('thinking') != {'type': 'disabled'} or body.get('model') != campaign.MODEL
                or body.get('max_tokens') != 8192 or body.get('temperature') != .3):
            raise RuntimeError('actual_wire_configuration_drift')
        for key, value in [('body_sha256', body), ('messages_sha256', body.get('messages', []))]:
            canonical = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
            if hashlib.sha256(canonical.encode()).hexdigest() != data.get(key):
                raise RuntimeError('wire_archive_hash_mismatch')


def stop_gateway_tree(process: subprocess.Popen) -> str:
    """Windows venv wrappers can leave native Python children running."""
    if os.name == 'nt':
        if process.poll() is not None:
            return 'unconfirmed_tree_root_already_exited'
        result = subprocess.run(['taskkill', '/PID', str(process.pid), '/T', '/F'],
                                capture_output=True, timeout=12,
                                creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        try:
            process.wait(timeout=3)
        except subprocess.TimeoutExpired:
            return 'unconfirmed_tree_cleanup_timeout'
        return 'gateway_tree_stopped' if result.returncode == 0 else 'unconfirmed'
    process.terminate()
    try:
        process.wait(timeout=8)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)
    return 'gateway_stopped' if process.poll() is not None else 'unconfirmed'


def finalize_batch(process, logfile, previous, state, docs_root, raw_root) -> None:
    """Persist stop state and ledger even when process cleanup fails."""
    try:
        state['cleanup'] = stop_gateway_tree(process)
    except Exception as exc:
        state['cleanup'] = 'unconfirmed_cleanup_exception'
        state['cleanup_error'] = str(exc)
    finally:
        logfile.close()
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        write_json(docs_root / 'batch-state.json', state)
        write_json(docs_root / 'provider-request-budget.json', budget_state(raw_root))


def run_batch(ocr: Path, batch_id: str, *, resume: bool = False, prepare_only: bool = False) -> dict[str, Any]:
    if not __import__('re').fullmatch(r'm3-fulltext-rag-[A-Za-z0-9-]{8,64}', batch_id):
        raise ValueError('invalid_fulltext_batch_id')
    raw_root, docs_root = RAW_RESEARCH / batch_id, RESEARCH / batch_id
    if (docs_root / 'batch-state.json').exists():
        prior = json.loads((docs_root / 'batch-state.json').read_text(encoding='utf-8'))
        if prior.get('configuration_eligible') is False:
            raise RuntimeError('invalid_frozen_batch_cannot_resume; preserve lineage and create an authorized new batch')
    if raw_root.exists() and not resume:
        raise FileExistsError('batch_exists_use_explicit_resume')
    raw_root.mkdir(parents=True, exist_ok=True)
    docs_root.mkdir(parents=True, exist_ok=True)
    cases_file = raw_root / 'cases.json'
    cases = make_cases()
    if cases_file.exists() and json.loads(cases_file.read_text(encoding='utf-8')) != cases:
        raise ValueError('frozen_case_set_changed')
    write_json(cases_file, cases)
    source_database = raw_root / 'index/fulltext.sqlite'
    index = build_index(ocr, source_database)
    profile = _active_deepseek_profile()
    snapshot = campaign._selected_course_parameters()
    auth = authorization(batch_id)
    frozen = {'schema_version': 'deepprof-fulltext-rag-batch-v1', 'batch_id': batch_id,
              'cases_sha256': digest(cases_file), 'ocr_sha256': digest(ocr), 'ocr_path': str(ocr.resolve()),
              'index_sha256': index['index_snapshot']['sha256'], 'prompt_sha256': _prompt_fingerprint(),
              'runtime_files_sha256': runtime_fingerprint(),
              'provider_profile': profile['profile_id'], 'provider_profile_sha256': profile['profile_sha256'],
              'model': campaign.MODEL, 'bkt_snapshot': snapshot, 'temperature': .3,
              'max_output_tokens': 8192, 'max_retries': 0, 'thinking_enabled': False,
              'annotation_configuration': {'model': campaign.MODEL, 'temperature': 0,
                  'max_tokens': 8192, 'thinking': {'type': 'disabled'}, 'stream': False,
                  'retrieval_batch_size': 640, 'question_batch_size': 20, 'citation_batch_size': 384},
              'conditions': [name for name, _ in campaign.RAG_CONDITIONS], 'planned_cells': 480,
              'new_request_ceiling': auth['new_ceiling'], 'total_request_ceiling': auth['cumulative_ceiling'],
              'authorization': auth,
              'retrieval': {'strategy': 'hybrid_rrf', 'candidate_k': 50, 'rrf_constant': 60, 'top_k': 5}}
    canonical = json.dumps(frozen, ensure_ascii=False, sort_keys=True, separators=(',', ':'))
    frozen['config_sha256'] = hashlib.sha256(canonical.encode()).hexdigest()
    config_path = raw_root / 'frozen-config.json'
    if config_path.exists() and json.loads(config_path.read_text(encoding='utf-8')) != frozen:
        raise ValueError('frozen_batch_configuration_changed')
    write_json(config_path, frozen)
    write_json(docs_root / 'frozen-config.json', frozen)
    write_json(raw_root / 'request-authorization.json', auth)
    write_json(docs_root / 'request-authorization.json', auth)
    reproduction = raw_root / 'reproduction'
    for relative in RUNTIME_FILES:
        if not (ROOT / relative).exists():
            continue
        dest = reproduction / 'prompt-source' / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(ROOT / relative, dest)
    metadata = _probe_model_metadata(profile['profile_id'])
    check_budget(budget_state(raw_root), 0 if resume else FORMAL_BATCH_REQUEST_CEILING)
    if prepare_only:
        state = {'batch_id': batch_id, 'status': 'prepared', 'configuration_eligible': True,
                 'preflight_passed': False, 'conditions': {}, 'cleanup': 'no_service_started',
                 'config_sha256': frozen['config_sha256']}
        write_json(docs_root / 'batch-state.json', state)
        write_json(docs_root / 'provider-request-budget.json', budget_state(raw_root))
        write_json(docs_root / 'runtime-evidence.json', {'metadata': metadata, 'source_index': index})
        return state
    runtime_home = raw_root / 'runtime'
    caps = campaign._copy_runtime_configuration(runtime_home, max_output_tokens=8192,
                                                 profile_id=profile['profile_id'], thinking_parameter='thinking.type')
    port = campaign._free_loopback_port()
    base = f'http://127.0.0.1:{port}'
    env, _ = campaign._campaign_environment()
    changes = {'DEEPPROF_HOME': str(runtime_home), 'DEEPPROF_SQLITE_PATH': str(runtime_home / 'sessions.sqlite'),
               'DEEPPROF_API_HOST': '127.0.0.1', 'DEEPPROF_API_PORT': str(port),
               'DEEPPROF_LLM_MAX_TOKENS': '8192', 'DEEPPROF_LLM_MAX_RETRIES': '0',
               'DEEPPROF_STREAM_INCLUDE_USAGE': 'true', 'DEEPPROF_RETRIEVAL_STRATEGY': 'hybrid_rrf',
               'DEEPPROF_RESEARCH_REQUEST_ARCHIVE': str(reproduction / 'requests'),
               'DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED': 'true'}
    env.update(changes)
    previous = {key: os.environ.get(key) for key in changes}
    logfile = (raw_root / 'gateway.log').open('ab')
    kwargs = {'cwd': ROOT, 'env': env, 'stdout': logfile, 'stderr': subprocess.STDOUT}
    if os.name == 'nt':
        kwargs['creationflags'] = getattr(subprocess, 'CREATE_NO_WINDOW', 0)
    process = subprocess.Popen([sys.executable, '-m', 'uvicorn', 'api.app:app', '--host', '127.0.0.1',
                                '--port', str(port), '--log-level', 'warning'], **kwargs)
    os.environ.update(changes)
    state: dict[str, Any] = {'batch_id': batch_id, 'status': 'running', 'conditions': {},
                             'preflight_passed': False, 'configuration_eligible': True,
                             'gateway_pid': process.pid, 'gateway_port': port, 'cleanup': 'pending'}
    write_json(docs_root / 'batch-state.json', state)
    try:
        campaign._wait_gateway(process, base, logfile)
        library_snapshot = live._library_index_snapshot(runtime_home / 'sessions.sqlite')
        if not library_snapshot['active_course_textbooks']:
            live._copy_library_index(source_database, runtime_home / 'sessions.sqlite')
        write_json(docs_root / 'runtime-evidence.json', {'metadata': metadata, 'capabilities': caps,
                                                        'source_index': index, 'loopback_gateway': True})
        def guard_cell() -> None:
            if runtime_fingerprint() != frozen['runtime_files_sha256'] or digest(ocr) != frozen['ocr_sha256']:
                raise RuntimeError('frozen_runtime_or_ocr_changed')
            if (digest(cases_file) != frozen['cases_sha256']
                    or live._library_index_snapshot(source_database)['sha256'] != frozen['index_sha256']
                    or live._library_index_snapshot(runtime_home / 'sessions.sqlite')['sha256'] != frozen['index_sha256']
                    or campaign._selected_course_parameters() != frozen['bkt_snapshot']):
                raise RuntimeError('frozen_cases_index_or_bkt_changed')
            if _active_deepseek_profile()['profile_sha256'] != frozen['provider_profile_sha256']:
                raise RuntimeError('frozen_provider_configuration_changed')
            profiles = live.request_json(base, '/providers')
            active = next((r for r in profiles if r.get('profile_id') == profile['profile_id']), {})
            if not active.get('enabled', True):
                raise RuntimeError('isolated_provider_disabled')
            current_budget = budget_state(raw_root)
            generation_calls = current_budget['phase_requests']['preflight'] + current_budget['phase_requests']['formal']
            validate_wire_archive(reproduction / 'requests', generation_calls)
            check_budget(current_budget, int(live.expected_provider_calls_per_cell(options)),
                         phase='preflight' if phase == 'preflight' else 'formal')
        for phase, ids, options, planned in [
                ('preflight', list(PREFLIGHT_CASES), {'retrieval_enabled': True, 'evidence_constraint': True}, 4),
                *[(name, None, options, 120) for name, options in campaign.RAG_CONDITIONS]]:
            if phase != 'preflight' and not state['preflight_passed']:
                raise RuntimeError('formal_run_requires_successful_preflight')
            if frozen['prompt_sha256'] != _prompt_fingerprint() or digest(ocr) != frozen['ocr_sha256']:
                raise RuntimeError('frozen_prompt_or_ocr_changed')
            if runtime_fingerprint() != frozen['runtime_files_sha256']:
                raise RuntimeError('frozen_runtime_sources_changed')
            if _active_deepseek_profile()['profile_sha256'] != frozen['provider_profile_sha256']:
                raise RuntimeError('frozen_provider_configuration_changed')
            check_budget(budget_state(raw_root), 0 if phase != 'preflight' else 4)
            run_id = f'{batch_id}-{phase}'
            run_dir = raw_root / 'runs' / run_id
            is_resume = (run_dir / 'manifest.json').exists()
            budget = 0 if not live.expected_provider_calls_per_cell(options) else planned
            state['active_phase'] = phase
            write_json(docs_root / 'batch-state.json', state)
            summary = live.run(api_url=base, run_id=run_id, resume=is_resume,
                               output_root=raw_root / 'runs', case_count=planned, case_ids=ids,
                               cases_file=cases_file, phase=phase, provider_profile=profile['profile_id'],
                               model=campaign.MODEL, max_output_tokens=8192, request_budget=budget,
                               bkt_snapshot=snapshot, groups=('C',), m3_evidence_options=options,
                               stop_on_failed_cell=True, experiment_config_sha256=frozen['config_sha256'],
                               retrieval_strategy='hybrid_rrf', cell_guard=guard_cell)
            current_budget = budget_state(raw_root)
            recorded_calls = current_budget['phase_requests']['preflight'] + current_budget['phase_requests']['formal']
            validate_wire_archive(reproduction / 'requests', recorded_calls)
            passed = successful(summary, run_dir, planned)
            if phase == 'preflight':
                preflight_cells = [json.loads(p.read_text(encoding='utf-8'))
                                   for p in (run_dir / 'cells').glob('*.json')]
                passed = passed and any(int(c.get('model_calls') or 0) > 0 and
                                        int(c.get('locatable_references') or 0) > 0
                                        for c in preflight_cells)
                state['preflight_passed'] = passed
            else:
                state['conditions'][phase] = {'run_id': run_id, 'passed': passed,
                                               'observed_cells': summary['observed_cells']}
            write_json(docs_root / f'{phase}-summary.json', summary)
            write_json(docs_root / 'provider-request-budget.json', budget_state(raw_root))
            if not passed:
                raise RuntimeError(f'phase_failed:{phase}')
        state['status'] = 'completed'
        state['formal_cells'] = sum(row['observed_cells'] for row in state['conditions'].values())
    except BaseException as exc:
        state['status'] = 'stopped'
        state['reason'] = str(exc)
        raise
    finally:
        finalize_batch(process, logfile, previous, state, docs_root, raw_root)
    return state


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ocr', type=Path, required=True)
    parser.add_argument('--batch-id', default=ACTIVE_BATCH)
    parser.add_argument('--resume', action='store_true')
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    print(json.dumps(run_batch(args.ocr, args.batch_id, resume=args.resume, prepare_only=args.prepare_only), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
