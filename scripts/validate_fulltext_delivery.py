"""Validate the active batch's evidence and explicit unfinished work."""
from __future__ import annotations
import argparse
import ast
import hashlib
import json
import re
import sys
import xml.etree.ElementTree as ET
from collections import Counter
from pathlib import Path
from pypdf import PdfReader
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_m3_fulltext_rag import RAW_RESEARCH,RESEARCH,read_csv,budget_state
from scripts.run_m3_live_pilot import _library_index_snapshot
from scripts.ocr_full_textbook import digest,write_json

def load(path):return json.loads(path.read_text(encoding='utf-8-sig'))

def validate(bid):
    batch,docs=RAW_RESEARCH/bid,RESEARCH/bid
    out=ROOT/'docs/experiments/bkt-rag-improvement-20261001'
    cfg=load(batch/'frozen-config.json');ocr=Path(cfg['ocr_path'])
    signed=dict(cfg);signature=signed.pop('config_sha256')
    assert hashlib.sha256(json.dumps(signed,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==signature
    textbook=load(ocr);review=load(ocr.parent/'review-manifest.json')
    assert digest(ocr)==cfg['ocr_sha256']==review['ocr_sha256']
    assert [p['pdf_page'] for p in textbook['pages']]==list(range(1,348))
    assert digest(ocr.parent.parent/'textbook-full-ocr.json')==review['parent_ocr_sha256']
    assert _library_index_snapshot(batch/'index/fulltext.sqlite')['sha256']==cfg['index_sha256']
    for relative,expected in cfg['runtime_files_sha256'].items():
        assert digest(batch/'reproduction/prompt-source'/relative)==expected
    for page in textbook['pages']:
        assert digest(Path(page['image_path']))==page['image_sha256']
        assert page['human_review_status']=='not_reviewed'
        for correction in page.get('corrections',[]):
            assert all(correction.get(k) for k in ['reason','annotation_source'])
            assert correction.get('original_text',correction.get('original'))
            assert correction.get('corrected_text',correction.get('corrected'))
            assert correction.get('image_locator',correction.get('evidence_locator'))
            assert correction['annotation_source']=='ai'
    assert [r['pdf_page'] for r in review['receipts']]==list(range(1,348))
    assert review['human_review_status']=='not_reviewed'
    saved=load(docs/'human-files-preservation.json')
    human=RAW_RESEARCH/'bkt-rag-improvement-20261001/human-annotation'
    for name,value in saved.items():assert digest(human/name)==value['sha256'] and value['matches_original_manifest']
    cases=load(batch/'cases.json')
    assert Counter(c['split'] for c in cases['cases'])=={'historical':40,'development':40,'sealed_test':40}
    assert digest(batch/'cases.json')==cfg['cases_sha256']
    plan=read_csv(batch/'planned-matrix.csv')
    assert len(plan)==cfg['planned_cells']==480 and len({r['cell_id'] for r in plan})==480
    assert sum(int(r['expected_max_model_calls']) for r in plan)==360
    assert all(int(r['expected_max_model_calls'])==0 for r in plan if r['condition']=='retrieval-off_constraint-on')
    summary=load(docs/'evaluation-summary.json')
    assert summary==load(out/'data/fulltext-rag-summary.json')
    matrix=read_csv(out/'data/fulltext-matrix.csv')
    assert len(matrix)==len(cfg['conditions'])*len(cases['counts'])
    assert sum(int(r['observed']) for r in matrix)==summary['formal_observed_cells']
    assert sum(int(r['valid']) for r in matrix)==summary['formal_eligible_cells']
    assert sum(int(r['requests']) for r in matrix)==summary['budget']['phase_requests']['formal']
    actual=[]
    for path in (batch/'runs').glob('*/cells/*.json'):
        cell=load(path)
        phase=cell.get('phase') or load(path.parent.parent/'manifest.json')['phase']
        if phase in {'preflight','annotation'}:continue
        actual.append(cell)
        frozen_cell=cell['frozen_config']
        assert frozen_cell['experiment_config_sha256']==cfg['config_sha256']
        assert frozen_cell['prompt_sha256']==cfg['prompt_sha256']
        assert frozen_cell['model']==cfg['model'] and frozen_cell['sampling']['max_output_tokens']==8192
        assert frozen_cell['sampling']['thinking_enabled'] is False
        assert frozen_cell['sampling']['require_explicit_thinking_mode'] is True
        options=frozen_cell['m3_evidence_options']
        assert options['retrieval_enabled']==cell['phase'].startswith('retrieval-on_')
        assert options['evidence_constraint']==cell['phase'].endswith('_constraint-on')
        assert int(cell['model_calls'])<=1
        assert cell['developer_expected_action_applicable']==bool(cell['developer_expected_action_family'])
        if cell['split']!='historical':assert not cell['developer_expected_action_applicable']
        if cell['phase']=='retrieval-off_constraint-on':assert cell['model_calls']==0
    assert len(actual)==summary['formal_observed_cells']
    comparison_contract=load(out/'data/fulltext-ablation-figure-contract.json')
    assert comparison_contract['batch_id']==bid
    assert comparison_contract['source_summary_sha256']==digest(out/'data/fulltext-rag-summary.json')
    assert comparison_contract['question_annotations_sha256']==digest(batch/'ai-annotation/questions-120-ai.csv')
    for name,expected in comparison_contract['source_cell_sha256'].items():assert digest(batch/name)==expected
    for name,expected in comparison_contract['source_data_sha256'].items():assert digest(out/'data'/name)==expected
    decisions=read_csv(out/'data/fulltext-ablation-decisions.csv')
    assert len(decisions)==480 and len({(r['case_id'],r['condition']) for r in decisions})==480
    rates=read_csv(out/'data/fulltext-ablation-rates.csv')
    assert len(rates)==24
    for row in rates:
        expected=summary['conditions_by_split'][row['condition']][row['split']][row['metric']]
        assert int(row['numerator'])==expected['numerator'] and int(row['denominator'])==expected['denominator']
        assert float(row['value'])==expected['rate']
    transitions=read_csv(out/'data/fulltext-ablation-transitions.csv')
    assert len(transitions)==48 and sum(int(r['count']) for r in transitions)==240
    for row in transitions:
        before,after={'generated_both':(1,1),'generation_stopped':(1,0),
                      'generation_started':(0,1),'neither_generated':(0,0)}[row['transition']]
        grouped={}
        for decision in decisions:
            if decision['split']==row['split'] and decision['answerability']==row['answerability']:
                grouped.setdefault(decision['case_id'],{})[decision['condition']]=int(decision['generated'])
        off=f"retrieval-{row['retrieval']}_constraint-off";on=f"retrieval-{row['retrieval']}_constraint-on"
        assert len(grouped)==int(row['denominator'])
        assert sum((g[off],g[on])==(before,after) for g in grouped.values())==int(row['count'])
    assert len({(r['case_id'],r['phase']) for r in actual})==len(actual)
    resume_proof=docs/'checkpoint-resume-evidence.json'
    if resume_proof.exists():
        receipt=load(resume_proof)
        assert receipt['frozen_config_sha256']==cfg['config_sha256'] and not receipt['provider_retry_performed']
        for name,expected in receipt['completed_cell_sha256'].items():assert digest(batch/name)==expected
    planned_ids={(r['case_id'],r['condition']) for r in plan}
    actual_ids={(r['case_id'],r['phase']) for r in actual}
    assert actual_ids<=planned_ids
    if summary['matrix_complete']:
        assert actual_ids==planned_ids and summary['formal_eligible_cells']==480
        preflight=[load(p) for p in (batch/'runs').glob('*-preflight/cells/*.json')]
        assert len(preflight)==4 and all(c['status']=='completed' and c['evaluation_status']=='completed' for c in preflight)
        assert all(c['model_calls']==1 and c['locatable_references']>0 for c in preflight)
    if not summary['configuration_eligible']:assert not summary['formal_eligible_cells']
    for name in ['fulltext-retrieval.csv','fulltext-citation.csv']:
        for row in read_csv(out/'data'/name):
            if row.get('denominator')=='0' or row.get('n')=='0':assert row['value']==''
    markdown=(out/'M3-BKT-RAG-改进实验报告.md').read_text(encoding='utf-8')
    pdf_path=out/'M3-BKT-RAG-改进实验报告.pdf';pdf=PdfReader(pdf_path)
    normalize=lambda x:re.sub(r'\s+','',x)
    publication=[normalize(markdown),normalize('\n'.join(p.extract_text() for p in pdf.pages))]
    leaked=[];prompt_checks=0;actual_message_checks=0;wire_archives_checked=0;sources=[]
    for folder in RAW_RESEARCH.glob('m3-fulltext-rag-*'):
        archives=list((folder/'reproduction/requests').glob('*.json'))
        historical_proof=RESEARCH/folder.name/'wire-configuration-validation.json'
        if historical_proof.exists():
            assert len(archives)==load(historical_proof)['wire_request_count'], 'historical wire archive inaccessible or incomplete'
        wire_archives_checked+=len(archives)
        archives+=list((folder/'reproduction/annotation-requests').glob('*.json'))
        for path in archives:
            payload=load(path)
            sources.extend((str(path),m.get('content',''),'actual_message') for m in payload.get('body',{}).get('messages',[]))
    for path in (batch/'reproduction/prompt-source').rglob('*.py'):
        tree=ast.parse(path.read_text(encoding='utf-8'))
        sources.extend((str(path),n.value,'source_string') for n in ast.walk(tree) if isinstance(n,ast.Constant) and isinstance(n.value,str))
    for source,content,kind in sources:
        if not isinstance(content,str):continue
        text=normalize(content)
        if len(text)<(16 if kind=='actual_message' else 150):continue
        prompt_checks+=1
        actual_message_checks+=int(kind=='actual_message')
        snippets=[text] if len(text)<150 else [text[:150],text[-150:]]
        if any(snippet in article for snippet in snippets for article in publication):leaked.append(source)
    assert not leaked,leaked
    render=load(out/'validation/pdf-preview-fulltext/render-manifest.json')
    assert render['pdf_sha256']==digest(pdf_path) and render['pages']==len(pdf.pages)
    assert len(render['image_sha256'])==len(pdf.pages)
    assert render['visual_review_status']=='completed' and render['reviewed_pages']==list(range(1,len(pdf.pages)+1))
    for filename,expected in render['image_sha256'].items():
        assert digest(out/'validation/pdf-preview-fulltext'/filename)==expected
    figures=[]
    for n in ['07','08','09','10','11']:
        proof=load(out/f'validation/fulltext-figures/{n}-collision-corrected.json')
        assert proof['summary']['fail']==0 and proof['glyph_audit']['below_minimum_count']==0
        figures.append({'figure':n,'collision_failures':0,'minimum_rendered_glyph_pt':proof['glyph_audit']['minimum_found_pt']})
    visual=load(out/'validation/fulltext-figures/visual-review.json')
    assert visual['status']=='completed'
    for name,expected in visual['image_sha256'].items():assert digest(out/'figures'/name)==expected
    ledger=budget_state(batch)
    assert ledger==summary['budget'] and ledger['unresolved_requests']==0
    auth=cfg['authorization']
    assert ledger['cumulative_requests']<=auth['cumulative_ceiling'] and ledger['batch_requests']<=auth['additional_ceiling']
    for phase,count in ledger['phase_requests'].items():assert count<=auth['phase_ceilings'][phase]
    cleanup=load(docs/'service-cleanup-proof.json')
    assert cleanup['verified'] and not cleanup['remaining_processes'] and not cleanup['remaining_listeners']
    junit=out/'validation'/f"{bid.rsplit('-',1)[-1]}-final-tests.xml"
    suites=list(ET.parse(junit).getroot().iter('testsuite'))
    assert suites and all(int(s.get('failures','0'))==int(s.get('errors','0'))==0 for s in suites)
    passed=sum(int(s.get('tests','0'))-int(s.get('skipped','0')) for s in suites)
    annotation_complete=all(v.get('status')=='complete_ai_annotation' for v in summary['annotation_validation'].values())
    if annotation_complete or ((batch/'ai-annotation/retrieval-ai.csv').exists() and (batch/'ai-annotation/questions-120-ai.csv').exists()):
        ai=batch/'ai-annotation'
        expected_retrieval=read_csv(ai/'retrieval-historical-input.csv')+read_csv(ai/'retrieval-fulltext-input.csv')
        actual_retrieval=read_csv(ai/'retrieval-ai.csv')
        assert Counter(r['item_id'] for r in actual_retrieval)==Counter(r['item_id'] for r in expected_retrieval)
        spotcheck=load(ai/'retrieval-codex-spotcheck.json')
        assert spotcheck['api_requests']==0 and spotcheck['reviewed_rows']==17
        for row in actual_retrieval:
            if row['annotation_request_kind']!='local_codex_review_receipt':continue
            receipt=dict(spotcheck['records'][row['item_id']]);signature=receipt.pop('annotation_request_sha256')
            assert hashlib.sha256(json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==signature==row['annotation_request_sha256']
            assert receipt['source_excerpt_sha256']==hashlib.sha256(row['source_excerpt'].encode()).hexdigest()
            assert receipt['quote']==row['source_excerpt'].splitlines()[receipt['line_number']-1]
            assert receipt['relevance_grade']==row['relevance_grade'] and receipt['query']==row['query']
        expected_citation=read_csv(ai/'citation-input.csv');actual_citation=read_csv(ai/'citation-ai.csv') if (ai/'citation-ai.csv').exists() else []
        if actual_citation:assert Counter(r['item_id'] for r in actual_citation)==Counter(r['item_id'] for r in expected_citation)
        local_citations=load(ai/'citation-codex-review.json')
        assert local_citations['api_requests']==0 and local_citations['pairs']==234 and local_citations['unique_claims']==211
        assert local_citations['input_sha256']==digest(ai/'citation-input.csv')
        assert local_citations['judgments_source_sha256']==digest(ROOT/'scripts/local_citation_judgments.py')
        expected_by_id={r['item_id']:r for r in expected_citation}
        local_pair_rows=[r for r in actual_citation if r['record_type']=='claim_citation_pair']
        assert len(local_pair_rows)==234
        for row in local_pair_rows:
            receipt=dict(local_citations['records'][row['item_id']]);signature=receipt.pop('annotation_request_sha256')
            assert hashlib.sha256(json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==signature==row['annotation_request_sha256']
            assert receipt['source_text']==row['source_text']==expected_by_id[row['item_id']]['source_text']
            assert hashlib.sha256(row['source_text'].encode()).hexdigest()==receipt['source_text_sha256']
            assert receipt['claim_text']==row['claim_text'] and receipt['source_locator']==row['evidence_locator']
            peers=[r for r in local_pair_rows if (r['pool_version'],r['condition'],r['cell_id'],r['claim_id'])==(row['pool_version'],row['condition'],row['cell_id'],row['claim_id'])]
            assert set(receipt['joint_cited_item_ids'])=={r['item_id'] for r in peers}
            assert all(r['joint_support_label']==row['joint_support_label'] for r in peers)
            assert receipt['annotation_model']=='Codex' and row['annotation_request_kind']=='local_codex_review_receipt'
            for field in ['individual_support_label','individual_reason','joint_support_label','joint_reason']:assert receipt[field]==row[field]
        locator_receipts=load(ai/'no-citation-location-receipts.json') if (ai/'no-citation-location-receipts.json').exists() else {'api_requests':0,'source_locations_invented':0,'records':{}}
        assert locator_receipts['api_requests']==0 and locator_receipts['source_locations_invented']==0
        for row in actual_citation:
            if row['item_id'] not in locator_receipts['records']:continue
            receipt=dict(locator_receipts['records'][row['item_id']]);signature=receipt.pop('annotation_request_sha256')
            assert hashlib.sha256(json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==signature==row['annotation_request_sha256']
            assert receipt['evidence_locator']==json.loads(row['evidence_locator']) and receipt['evidence_locator']['document_chunk_page'] is None
            assert row['record_type']=='claim_without_explicit_citation' and row['individual_support_label']=='无支持'
        actual_questions=read_csv(ai/'questions-120-ai.csv')
        assert Counter(r['case_id'] for r in actual_questions)==Counter(c['case_id'] for c in cases['cases'])
        case_lookup={c['case_id']:c for c in cases['cases']}
        for row in actual_questions:
            assert row['question_text']==case_lookup[row['case_id']]['user_turns'][0]
            for witness in json.loads(row['evidence_locator']):
                n,a,b=int(witness['pdf_page']),int(witness['line_start']),int(witness['line_end'])
                lines=textbook['pages'][n-1]['corrected_text'].splitlines()
                assert 1<=n<=347 and 1<=a<=b<=len(lines) and b-a<8
                assert witness['quote']=='\n'.join(lines[a-1:b])
        assert len(read_csv(ai/'new-80-ai.csv'))==80
        span_receipts=load(ai/'question-span-normalization.json')
        assert span_receipts['api_requests']==0
        for receipt in span_receipts['records'].values():
            signed=dict(receipt);signature=signed.pop('normalization_receipt_sha256')
            assert hashlib.sha256(json.dumps(signed,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()==signature
            assert not receipt['semantic_verdict_changed_from_provider'] and receipt['provider_answerability']=='answerable'
            for witness in receipt['verified_split_ranges']:
                n,a,z=witness['pdf_page'],witness['line_start'],witness['line_end']
                assert z-a<8 and witness['quote']=='\n'.join(textbook['pages'][n-1]['corrected_text'].splitlines()[a-1:z])
        local_questions=load(ai/'first-turn-codex-review.json')
        assert local_questions['api_requests']==0 and len(local_questions['records'])==15
        for row in actual_questions:
            if row['annotation_request_kind']!='local_codex_review_receipt':continue
            receipt=dict(local_questions['records'][row['case_id']])
            signature=receipt.pop('annotation_request_sha256')
            encoded=json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
            assert hashlib.sha256(encoded).hexdigest()==signature==row['annotation_request_sha256']
            assert receipt['question_text']==row['question_text'] and receipt['answerability']==row['answerability']
        for row in actual_retrieval+actual_citation+actual_questions:
            assert row['annotation_source']=='ai' and row['human_review_status']=='not_reviewed'
            assert row['annotation_ocr_sha256']==cfg['ocr_sha256'] and len(row['annotation_request_sha256'])==64
            assert row.get('evidence_locator') and (row.get('rationale') or row.get('individual_reason'))
        for path in (batch/'reproduction/annotation-requests').glob('*.json'):
            request=load(path);body=request['body']
            assert body['model']==cfg['model'] and body['thinking']=={'type':'disabled'}
            assert body['max_tokens']==8192 and body['temperature']==0 and body['stream'] is False
            canonical=json.dumps(body,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
            assert hashlib.sha256(canonical).hexdigest()==request['body_sha256']
        evaluator=batch/'reproduction/evaluator2'
        if evaluator.exists():
            spec=load(evaluator/'frozen-evaluator.json')
            assert spec['evaluator_source_sha256']==digest(evaluator/'source.py')==digest(ROOT/'scripts/annotate_fulltext_retrieval_v2.py')
            assert hashlib.sha256(load(evaluator/'protocol.json')['rule'].encode()).hexdigest()==spec['rule_sha256']
            assert spec['frozen_generation_config_sha256']==cfg['config_sha256']
            assert spec['automatic_retries']==0 and spec['max_tokens']==8192 and spec['thinking']=={'type':'disabled'}
            for filename,expected in spec['input_sha256'].items():assert digest(ai/filename)==expected
            diagnosis=load(docs/'annotation-protocol-diagnosis.json')
            assert diagnosis['included_in_metrics'] is False and diagnosis['requests_charged']==1
            assert digest(batch/'reproduction/annotation-responses/retrieval-00000.json')==diagnosis['original_response_sha256']
    pending=['genuine personnel review']
    if not annotation_complete:
        failure=summary.get('annotation_provider_failure') or {}
        pending.append(f"{failure['pending_semantic_citation_pairs']} citation-pair semantic judgments after terminal HTTP {failure['http_status']}" if failure else 'AI semantic judgments on all applicable items')
    if not summary['matrix_complete']:pending.append('four passed real preflights and 480-cell formal matrix')
    result={'status':'complete_ai_scope' if summary['matrix_complete'] and summary['configuration_eligible'] and annotation_complete else 'partial_delivery',
        'batch_id':bid,'batch_status':summary['batch_status'],'ocr_pages':len(textbook['pages']),
        'ocr_visual_review_scope':'AI focus review on all page previews, enlarged inspections and recorded unresolved diagrams/tables; not character-perfect certification',
        'ocr_focus_corrections':textbook['coverage'].get('supplemental_corrected_pages',0),
        'ocr_unresolved_focus_pages':textbook['coverage'].get('unresolved_focus_pages',[]),
        'personnel_review_completed':False,'ai_semantic_annotation_completed':annotation_complete,
        'formal_matrix_completed':summary['matrix_complete'],'eligible_formal_cells':summary['formal_eligible_cells'],
        'saved_diagnostic_cells':summary['formal_observed_cells'],'planned_formal_cells':cfg['planned_cells'],
        'frozen_ocr_index_and_source_snapshots_unchanged':True,'human_review_files_unchanged':len(saved),
        'tests_passed':passed,'report_pdf_pages':len(pdf.pages),'all_report_pages_visually_reviewed':True,
        'prompt_content_leaks_detected':0,'prompt_strings_checked':prompt_checks,
        'actual_messages_checked':actual_message_checks,'historical_wire_archives_checked':wire_archives_checked,'prompt_archive_separate':True,
        'figures':figures,'budget':ledger,'cleanup_proof_sha256':digest(docs/'service-cleanup-proof.json'),
        'pending':pending,
        'current_report_sha256':{'markdown':digest(out/'M3-BKT-RAG-改进实验报告.md'),'pdf':digest(pdf_path)}}
    write_json(out/'validation/fulltext-validation.json',result)
    write_json(docs/'delivery-validation.json',result)
    return result

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch-id')
    a=p.parse_args();current=load(ROOT/'docs/experiments/bkt-rag-improvement-20261001/data/fulltext-rag-summary.json')
    print(json.dumps(validate(a.batch_id or current['batch_id']),ensure_ascii=False,indent=2))

if __name__=='__main__':main()

