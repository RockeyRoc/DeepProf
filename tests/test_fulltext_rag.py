from __future__ import annotations
import json
from pathlib import Path
import pytest

from scripts.ocr_full_textbook import batches, merge_pages, write_json, digest
from scripts.run_m3_fulltext_rag import make_cases, check_budget, successful, build_index
from scripts.validate_bkt_rag_annotations import validate_ai_csv, compare, CITATION_LABELS
from runtime.providers.openai_compatible import OpenAICompatibleProvider


def test_replacement_batch_debits_failed_predecessor_without_expanding_budget():
    from scripts.run_m3_fulltext_rag import authorization, ACTIVE_BATCH, V3_BATCH
    prior, current = authorization(V3_BATCH), authorization(ACTIVE_BATCH)
    assert current['baseline_cumulative'] == prior['baseline_cumulative'] + 1
    assert current['additional_ceiling'] == prior['additional_ceiling'] - 1
    assert current['baseline_cumulative'] + current['additional_ceiling'] == 887
    assert sum(current['phase_ceilings'].values()) == 399
    assert current['phase_ceilings']['annotation'] == 35
    state = {'cumulative_requests': 886, 'new_requests': 559, 'unresolved_requests': 0,
             'batch_requests': 398, **{k: current[k] for k in
                 ['new_ceiling','cumulative_ceiling','additional_ceiling','phase_ceilings']},
             'phase_requests': {'preflight': 4, 'formal': 360, 'annotation': 34}}
    check_budget(state, 1, phase='annotation')
    with pytest.raises(RuntimeError, match='budget_exhausted'):
        check_budget(state, 2, phase='annotation')
    with pytest.raises(RuntimeError, match='phase_request_budget_exhausted'):
        check_budget(state, 1, phase='preflight')


def test_fulltext_batch_boundaries():
    assert batches(347) == [(1, 50), (51, 100), (101, 150), (151, 200), (201, 250), (251, 300), (301, 347)]
    with pytest.raises(ValueError):
        batches(347, 51)


def test_ocr_recovers_corrupt_page_then_resumes_without_repeating_valid_pages(tmp_path, monkeypatch):
    from PIL import Image
    import pypdfium2
    import rapidocr_onnxruntime
    from scripts.ocr_full_textbook import run
    source=tmp_path/'source.bin';source.write_bytes(b'local source fixture')
    output=tmp_path/'ocr';image=output/'images/001.png';image.parent.mkdir(parents=True)
    Image.new('RGB',(20,20),'white').save(image)
    write_json(output/'pages/001.json',{'pdf_page':1,'source_sha256':digest(source),
        'image_path':'images/001.png','image_sha256':digest(image),'corrected_text':'verified first page'})
    bad=output/'pages/002.json';bad.write_text('{',encoding='utf-8')
    rendered=[];fail_once=[True]
    class Bitmap:
        def to_pil(self):return Image.new('RGB',(20,20),'white')
        def close(self):pass
    class Page:
        def __init__(self,n):self.n=n
        def get_size(self):return (10,10)
        def render(self,scale):
            rendered.append(self.n)
            if self.n==3 and fail_once[0]:fail_once[0]=False;raise OSError('simulated disconnect')
            return Bitmap()
        def close(self):pass
    class Document:
        def __init__(self,*args):pass
        def __enter__(self):return self
        def __exit__(self,*args):pass
        def __len__(self):return 3
        def __getitem__(self,k):return Page(k+1)
    class Engine:
        def __init__(self,**kwargs):pass
        def __call__(self,image):return ([[[[0,0],[8,0],[8,8],[0,8]],'recognized',.99]],{})
    monkeypatch.setattr(pypdfium2,'PdfDocument',Document)
    monkeypatch.setattr(rapidocr_onnxruntime,'RapidOCR',Engine)
    before=digest(output/'pages/001.json')
    with pytest.raises(OSError,match='disconnect'):run(source,output)
    assert (output/'recovery/002.json').read_text(encoding='utf-8')=='{'
    run(source,output)
    assert rendered==[2,3,3]
    assert digest(output/'pages/001.json')==before
    assert json.loads((output/'textbook-full-ocr.json').read_text(encoding='utf-8'))['coverage']['recognized_pages']==3


