"""Private, source-grounded AI review with the same cumulative request ledger.

Raw evaluation instructions and responses live under reproduction, never in reports.
No reviewer identity or human agreement is inferred from these judgments.
"""
from __future__ import annotations
import argparse
import ast
import csv
import hashlib
import json
import math
import re
import sqlite3
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
from scripts.run_m3_fulltext_rag import (RAW_RESEARCH, RESEARCH, ANNOTATIONS, SOURCE_HOME,
    read_csv, budget_state, check_budget, runtime_fingerprint, ACTIVE_BATCH)
from scripts.ocr_full_textbook import write_json, digest
from scripts import run_m3_live_pilot as live
from library.embeddings import HashingEmbedder, retrieval_index_profile
from library.chunking import chunking_version
from library.concept_queries import expand_concept_query
from library.hybrid_retrieval import reciprocal_rank_fusion
from runtime.storage.resource_store import SqliteResourceStore
from runtime.core.events import utc_now
from evaluation.rag_citation_audit import audit_run

VERSION = 'fulltext-ai-review-20261004-v4'
LABELS = ('完整支持', '部分支持', '无支持', '矛盾', '无法判定')
REASONS = {
    'D': '片段直接覆盖问题所需定义或判断依据',
    'A': '片段给出所需算法步骤或条件',
    'P': '片段仅覆盖必要条件的一部分',
    'B': '片段是同概念背景，不能单独解决具体问题',
    'T': '仅词语或主题相近，未给出所需证据',
    'U': '内容与问题无关',
    'M': '所问接口、参数或证明在该片段中缺失',
    'O': 'OCR损坏妨碍判断',
}
CITE_REASONS = {'F':'原文直接陈述或完整蕴含主张','P':'仅覆盖主张的一部分',
    'C':'主张的限定条件或具体结论超出引文','G':'引文只提供相关背景',
    'X':'主张与引文冲突','O':'来源或OCR不足以判定','N':'引文不支持该主张'}


def frozen_ocr(batch: Path) -> Path:
    frozen = json.loads((batch / 'frozen-config.json').read_text(encoding='utf-8'))
    path = Path(frozen.get('ocr_path', SOURCE_HOME / 'course/fulltext-ocr/bce3d6d54eaf/textbook-full-ocr.json'))
    if digest(path) != frozen['ocr_sha256']:
        raise RuntimeError('frozen_annotation_ocr_changed')
    return path


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', encoding='utf-8-sig', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fields)
        writer.writeheader()
        writer.writerows(rows)


