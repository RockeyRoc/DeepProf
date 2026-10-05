"""Preserve the failed preflight and record its local, pre-HTTP diagnosis."""
from __future__ import annotations
import json
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from graph.education.state import new_state
from graph.education.nodes import runtime_ctx
from runtime.core.errors import ProviderError, classify_external_failure
from runtime.providers.openai_compatible import OpenAICompatibleProvider
from runtime.providers.profiles import ProviderProfile
from runtime.providers.secrets import InMemorySecretStore
from scripts.run_m3_fulltext_rag import RAW_RESEARCH, RESEARCH, V3_BATCH, budget_state
from scripts.ocr_full_textbook import write_json, digest
from runtime.core.events import utc_now

def main():
    raw, docs = RAW_RESEARCH / V3_BATCH, RESEARCH / V3_BATCH
    if (docs/'preflight-diagnosis.json').exists():
        print((docs/'preflight-diagnosis.json').read_text(encoding='utf-8'))
        return
    proof = json.loads((docs/'service-cleanup-proof.json').read_text(encoding='utf-8-sig'))
    assert proof['verified'] and not proof['remaining_processes'] and not proof['remaining_listeners']
    # This command is run before repairing the live implementation. It performs
    # only body construction and the archive guard, never HTTP or generation.
    incoming = {'thinking_enabled':False, 'thinking_mode':'off',
                'require_explicit_thinking_mode':True, 'experiment_run':True}
    context = runtime_ctx(new_state(**incoming))
    profile = ProviderProfile(profile_id='local-diagnosis', display_name='local-diagnosis',
        default_model='deepseek-flash', base_url='https://example.invalid',
        model_capabilities={'deepseek-flash':{'reasoning_mode':'toggle','thinking_parameter':'thinking.type'}})
    provider = OpenAICompatibleProvider(profile, InMemorySecretStore())
    body = provider._body(provider._with_context_thinking(
        {'model':'deepseek-flash','messages':[],'max_tokens':8192,'temperature':.3}, context), stream=True)
    previous = os.environ.get('DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED')
    os.environ['DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED']='true'
    try:
        try:
            provider._archive_research_request(body, context)
            raise AssertionError('expected pre-HTTP rejection')
        except ProviderError as exc:
            rejection = exc.to_dict()
            missing_error_attribute = not hasattr(classify_external_failure(error=exc), 'message')
    finally:
        if previous is None: os.environ.pop('DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED',None)
        else: os.environ['DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED']=previous
    assert body.get('thinking') != {'type':'disabled'} and missing_error_attribute
    cells = list((raw/'runs').glob('*/cells/*.json'))
    wire = list((raw/'reproduction/requests').glob('*.json'))
    assert len(cells)==1 and not wire
    diagnosis={'schema_version':'deepprof-local-preflight-diagnosis-v1','checked_at':utc_now(),
        'batch_id':V3_BATCH,'http_requests_sent_by_diagnostic':0,
        'caller_flags':incoming,'graph_context_flags':{k:context.get(k) for k in incoming},
        'body_has_explicit_thinking_disabled':False,'archive_guard_rejection':rejection,
        'failure_event_recording_error':'FailureClassification has no message attribute',
        'v3_wire_archives':len(wire),'conservative_counted_attempts':1,
        'v3_external_model_posts':0,'v3_external_post_evidence':'Guard executes before archive and HTTP; frozen code reproduces rejection locally.',
        'cell_evidence':{str(p.relative_to(raw)):digest(p) for p in cells},
        'configuration_changes_require_new_freeze':True,'automatic_retry_performed':False}
    write_json(docs/'preflight-diagnosis.json',diagnosis)
    state_path=docs/'batch-state.json'
    old=json.loads(state_path.read_text(encoding='utf-8'))
    write_json(docs/'transient-state-before-cleanup.json',old)
    state={**old,'status':'stopped_preflight_configuration_guard',
        'configuration_eligible':False,'preflight_passed':False,'formal_cells':0,
        'reason':'preflight_thinking_context_lost_before_http; failure_event_attribute_error',
        'cleanup':'gateway_tree_and_port_verified_closed','stopped_at':utc_now(),
        'conservative_failed_attempts':1,'external_posts':0,
        'diagnosis_sha256':digest(docs/'preflight-diagnosis.json'),
        'cleanup_proof_sha256':digest(docs/'service-cleanup-proof.json')}
    write_json(state_path,state)
    write_json(docs/'provider-request-budget.json',budget_state(raw))
    write_json(raw/'service-cleanup-proof.json',proof)
    print(json.dumps({'state':state,'budget':budget_state(raw)},ensure_ascii=False,indent=2))

if __name__=='__main__':main()