def test_merge_rejects_wrong_page_and_missing_page(tmp_path):
    image = tmp_path / 'images/001.png'
    image.parent.mkdir()
    image.write_bytes(b'image')
    write_json(tmp_path / 'pages/001.json', {'pdf_page': 2, 'source_sha256': 'source',
               'image_path': 'images/001.png', 'image_sha256': digest(image), 'corrected_text': 'text'})
    with pytest.raises(ValueError, match='lineage'):
        merge_pages(tmp_path, {'pdf_pages': 1, 'source_sha256': 'source'})
    write_json(tmp_path / 'pages/001.json', {'pdf_page': 1, 'source_sha256': 'source',
               'image_path': 'images/001.png', 'image_sha256': digest(image), 'corrected_text': 'text'})
    with pytest.raises(FileNotFoundError):
        merge_pages(tmp_path, {'pdf_pages': 2, 'source_sha256': 'source'})


def test_120_cases_preserve_partitions_without_gold_leakage(tmp_path):
    import csv
    owner = tmp_path / 'question-owner.csv'
    columns = ['item_id', 'split', 'question_family_id', 'concept_id', 'question_text',
               'concept_name', 'required_conditions', 'standard_evidence_text', 'draft_answerability']
    with owner.open('w', encoding='utf-8', newline='') as file:
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        for split in ['development', 'sealed_test']:
            for index in range(40):
                writer.writerow({'item_id': f'{split}-{index}', 'split': split,
                    'question_family_id': f'{split}-family-{index}', 'concept_id': 'DS-LIN-01',
                    'question_text': 'Synthetic public test question', 'concept_name': 'Linear list',
                    'required_conditions': 'private gold', 'standard_evidence_text': 'private gold',
                    'draft_answerability': 'private gold'})
    data = make_cases(owner)
    assert len(data['cases']) == 120
    assert data['counts'] == {'historical': 40, 'development': 40, 'sealed_test': 40}
    for row in data['cases'][40:]:
        assert row['attempt_history'] == []
        assert row['allowed_variants'] == []
        assert not set(row) & {'required_conditions', 'standard_evidence_text', 'draft_answerability'}


def test_budget_never_refunds_pending_or_exceeds_either_ceiling():
    base = {'new_requests': 0, 'cumulative_requests': 327, 'unresolved_requests': 0}
    check_budget(base, 364)
    with pytest.raises(RuntimeError):
        check_budget(base, 365)
    with pytest.raises(RuntimeError, match='reconciliation'):
        check_budget({**base, 'unresolved_requests': 1})
    with pytest.raises(RuntimeError):
        check_budget({**base, 'cumulative_requests': 691}, 1)