def connect(path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(path.resolve().as_uri() + '?mode=ro', uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def prepare(batch: Path) -> dict[str, Any]:
    output = batch / 'ai-annotation'
    output.mkdir(exist_ok=True)
    old_db = RAW_RESEARCH / 'm3-abc-bkt-20260928T134542Z-a216df/runtime/sessions.sqlite'
    old = read_csv(ANNOTATIONS / 'retrieval-rater-1.csv')
    keys = {r['item_id']: r for r in read_csv(ANNOTATIONS / 'retrieval-blinding-key.csv')}
    with connect(old_db) as c:
        texts = {r['chunk_id']: r['text'] for r in c.execute('SELECT chunk_id,text FROM library_chunks')}
    historical = []
    for row in old:
        key = keys[row['item_id']]
        historical.append({**row, **key, 'query': row['query_text'],
                           'split': 'historical', 'pool_version': 'historical',
                           'source_excerpt': texts.get(row['chunk_id'], row['candidate_excerpt']),
                           'page': row['pdf_page']})
    write_csv(output / 'retrieval-historical-input.csv', historical)
    cases = json.loads((batch / 'cases.json').read_text(encoding='utf-8'))['cases']
    store = SqliteResourceStore(connect(batch / 'index/fulltext.sqlite'))
    embedder = HashingEmbedder()
    profile = retrieval_index_profile(embedder, chunking_version=chunking_version(800, 120))
    rows = []
    try:
        for case in cases:
            query = live._retrieval_queries(case)['C']
            expanded, _ = expand_concept_query(query, 'ds.c_language.v1', [])
            vector = embedder.embed([expanded])[0]
            dense = store.search(vector, score=embedder.similarity, top_k=50, min_score=.10,
                retrieval_profile=profile, course_id='ds.c_language.v1', owner_id='local')
            lexical = store.search_bm25(expanded, top_k=50, course_id='ds.c_language.v1', owner_id='local')
            fused = reciprocal_rank_fusion({'dense': dense, 'bm25': lexical}, rank_constant=60, candidate_limit=100)
            dr = {x['chunk_id']: i for i, x in enumerate(dense, 1)}
            br = {x['chunk_id']: i for i, x in enumerate(lexical, 1)}
            for rank, hit in enumerate(fused, 1):
                item = 'RF-' + hashlib.sha256((case['case_id']+'|'+hit['chunk_id']).encode()).hexdigest()[:16]
                rows.append({'item_id': item, 'case_id': case['case_id'], 'split': case['split'],
                    'pool_version': 'fulltext', 'query': query, 'concept_id': case['concept_id'],
                    'document_id': hit['document_id'], 'chunk_id': hit['chunk_id'], 'page': hit['page'],
                    'printed_page': hit.get('printed_page'), 'chapter': hit.get('chapter', ''),
                    'hash_dense_rank': dr.get(hit['chunk_id'], ''), 'bm25_rank': br.get(hit['chunk_id'], ''),
                    'rrf_rank': rank, 'source_excerpt': hit['text']})
    finally:
        store.close()
    write_csv(output / 'retrieval-fulltext-input.csv', rows)
    proof = {'historical_candidates': len(historical), 'fulltext_candidates': len(rows),
             'questions': len(cases), 'candidate_k': 50, 'rrf_constant': 60, 'dense_min_score': .10,
             'annotation_source': 'ai', 'status': 'prepared; not yet judged',
             'reference_answers_excluded_from_generation': True}
    write_json(output / 'input-manifest.json', proof)
    return proof


def model_request(batch: Path, name: str, messages: list[dict[str, str]]) -> tuple[str, dict[str, Any]]:
    """One request, no retry; an uncertain transport outcome remains reserved."""
    state_path = RESEARCH / batch.name / 'batch-state.json'
    if state_path.exists() and json.loads(state_path.read_text(encoding='utf-8')).get('configuration_eligible') is False:
        raise RuntimeError('configuration_drift_stop; no additional model requests authorized in this batch')
    import httpx
    from runtime.providers.windows_secrets import WindowsDpapiSecretStore
    frozen = json.loads((batch / 'frozen-config.json').read_text(encoding='utf-8'))
    body = {'model': frozen['model'], 'messages': messages, 'temperature': 0,
            'max_tokens': 8192, 'thinking': {'type': 'disabled'}, 'stream': False}
    encoded = json.dumps(body, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()
    h = hashlib.sha256(encoded).hexdigest()
    run_id = batch.name + '-annotation-' + name
    run = batch / 'runs' / run_id
    cell_path = run / 'cells/request.json'
    request_path = batch / 'reproduction/annotation-requests' / (name + '.json')
    response_path = batch / 'reproduction/annotation-responses' / (name + '.json')
    if cell_path.exists():
        cell = json.loads(cell_path.read_text(encoding='utf-8'))
        if cell['body_sha256'] != h:
            raise RuntimeError('annotation_resume_input_or_protocol_changed')
        if cell['evaluation_status'] != 'completed':
            raise RuntimeError('failed_annotation_request_retained; no_automatic_retry')
        saved = json.loads(response_path.read_text(encoding='utf-8'))
        return saved['choices'][0]['message']['content'], cell
    if (run / 'pending/request.json').exists():
        raise RuntimeError('annotation_request_unresolved; reconcile_before_resume')
    if runtime_fingerprint() != frozen['runtime_files_sha256']:
        raise RuntimeError('frozen_annotation_or_runtime_sources_changed')
    frozen_ocr(batch)
    from scripts.run_m3_rag_repair import _active_deepseek_profile
    if _active_deepseek_profile()['profile_sha256'] != frozen['provider_profile_sha256']:
        raise RuntimeError('frozen_provider_configuration_changed')
    check_budget(budget_state(batch), 1, phase='annotation')
    profiles = json.loads((SOURCE_HOME / 'providers.json').read_text(encoding='utf-8'))['profiles']
    profile = next(p for p in profiles if p['profile_id'] == frozen['provider_profile'])
    if profile['base_url'] != 'https://api.deepseek.com':
        raise RuntimeError('frozen_provider_endpoint_changed')
    key = WindowsDpapiSecretStore(SOURCE_HOME / 'credentials/dpapi.json').get(profile['api_key_ref'])
    if not key:
        raise RuntimeError('annotation_provider_credential_missing')
    write_json(request_path, {'body': body, 'body_sha256': h,
        'messages_sha256': hashlib.sha256(json.dumps(messages,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest(),
        'purpose': 'ai_evaluation_only', 'annotation_version': VERSION, 'private_local_only': True})
    write_json(run / 'manifest.json', {'run_id': run_id, 'phase': 'annotation', 'created_at': utc_now(),
                                      'body_sha256': h, 'planned_cells': 1, 'status': 'running'})
    write_json(run / 'pending/request.json', {'request_budget_reservation': 1, 'state': 'submitted',
                                             'body_sha256': h, 'created_at': utc_now()})
    # Never include credentials, authorization headers, or transport dumps in artifacts.
    try:
        with httpx.Client(timeout=httpx.Timeout(360, connect=20)) as client:
            result = client.post(profile['base_url'] + '/chat/completions', json=body,
                                 headers={'Authorization': 'Bearer '+key})
    except httpx.TransportError as exc:
        raise RuntimeError('annotation_transport_outcome_unresolved:' + type(exc).__name__) from None
    status = 'provider_failed'
    try:
        payload = result.json() if result.status_code == 200 else {'http_status': result.status_code}
    except ValueError:
        payload = {'http_status': result.status_code, 'invalid_json_response': True}
    write_json(response_path, payload)
    choices = payload.get('choices') or []
    if choices and choices[0].get('finish_reason') == 'stop':
        status = 'completed'
    cell = {'cell_id': 'request', 'model_calls': 1, 'evaluation_status': status,
            'body_sha256': h, 'http_status': result.status_code, 'usage': payload.get('usage') or {},
            'annotation_source': 'ai', 'annotation_model': frozen['model'], 'finished_at': utc_now()}
    write_json(cell_path, cell)
    write_json(run / 'summary.json', {'run_id': run_id, 'status': status,
               'sample': {'phase': 'annotation'}, 'token_usage': {'model_calls': 1}})
    write_json(run / 'pending/request.json', {'request_budget_reservation': 0, 'state': 'terminal', 'finished_at': utc_now()})
    write_json(RESEARCH / batch.name / 'provider-request-budget.json', budget_state(batch))
    if status != 'completed':
        raise RuntimeError('annotation_provider_failure_or_truncation; no_automatic_retry')
    return choices[0]['message']['content'], cell


def parse_json(text: str) -> Any:
    stripped = re.sub(r'^```(?:json)?\s*|\s*```$', '', text.strip())
    return json.loads(stripped)


def annotate_retrieval(batch: Path, size: int = 640) -> dict[str, Any]:
    folder = batch / 'ai-annotation'
    rows = read_csv(folder / 'retrieval-historical-input.csv') + read_csv(folder / 'retrieval-fulltext-input.csv')
    # Identical query/content pairs are reviewed once, with all method ranks retained.
    groups: dict[str, list[dict[str, str]]] = {}
    for row in rows:
        key = hashlib.sha256((row['query']+'\0'+row['source_excerpt']).encode()).hexdigest()
        groups.setdefault(key, []).append(row)
    unique = list(groups.items())
    frozen=json.loads((batch/'frozen-config.json').read_text(encoding='utf-8'))
    if size != frozen['annotation_configuration']['retrieval_batch_size']:
        raise ValueError('frozen_retrieval_annotation_batch_size_changed')
    judged = []
    for start in range(0, len(unique), size):
        block = unique[start:start+size]
        texts, queries = {}, {}
        pairs = []
        line_lists = []
        for i, (_, mapped) in enumerate(block):
            row = mapped[0]
            lines = [s for s in row['source_excerpt'].splitlines() if s.strip()]
            line_lists.append(lines)
            q = queries.setdefault(row['query'], len(queries))
            t = texts.setdefault(row['source_excerpt'], len(texts))
            pairs.append([i, q, t, row['page']])
        instruction = ('逐条判断数据结构问题与候选片段的相关性。0=不相关或仅主题相似；1=部分支持；'
          '2=直接覆盖问题的关键证据需求；?=OCR妨碍判断。不要以术语重合代替判断，不用外部知识补足缺失条件。'
          '每行严格输出 i,grade,reason_code,line_number（i是输入pairs的编号；line_number从1起，指出最有判别力的原文行，'
          '内容无关可用0）。允许代码：'+json.dumps(REASONS,ensure_ascii=False)+
          '。不输出标题、解释或Markdown。必须完整覆盖所有pairs且编号不重复。')
        data = {'queries': {str(v):k for k,v in queries.items()},
                'texts': {str(v):'\n'.join(f'{i}: {s}' for i,s in enumerate(k.splitlines(),1)) for k,v in texts.items()},
                'pairs': pairs}
        answer, request = model_request(batch, f'retrieval-{start:05d}',
             [{'role':'system','content':instruction},{'role':'user','content':json.dumps(data,ensure_ascii=False)}])
        verdicts = {}
        for line in answer.splitlines():
            if line.strip().startswith('```'):continue
            m = re.fullmatch(r'\s*(\d+)\s*,\s*([012?])\s*,\s*([DAPBTUMO])\s*,\s*(\d+)\s*', line)
            if not m:
                if not line.strip(): continue
                raise ValueError('invalid_retrieval_annotation_line')
            i, grade, code, line_no = m.groups(); i, line_no = int(i), int(line_no)
            if i in verdicts or not 0 <= i < len(block): raise ValueError('duplicate_or_unknown_annotation_id')
            original_lines = block[i][1][0]['source_excerpt'].splitlines()
            if line_no > len(original_lines): raise ValueError('annotation_quote_line_out_of_range')
            if grade == '2' and (code not in {'D','A'} or not line_no): raise ValueError('grade_two_needs_direct_evidence')
            verdicts[i] = (grade,code,line_no,original_lines[line_no-1] if line_no else '')
        if set(verdicts) != set(range(len(block))): raise ValueError('incomplete_retrieval_annotations')
        for i, (key, mapped) in enumerate(block):
            grade,code,line_no,quote = verdicts[i]
            for row in mapped:
                judged.append({**row, 'relevance_grade_0_1_2': '无法判定' if grade=='?' else grade,
                   'relevance_grade': '无法判定' if grade=='?' else grade, 'relevance_status': 'reviewed',
                   'rationale': REASONS[code]+'；问题：'+row['query']+'；判别原文：'+(quote or '该片段没有对应依据'),
                   'evidence_locator': f"{row['document_id']} / {row['chunk_id']} / PDF {row['page']} / line {line_no}",
                   'annotation_source':'ai', 'annotation_version':VERSION, 'annotation_model':'deepseek-flash',
                   'annotation_request_sha256':request['body_sha256'], 'deduplicated_judgment_id':key,
                   'annotation_request_kind':'provider_http_body', 'annotation_ocr_sha256':digest(frozen_ocr(batch)),
                   'human_review_status':'not_reviewed'})
        write_csv(folder / 'retrieval-ai.partial.csv', judged)
        print(f'Retrieval reviewed {min(start+size,len(unique))}/{len(unique)} unique pairs', flush=True)
    write_csv(folder / 'retrieval-ai.csv', judged)
    return {'rows':len(judged),'unique_judgments':len(unique),'labels':dict(Counter(r['relevance_grade'] for r in judged))}


def annotate_questions(batch: Path) -> dict[str, Any]:
    folder=batch/'ai-annotation'
    frozen=json.loads((batch/'frozen-config.json').read_text(encoding='utf-8'))
    # The frozen OCR file is located via the source index lineage, not the drafts' gold labels.
    ocr=frozen_ocr(batch)
    if digest(ocr)!=frozen['ocr_sha256']: raise RuntimeError('frozen_ocr_changed')
    pages=json.loads(ocr.read_text(encoding='utf-8'))['pages']
    book='\n\n'.join(f"[PDF {p['pdf_page']}]\n"+'\n'.join(f'{i}: {s}' for i,s in
         enumerate(p['corrected_text'].splitlines(),1)) for p in pages)
    rows=read_csv(ANNOTATIONS/'new-80-question-owner.csv')
    cases=json.loads((batch/'cases.json').read_text(encoding='utf-8'))['cases']
    rows += [{'item_id':c['case_id'],'split':'historical','question_text':'\n'.join(c['user_turns']),
              'draft_answerability':''} for c in cases if c['split']=='historical']
    blind={r['question_text']:r['item_id'] for r in read_csv(ANNOTATIONS/'new-80-rater-1.csv')}
    judged=[]
    for start in range(0,len(rows),20):
        selected=rows[start:start+20]
        ask=[{'i':i,'question':r['question_text']} for i,r in enumerate(selected)]
        instruction=('只依据完整教材逐题复核可答性，可纠正先前草案。教材没有所问参数、接口或严格证明时用insufficient_evidence；'
          'answerable需教材证据覆盖全部必要条件；OCR妨碍则无法判定。只输出JSON数组：'
          '[{"i":0,"answerability":"answerable|insufficient_evidence|无法判定","required_conditions":"具体必要条件",'
          '"reason":"具体理由","evidence":[{"pdf_page":1,"line_start":1,"line_end":2}]}]。'
          '证据只报所给原文的PDF页和行号，需包含关键条件，每段最多8行；不要臆造行号。'
          '无法找到证据时evidence为空并解释检索范围。问题若要求检查学习起点，以教材可支持基础提问为准。')
        answer,request=model_request(batch,f'questions-{start:02d}',[{'role':'system','content':instruction},
          {'role':'user','content':json.dumps({'questions':ask,'textbook':book},ensure_ascii=False)}])
        verdicts=parse_json(answer)
        if sorted(x['i'] for x in verdicts)!=list(range(len(selected))): raise ValueError('incomplete_question_annotations')
        for v in verdicts:
            r=selected[v['i']]
            if v['answerability'] not in {'answerable','insufficient_evidence','无法判定'}:raise ValueError('invalid_answerability')
            verified=[]; failed_quotes=[]
            for e in v['evidence']:
                n=int(e['pdf_page']);a=int(e['line_start']);b=int(e['line_end'])
                lines=pages[n-1]['corrected_text'].splitlines() if 1<=n<=347 else []
                if not 1<=a<=b<=len(lines) or b-a>=8:
                    failed_quotes.append(e);continue
                verified.append({**e,'quote':'\n'.join(lines[a-1:b])})
            if v['answerability']=='answerable' and not verified:
                v['answerability']='无法判定';v['reason']+='；模型提供的引文未通过逐字来源校验，不能据此确认可答性。'
            judged.append({'item_id':blind.get(r['question_text'],r['item_id']),'case_id':r['item_id'],'split':r['split'],
              'question_text':r['question_text'],'answerability':v['answerability'],
              'required_conditions':v['required_conditions'],'rationale':v['reason'],
              'evidence_locator':json.dumps(verified,ensure_ascii=False),'unverified_model_quotes':json.dumps(failed_quotes,ensure_ascii=False),
              'draft_answerability':r['draft_answerability'],
              'annotation_source':'ai','annotation_version':VERSION,'annotation_model':'deepseek-flash',
              'annotation_request_sha256':request['body_sha256'],'human_review_status':'not_reviewed',
              'annotation_request_kind':'provider_http_body','annotation_ocr_sha256':frozen['ocr_sha256']})
        write_csv(folder/'new-80-ai.partial.csv',judged)
        print(f'Questions reviewed {len(judged)}/{len(rows)}',flush=True)
    write_csv(folder/'questions-120-ai.csv',judged)
    write_csv(folder/'new-80-ai.csv',[r for r in judged if r['split']!='historical'])
    return {'rows':len(judged),'labels':dict(Counter(r['answerability'] for r in judged))}


def prepare_citations(batch: Path) -> list[dict[str, Any]]:
    folder=batch/'ai-annotation'
    old=read_csv(ANNOTATIONS/'citation-rater-1.csv')
    key={r['item_id']:r for r in read_csv(ANNOTATIONS/'citation-blinding-key.csv')}
    with connect(RAW_RESEARCH/'m3-abc-bkt-20260928T134542Z-a216df/runtime/sessions.sqlite') as c:
        old_sources={r['chunk_id']:dict(r) for r in c.execute('SELECT chunk_id,document_id,page,text,reliable FROM library_chunks')}
    with connect(batch/'index/fulltext.sqlite') as c:
        new_sources={r['chunk_id']:dict(r) for r in c.execute('SELECT chunk_id,document_id,page,text,reliable FROM library_chunks')}
    rows=[]
    for r in old:
        k=key[r['item_id']]
        try:locator=json.loads(k['locator']) if k['locator'].strip() else {}
        except json.JSONDecodeError:locator=ast.literal_eval(k['locator'])
        if not isinstance(locator,dict):raise ValueError('citation_locator_must_be_object')
        source=old_sources.get(locator.get('chunk_id'),{})
        exact=bool(source and source['document_id']==locator.get('document_id') and source['page']==locator.get('page') and source['reliable'])
        rows.append({**r,**k,'pool_version':'historical','page':r['pdf_page'],
          'source_text':source.get('text',r['source_excerpt']),
          'locator_status':'exact_document_chunk_page_match' if exact else 'no_citation_or_unresolved_locator',
          'source_location_verified':exact,
          'evidence_locator':k['locator']})
    for run in sorted((batch/'runs').glob('*')):
        if not (run/'manifest.json').exists():continue
        manifest=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('phase') in {'preflight','annotation'}:continue
        name=run.name.removeprefix(batch.name+'-')
        detail=folder/'citation-audits'/name/'detail.jsonl'
        audit_run(run,batch/'index/fulltext.sqlite',detail,detail.parent/'audit-summary.json',detail.parent/'review.csv')
        for raw in map(json.loads,detail.read_text(encoding='utf-8').splitlines()):
            if raw['record_type']=='cell_trace':continue
            ident='CF-'+hashlib.sha256((name+'|'+raw['cell_id']+'|'+str(raw.get('claim_id'))+'|'+str(raw.get('citation_selector'))).encode()).hexdigest()[:16]
            locator=raw.get('locator') or {}
            source=new_sources.get(locator.get('chunk_id'),{})
            rows.append({**raw,'item_id':ident,'pool_version':'fulltext','condition':name,
              'source_text':source.get('text',raw['source_excerpt']),'page':raw.get('source_page',''),
              'source_location_verified':raw.get('locator_status')=='exact_document_chunk_page_match',
              'evidence_locator':json.dumps(locator,ensure_ascii=False)})
    # Historical source excerpts are retained verbatim; new OCR provides a separate page witness.
    pages=json.loads(frozen_ocr(batch).read_text(encoding='utf-8'))['pages']
    for r in rows:
        try:n=int(r.get('page') or 0)
        except ValueError:n=0
        r['fulltext_page_witness']=pages[n-1]['corrected_text'] if 1<=n<=347 else ''
    write_csv(folder/'citation-input.csv',rows)
    return rows


def annotate_citations(batch: Path, size: int=384) -> dict[str, Any]:
    folder=batch/'ai-annotation';rows=prepare_citations(batch)
    groups=defaultdict(list)
    for r in rows:
        groups[(r['pool_version'],r.get('condition',''),r.get('cell_id',''),r.get('claim_id',''))].append(r)
    judged=[];pairs=[]
    for r in rows:
        if r['record_type']=='claim_citation_pair' and r['source_text'].strip():pairs.append(r);continue
        no_cite=r['record_type']=='claim_without_explicit_citation'
        label='无支持' if no_cite else '无法判定'
        reason='回答事实主张没有可映射的显式引用；此标签评价引用支持，不判定事实真假。' if no_cite else '没有可核验的主张—来源配对，不能给出语义支持判断。'
        judged.append({**r,'individual_support_label':label,'individual_reason':reason,
          'joint_support_label':label,'joint_reason':reason,'annotation_source':'ai',
          'annotation_method':'deterministic citation structure reviewed by Codex',
          'annotation_version':VERSION,'annotation_model':'Codex','human_review_status':'not_reviewed',
          'annotation_request_kind':'local_codex_review_receipt', 'annotation_ocr_sha256':digest(frozen_ocr(batch)),
          'annotation_request_sha256':hashlib.sha256(json.dumps({'record': r, 'reason': reason, 'version': VERSION},ensure_ascii=False,sort_keys=True).encode()).hexdigest(),
          'source_location_status':'no_answer_citation' if no_cite else r.get('locator_status','unresolved')})
    for start in range(0,len(pairs),size):
        block=pairs[start:start+size];data=[]
        for i,r in enumerate(block):
            union=[x['source_text'] for x in groups[(r['pool_version'],r.get('condition',''),r.get('cell_id',''),r.get('claim_id',''))] if x['source_text']]
            data.append({'i':i,'claim':r['claim_text'],'source':'\n'.join(f'{n}: {s}' for n,s in enumerate(r['source_text'].splitlines(),1)),
                         'joint_cited_sources':union,'pdf_page':r.get('page'),
                         'fulltext_page_witness':r['fulltext_page_witness']})
        instruction=('逐条核验回答主张与其实际引用。分别判断单条source支持和joint_cited_sources联合支持。'
          '全文page_witness只供定位与OCR核对，不得将回答没有引用的内容计入支持。'
          '分类0完整支持/1部分支持/2无支持/3矛盾/4无法判定。完整支持需覆盖限定条件；相关术语不能当支持。'
          '每行严格输出 i,single_label,joint_label,reason_code,line_no；line_no指单条source最有判别力的一行，从1起，无相关原文用0。'
          '原因代码'+json.dumps(CITE_REASONS,ensure_ascii=False)+'。必须完整覆盖所有i。不要Markdown或额外说明。')
        answer,request=model_request(batch,f'citations-{start:05d}',[{'role':'system','content':instruction},
          {'role':'user','content':json.dumps(data,ensure_ascii=False)}])
        verdicts={}
        for line in answer.splitlines():
            if line.strip().startswith('```'):continue
            if not line.strip():continue
            m=re.fullmatch(r'\s*(\d+)\s*,\s*([0-4])\s*,\s*([0-4])\s*,\s*([FPCGXON])\s*,\s*(\d+)\s*',line)
            if not m:raise ValueError('invalid_citation_annotation_line')
            i,s,j,code,n=m.groups();i,n=int(i),int(n)
            if i in verdicts or not 0<=i<len(block):raise ValueError('citation_annotation_duplicate_or_unknown')
            lines=block[i]['source_text'].splitlines()
            if n>len(lines):raise ValueError('citation_annotation_quote_line_out_of_range')
            if s=='0' and (code!='F' or n==0):raise ValueError('fully_supported_citation_needs_specific_source')
            verdicts[i]=(LABELS[int(s)],LABELS[int(j)],code,n,lines[n-1] if n else '')
        if set(verdicts)!=set(range(len(block))):raise ValueError('incomplete_citation_annotations')
        for i,r in enumerate(block):
            s,j,code,n,quote=verdicts[i]
            reason=CITE_REASONS[code]+'；主张：'+r['claim_text']+'；单条判别原文：'+(quote or '无对应依据')
            judged.append({**r,'individual_support_label':s,'individual_reason':reason,
              'joint_support_label':j,'joint_reason':'回答实际引用的联合支持分类：'+j+'；'+reason,
              'annotation_source':'ai','annotation_method':'source-grounded semantic review',
              'annotation_version':VERSION,'annotation_model':'deepseek-flash',
              'annotation_request_sha256':request['body_sha256'],'human_review_status':'not_reviewed',
              'annotation_request_kind':'provider_http_body', 'annotation_ocr_sha256':digest(frozen_ocr(batch)),
              'source_location_status':r.get('locator_status') or 'original indexed locator; PDF page witness available'})
        write_csv(folder/'citation-ai.partial.csv',judged)
        print(f'Citations reviewed {min(start+size,len(pairs))}/{len(pairs)} pairs',flush=True)
    # Inconsistent joint classifications cannot count as full claim support.
    joint=defaultdict(set)
    for r in judged:joint[(r['pool_version'],r.get('condition',''),r.get('cell_id',''),r.get('claim_id',''))].add(r['joint_support_label'])
    for r in judged:
        if len(joint[(r['pool_version'],r.get('condition',''),r.get('cell_id',''),r.get('claim_id',''))])>1:
            r['joint_support_label']='无法判定';r['joint_reason']='同一主张的联合判断不一致；保留为无法判定，未补造支持。'
    write_csv(folder/'citation-ai.csv',judged)
    return {'rows':len(judged),'semantic_pairs':len(pairs),'labels':dict(Counter(r['individual_support_label'] for r in judged))}


def main() -> None:
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument('--batch-id',default=ACTIVE_BATCH)
    p.add_argument('--stage',choices=['prepare','retrieval','questions','citations'],required=True)
    p.add_argument('--batch-size',type=int,default=640)
    a=p.parse_args();batch=RAW_RESEARCH/a.batch_id
    result=prepare(batch) if a.stage=='prepare' else annotate_retrieval(batch,a.batch_size) if a.stage=='retrieval' else annotate_citations(batch) if a.stage=='citations' else annotate_questions(batch)
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__': main()