def test_v3_authorized_total_and_phase_ceilings(tmp_path,monkeypatch):
    from scripts.run_m3_fulltext_rag import authorization, V3_BATCH
    auth=authorization(V3_BATCH)
    base={'new_requests':160,'cumulative_requests':487,'unresolved_requests':0,
          'new_ceiling':auth['new_ceiling'],'cumulative_ceiling':auth['cumulative_ceiling'],
          'batch_requests':0,'additional_ceiling':400,'phase_requests':{'annotation':0},
          'phase_ceilings':auth['phase_ceilings']}
    check_budget(base,400)
    with pytest.raises(RuntimeError,match='budget_exhausted'):check_budget(base,401)
    check_budget(base,36,phase='annotation')
    with pytest.raises(RuntimeError,match='phase_request_budget'):check_budget(base,37,phase='annotation')
    with pytest.raises(RuntimeError,match='reconciliation'):check_budget({**base,'unresolved_requests':1})
    from scripts import run_m3_fulltext_rag as runner
    import hashlib
    raw=tmp_path/V3_BATCH;raw.mkdir()
    monkeypatch.setattr(runner.campaign,'_provider_budget_state',lambda *_:{'provider_calls':487,
        'unresolved_provider_request_reservation':0})
    frozen={'authorization':auth}
    frozen['config_sha256']=hashlib.sha256(json.dumps(frozen,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    write_json(raw/'frozen-config.json',frozen)
    saved=runner.budget_state(raw)
    assert saved['cumulative_ceiling']==887 and saved['phase_ceilings']['annotation']==36
    frozen['authorization']['cumulative_ceiling']=888
    write_json(raw/'frozen-config.json',frozen)
    with pytest.raises(RuntimeError,match='frozen_budget_config_hash_mismatch'):runner.budget_state(raw)


def test_isolated_course_index_is_readable_by_fixture_learners(tmp_path):
    from library.service import ResourceLibrary
    from runtime.storage.resource_store import SqliteResourceStore
    ocr=tmp_path/'ocr.json'
    rows=[{'pdf_page':n,'corrected_text':'线性表是数据元素的有限序列。',
           'review_status':'ai_visual_reviewed','index_eligible':True} for n in range(1,348)]
    write_json(ocr,{'source':{'pdf_pages':347,'source_sha256':'fixture','source_filename':'local.pdf'},'pages':rows})
    db=tmp_path/'index/course.sqlite';proof=build_index(ocr,db)
    store=SqliteResourceStore.open(str(db))
    try:
        lib=ResourceLibrary(store,library_root=tmp_path/'library',default_retrieval_strategy='hybrid_rrf')
        result=lib.search('线性表',course_id='ds.c_language.v1',owner_id='fixture-learner')
        assert result['status']=='ok' and result['evidence']
        assert proof['pdf_pages']==347
        assert all(x['retrieval_strategy']=='hybrid_rrf' for x in result['evidence'])
    finally:store.close()


def test_conditional_metrics_use_all_judged_pool_and_do_not_fill_unknowns():
    from scripts.summarize_fulltext_rag import retrieval_metrics,dcg
    rows=[{'case_id':'q','chunk_id':str(i),'relevance_grade':str(g),
           'hash_dense_rank':str(rank),'bm25_rank':str(rank),'rrf_rank':str(rank)}
          for i,(g,rank) in enumerate([(2,1),(1,21),(0,2)])]
    result=retrieval_metrics(rows)
    assert result['methods']['rrf']['recall_at_20']==.5
    assert result['methods']['rrf']['ndcg_at_5']==pytest.approx(dcg([2,0])/dcg([2,1,0]))
    rows[1]['relevance_grade']='无法判定'
    result=retrieval_metrics(rows)
    assert result['fully_judged_queries']==0
    assert result['methods']['rrf']['recall_at_20'] is None


def test_preflight_rejects_truncation(tmp_path):
    write_json(tmp_path / 'cells/c.json', {'status': 'completed', 'application_status': 'completed',
                                          'evaluation_status': 'truncated', 'model_calls': 1})
    assert not successful({'observed_cells': 1}, tmp_path, 1)


def test_private_prompt_archive_keeps_actual_messages_but_not_headers(tmp_path, monkeypatch):
    monkeypatch.setenv('DEEPPROF_RESEARCH_REQUEST_ARCHIVE', str(tmp_path))
    body = {'messages': [{'role': 'user', 'content': 'question'}], 'model': 'test'}
    OpenAICompatibleProvider._archive_research_request(body, {'trace_id': 't1'})
    data = json.loads(next(tmp_path.glob('*.json')).read_text(encoding='utf-8'))
    assert data['body'] == body and data['trace_id'] == 't1'
    assert len(data['messages_sha256']) == 64
    assert 'headers' not in data and 'Authorization' not in data


def test_ai_provenance_is_not_human_agreement(tmp_path):
    text = 'item_id,individual_support_label,individual_reason,reviewer,annotation_source,annotation_version\na,完整支持,explicit source,AI,ai,v1\n'
    for n in (1, 2):
        (tmp_path / f'citation-rater-{n}.csv').write_text(text, encoding='utf-8')
    with pytest.raises(ValueError, match='AI_labels'):
        compare(tmp_path, 'citation', 'individual_support_label', CITATION_LABELS)
    path = tmp_path / 'citation-ai.csv'
    path.write_text(text, encoding='utf-8')
    result = validate_ai_csv(path, 'individual_support_label', CITATION_LABELS)
    assert result['rows'] == 1 and not result['provenance_errors']
    assert result['cohen_kappa'] is None


@pytest.mark.asyncio
async def test_research_wire_guard_forces_disabled_and_blocks_omission_before_http(monkeypatch):
    import httpx
    from runtime.providers.profiles import ProviderProfile
    from runtime.providers.secrets import InMemorySecretStore
    from runtime.core.errors import ProviderError
    bodies=[]
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,json={'choices':[{'message':{'content':'ok'},'finish_reason':'stop'}]})
    profile=ProviderProfile(profile_id='guard',display_name='guard',base_url='https://example.invalid/v1',
        default_model='model-a',api_key_ref='fixture-key',model_capabilities={'model-a':{'reasoning_mode':'toggle','thinking_parameter':'thinking.type'}})
    provider=OpenAICompatibleProvider(profile,InMemorySecretStore({'fixture-key':'test-only'}),transport=httpx.MockTransport(handler))
    await provider.generate({'messages':[]},{'thinking_enabled':False,'thinking_mode':'default',
                                              'require_explicit_thinking_mode':True})
    assert bodies[0]['thinking']=={'type':'disabled'}
    monkeypatch.setenv('DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED','true')
    with pytest.raises(ProviderError,match='configuration_mismatch'):
        await provider.generate({'messages':[],'thinking_mode':'default','thinking_enabled':False},{})
    assert len(bodies)==1


def test_preflight_wire_audit_rejects_missing_disabled_even_when_application_passed(tmp_path):
    import hashlib
    from scripts.run_m3_fulltext_rag import validate_wire_archive
    body={'model':'deepseek-flash','messages':[],'max_tokens':8192,'temperature':.3}
    encoded=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
    write_json(tmp_path/'request.json',{'body':body,'body_sha256':hashlib.sha256(encoded).hexdigest(),
                                         'messages_sha256':hashlib.sha256(b'[]').hexdigest()})
    with pytest.raises(RuntimeError,match='actual_wire_configuration_drift'):validate_wire_archive(tmp_path,1)


def test_configuration_invalid_batch_has_no_formal_scores():
    from scripts.run_m3_fulltext_rag import RESEARCH
    path=RESEARCH/'m3-fulltext-rag-20261004-v2/evaluation-summary.json'
    if not path.exists():pytest.skip('local evidence fixture unavailable')
    data=json.loads(path.read_text(encoding='utf-8'))
    assert data['configuration_eligible'] is False
    assert data['formal_eligible_cells']==0
    for groups in data['conditions_by_split'].values():
        for row in groups.values():
            assert row['valid']==0 and row['false_accept_generation']['rate'] is None


@pytest.mark.asyncio
async def test_session_graph_runtime_provider_chain_keeps_frozen_controls(monkeypatch):
    import httpx
    from api.sessions import _teaching_generation_context
    from graph.education.state import new_state
    from graph.education.nodes import runtime_ctx
    from runtime.providers.profiles import ProviderProfile
    from runtime.providers.secrets import InMemorySecretStore
    from runtime.testing import make_service
    bodies=[]
    def handler(request):
        bodies.append(json.loads(request.content))
        return httpx.Response(200,text='data: '+json.dumps({'choices':[{'delta':{'content':'complete'},'finish_reason':'stop'}]})+'\n\ndata: [DONE]\n\n')
    caps={'reasoning_mode':'toggle','thinking_parameter':'thinking.type'}
    profile=ProviderProfile(profile_id='frozen',display_name='frozen',base_url='https://example.invalid/v1',
        default_model='deepseek-flash',api_key_ref='fixture-key',capabilities={'stream':True},model_capabilities={'deepseek-flash':caps})
    provider=OpenAICompatibleProvider(profile,InMemorySecretStore({'fixture-key':'mock-only'}),transport=httpx.MockTransport(handler))
    service=make_service()
    service.router.add(profile,provider)
    incoming={'thinking_enabled':False,'thinking_mode':'off','require_explicit_thinking_mode':True,
              'experiment_run':True,'allow_provider_fallback':False,
              'generation_config':{'temperature':.3,'max_output_tokens':8192}}
    ctx=runtime_ctx(new_state(session_id='fixture',provider_profile='frozen',model='deepseek-flash',
                              **_teaching_generation_context(incoming)))
    monkeypatch.setenv('DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED','true')
    frames=[f async for f in service.generate({'messages':[]},ctx)]
    assert any(f.get('text')=='complete' for f in frames), frames
    assert len(bodies)==1 and bodies[0]['thinking']=={'type':'disabled'}
    assert bodies[0]['max_tokens']==8192 and bodies[0]['temperature']==.3
    assert ctx['experiment_run'] and not ctx['allow_provider_fallback']
    await service.aclose()


@pytest.mark.asyncio
async def test_pre_http_guard_failure_is_terminal_and_does_not_send(monkeypatch,tmp_path):
    import httpx
    from runtime.providers.profiles import ProviderProfile
    from runtime.providers.secrets import InMemorySecretStore
    from runtime.testing import make_service
    sent=[]
    provider=OpenAICompatibleProvider(ProviderProfile(profile_id='guard',display_name='guard',
        base_url='https://example.invalid',default_model='deepseek-flash',capabilities={'stream':True}),
        InMemorySecretStore(),transport=httpx.MockTransport(lambda request:sent.append(request)))
    service=make_service(); service.router.add(provider.profile,provider)
    monkeypatch.setenv('DEEPPROF_RESEARCH_REQUIRE_THINKING_DISABLED','true')
    monkeypatch.setenv('DEEPPROF_RESEARCH_REQUEST_ARCHIVE',str(tmp_path))
    frames=[f async for f in service.generate({'messages':[]},{'provider_profile':'guard',
        'session_id':'failure-fixture','experiment_run':True})]
    assert not sent and frames[-1]['type']=='error'
    assert frames[-1]['error']['details']['kind']=='capability_missing'
    events=service.history('failure-fixture')
    assert [e['type'] for e in events]==['model.requested','model.failed']
    assert not list(tmp_path.glob('*.json'))
    rejected=list((tmp_path/'rejected-before-http').glob('*.json'))
    assert len(rejected)==1 and json.loads(rejected[0].read_text())['request_state']=='rejected_before_http'
    await service.aclose()


def test_cleanup_exception_still_persists_stop_and_ledger(tmp_path,monkeypatch):
    import io
    import subprocess
    from scripts import run_m3_fulltext_rag as runner
    def fail(_process):raise subprocess.TimeoutExpired('taskkill',3)
    monkeypatch.setattr(runner,'stop_gateway_tree',fail)
    monkeypatch.setattr(runner,'budget_state',lambda _root:{'cumulative_requests':488})
    monkeypatch.setenv('DEEPPROF_API_PORT','49362')
    state={'status':'stopped','reason':'guard failure'};log=io.StringIO()
    runner.finalize_batch(None,log,{'DEEPPROF_API_PORT':None},state,tmp_path,tmp_path)
    saved=json.loads((tmp_path/'batch-state.json').read_text(encoding='utf-8'))
    assert saved['status']=='stopped' and saved['reason']=='guard failure'
    assert saved['cleanup']=='unconfirmed_cleanup_exception' and log.closed
    assert json.loads((tmp_path/'provider-request-budget.json').read_text())['cumulative_requests']==488


@pytest.mark.asyncio
@pytest.mark.parametrize('retrieval,constraint',[(True,True),(True,False),(False,True),(False,False)])
async def test_real_teaching_graph_isolates_all_four_conditions(retrieval,constraint):
    from graph.education.builder import run_teaching_turn
    from graph.education.bindings import ACTION_BINDINGS
    from runtime.testing import make_service
    from skills.rag import RAGSkill
    from tools.retrieval.search_textbook import build_search_textbook_tool
    source='独立测试教材片段：线性表是有限序列。'
    searches=[];requests=[]
    def search(query,**kwargs):
        searches.append(query)
        return {'evidence':[{'document_id':'fixture-doc','chunk_id':'fixture-chunk','page':21,
                             'text':source,'source':'local://fixture','reliable':True}]}
    def generate(request):requests.append(request);return {'content':'本地测试回答','finish_reason':'stop'}
    service=make_service(bindings=ACTION_BINDINGS,script=[generate])
    service.skills.register(RAGSkill());service.tools.register(build_search_textbook_tool(search))
    result=await run_teaching_turn(service,{'session_id':'four-condition-fixture','course_id':'ds.c_language.v1',
        'provider_profile':'fake','model':'fake-model',
        'current_concept':'线性表','learning_goal':'理解线性表','user_input':'请讲讲线性表',
        'experiment_run':True,'evidence_constraint':constraint,
        'm3_evidence_options':{'retrieval_enabled':retrieval,'evidence_constraint':constraint}})
    assert bool(searches)==retrieval
    assert len(requests)==int(retrieval or not constraint)
    if requests:
        prompt=repr(requests[0]['messages'])
        assert (source in prompt)==retrieval
        assert ('唯一允许的依据' in prompt)==constraint
    if not retrieval and constraint:assert result['action']=='reflect' and not result['citations']
    await service.aclose()
