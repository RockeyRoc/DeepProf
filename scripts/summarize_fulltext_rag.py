"""Derive report data from observed cells and labeled pools; never fill missing scores."""
from __future__ import annotations
import argparse
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.run_m3_fulltext_rag import RAW_RESEARCH,RESEARCH,SOURCE_HOME,read_csv,budget_state,ACTIVE_BATCH
from scripts.ocr_full_textbook import write_json,digest
from scripts.validate_bkt_rag_annotations import validate_ai_csv,CITATION_LABELS,ANSWERABILITY_LABELS


def ratio(n:int,d:int)->dict[str,Any]:
    return {'numerator':n,'denominator':d,'rate':n/d if d else None}


def dcg(grades:list[int],k:int=5)->float:
    return sum((2**g-1)/math.log2(i+1) for i,g in enumerate(grades[:k],1))


def retrieval_metrics(rows:list[dict[str,str]])->dict[str,Any]:
    grouped=defaultdict(list)
    for r in rows:grouped[r['case_id']].append(r)
    complete={k:v for k,v in grouped.items() if all(x.get('relevance_grade') in {'0','1','2'} for x in v)}
    methods={}
    for method,field in [('hash_dense','hash_dense_rank'),('bm25','bm25_rank'),('rrf','rrf_rank')]:
        recall=[];ndcg=[]
        for data in complete.values():
            if len({r['chunk_id'] for r in data})!=len(data):raise ValueError('duplicate_case_candidate')
            total=sum(int(r['relevance_grade'])>0 for r in data)
            if not total:continue
            ranked=sorted([r for r in data if str(r.get(field,'')).strip()],key=lambda r:int(r[field]))
            recall.append(sum(int(r['relevance_grade'])>0 for r in ranked if int(r[field])<=20)/total)
            ideal=dcg(sorted([int(r['relevance_grade']) for r in data],reverse=True))
            actual=sum((2**int(r['relevance_grade'])-1)/math.log2(int(r[field])+1)
                       for r in ranked if int(r[field])<=5)
            ndcg.append(actual/ideal if ideal else 0)
        methods[method]={'recall_at_20':sum(recall)/len(recall) if recall else None,
                        'ndcg_at_5':sum(ndcg)/len(ndcg) if ndcg else None,'scored_queries':len(recall)}
    return {'queries':len(grouped),'fully_judged_queries':len(complete),'candidate_rows':len(rows),
            'unknown_candidate_rows':sum(r.get('relevance_grade')=='无法判定' for r in rows),
            'methods':methods,'recall_denominator':'relevant passages in each fully judged candidate pool; conditional, not corpus-wide'}


def summarize(batch:Path)->dict[str,Any]:
    docs=RESEARCH/batch.name;folder=batch/'ai-annotation'
    state=json.loads((docs/'batch-state.json').read_text(encoding='utf-8'))
    eligible=state.get('configuration_eligible', True) and state['status']=='completed'
    cells=[]
    for run in (batch/'runs').glob('*'):
        if not (run/'manifest.json').exists():continue
        m=json.loads((run/'manifest.json').read_text(encoding='utf-8'))
        if m.get('phase') in {'preflight','annotation'}:continue
        condition=run.name.removeprefix(batch.name+'-')
        cells += [{**json.loads(p.read_text(encoding='utf-8')),'condition':condition} for p in (run/'cells').glob('*.json')]
    questions_path=folder/'questions-120-ai.csv'
    if not questions_path.exists():questions_path=folder/'questions-120-ai.partial.csv'
    questions=read_csv(questions_path) if questions_path.exists() else []
    gold={r['case_id']:r for r in questions}
    retrieval=read_csv(folder/'retrieval-ai.csv') if (folder/'retrieval-ai.csv').exists() else []
    citation=read_csv(folder/'citation-ai.csv') if (folder/'citation-ai.csv').exists() else []
    reports={}
    for condition in ['retrieval-on_constraint-on','retrieval-on_constraint-off','retrieval-off_constraint-on','retrieval-off_constraint-off']:
        reports[condition]={}
        for split in ['historical','development','sealed_test']:
            observed=[c for c in cells if c['condition']==condition and c['split']==split]
            valid=[c for c in observed if eligible and c['status']=='completed' and c['evaluation_status'] in {'completed','not_applicable'}]
            expected=[c for c in valid if c.get('developer_expected_action_applicable')]
            ans=[c for c in valid if gold.get(c['case_id'],{}).get('answerability')=='answerable']
            no=[c for c in valid if gold.get(c['case_id'],{}).get('answerability')=='insufficient_evidence']
            # This metric concerns whether the system proceeds to generation. It does not
            # equate an ask/hint with a correct, complete answer.
            reports[condition][split]={'observed':len(observed),'planned':40,'valid':len(valid),
                'generated':sum(c['model_calls']>0 for c in valid),'requests':sum(c['model_calls'] for c in observed),
                'failed':sum(c['status']!='completed' for c in observed),
                'excluded_configuration':len(observed) if not eligible else 0,
                'unknown_answerability':sum(gold.get(c['case_id'],{}).get('answerability') not in {'answerable','insufficient_evidence'} for c in valid),
                'false_accept_generation':ratio(sum(c['model_calls']>0 for c in no),len(no)),
                'false_reject_no_generation':ratio(sum(c['model_calls']==0 for c in ans),len(ans)),
                'developer_action_match':ratio(sum(bool(c['action_family_match']) for c in expected),len(expected))}
    retrieval_reports={}
    for pool in ['historical','fulltext']:
        retrieval_reports[pool]={}
        for split in ['historical','development','sealed_test']:
            selected=[r for r in retrieval if r['pool_version']==pool and r['split']==split]
            if selected:retrieval_reports[pool][split]=retrieval_metrics(selected)
    citation_reports={}
    cell_split={c['cell_id']:c['split'] for c in cells}
    for pool in ['historical','fulltext']:
        conditions=['historical'] if pool=='historical' else list(reports)
        for condition in conditions:
            for split in ['historical','development','sealed_test']:
                selected=[r for r in citation if r['pool_version']==pool and (pool=='historical' or r.get('condition')==condition)
                          and (split=='historical' if pool=='historical' else cell_split.get(r['cell_id'])==split)]
                if not selected:continue
                pairs=[r for r in selected if r['record_type']=='claim_citation_pair']
                claims={r['claim_id']:r['joint_support_label'] for r in selected if r.get('claim_id')}
                citation_reports[f'{pool}/{condition}/{split}']={'records':len(selected),'pairs':len(pairs),
                    'source_location_rate':ratio(sum(r.get('source_location_verified') in {'True','true','1'} for r in pairs),len(pairs)),
                    'single_support':ratio(sum(r['individual_support_label']=='完整支持' for r in pairs),len(pairs)),
                    'claim_evidence_coverage':ratio(sum(s=='完整支持' for s in claims.values()),len(claims)),
                    'single_label_counts':dict(Counter(r['individual_support_label'] for r in pairs)),
                    'claims_without_citation':sum(r['record_type']=='claim_without_explicit_citation' for r in selected),
                    'unknown_pairs':sum(r['individual_support_label']=='无法判定' for r in pairs)}
    validation={}
    for stem,field,allowed in [('citation','individual_support_label',CITATION_LABELS),
                            ('retrieval','relevance_grade_0_1_2',{'0','1','2','无法判定'}),
                            ('new-80','answerability',ANSWERABILITY_LABELS),
                            ('questions-120','answerability',ANSWERABILITY_LABELS)]:
        path=folder/f'{stem}-ai.csv'
        validation[stem]=validate_ai_csv(path,field,allowed) if path.exists() else {'status':'missing'}
        if path.exists():
            invalid=any(validation[stem].get(k) for k in ['duplicate_ids','empty_ids','invalid_labels',
                'missing_label_or_reason','provenance_errors','missing_evidence_locator','invalid_joint_support'])
            if invalid:raise ValueError('invalid_AI_annotation:' + stem)
            validation[stem]['status']='complete_ai_annotation'
    frozen=json.loads((batch/'frozen-config.json').read_text(encoding='utf-8'))
    ocr=Path(frozen.get('ocr_path',SOURCE_HOME/'course/fulltext-ocr/bce3d6d54eaf/textbook-full-ocr.json'))
    if digest(ocr)!=frozen['ocr_sha256']:raise ValueError('summary_frozen_ocr_mismatch')
    coverage=json.loads(ocr.read_text(encoding='utf-8'))['coverage']
    result={'schema_version':'deepprof-fulltext-report-data-v1','batch_id':batch.name,
        'batch_status':state['status'],'formal_observed_cells':len(cells),'formal_planned_cells':480,
        'configuration_eligible':eligible,'stop_reason':state.get('reason'),
        'formal_eligible_cells':sum(r['valid'] for group in reports.values() for r in group.values()),
        'matrix_complete':len(cells)==480 and len({(c['case_id'],c['condition']) for c in cells})==480
                          and len({c['case_id'] for c in cells})==120,
        'ocr_coverage':coverage,'ocr_sha256':digest(ocr),'configuration':frozen,
        'index_manifest':json.loads((batch/'index/index-manifest.json').read_text(encoding='utf-8')),
        'budget':budget_state(batch),'conditions_by_split':reports,'retrieval_by_pool_and_split':retrieval_reports,
        'citation_by_pool_condition_split':citation_reports,'annotation_validation':validation,
        'citation_codex_review':json.loads((docs/'citation-codex-review-summary.json').read_text(encoding='utf-8')) if (docs/'citation-codex-review-summary.json').exists() else None,
        'annotation_counts':{'retrieval':len(retrieval),'citation':len(citation),'questions':len(questions)},
        'answerability_counts':dict(Counter(r['answerability'] for r in questions)),
        'annotation_versions':dict(Counter(r['annotation_version'] for r in retrieval+citation+questions)),
        'retrieval_protocol_exceptions':sum(bool(r.get('annotation_protocol_exception')) for r in retrieval),
        'independent_evaluator':json.loads((batch/'reproduction/evaluator2/frozen-evaluator.json').read_text(encoding='utf-8')) if (batch/'reproduction/evaluator2/frozen-evaluator.json').exists() else None,
        'invalid_annotation_predecessor':json.loads((docs/'annotation-protocol-diagnosis.json').read_text(encoding='utf-8')) if (docs/'annotation-protocol-diagnosis.json').exists() else None,
        'retrieval_spotcheck':json.loads((docs/'retrieval-spotcheck-validation.json').read_text(encoding='utf-8')) if (docs/'retrieval-spotcheck-validation.json').exists() else None,
        'question_span_normalization':json.loads((docs/'question-span-normalization-validation.json').read_text(encoding='utf-8')) if (docs/'question-span-normalization-validation.json').exists() else None,
        'checkpoint_resume_evidence_sha256':digest(docs/'checkpoint-resume-evidence.json') if (docs/'checkpoint-resume-evidence.json').exists() else None,
        'post_freeze_code_errata':json.loads((docs/'ocr-code-limitations.json').read_text(encoding='utf-8')) if (docs/'ocr-code-limitations.json').exists() else None,
        'annotation_send_status':json.loads((docs/'annotation-send-block.json').read_text(encoding='utf-8')) if (docs/'annotation-send-block.json').exists() else None,
        'annotation_provider_failure':json.loads((docs/'annotation-provider-failure.json').read_text(encoding='utf-8')) if (docs/'annotation-provider-failure.json').exists() else None,
        'human_agreement':'not_computed; no genuine personnel reviews received',
        'acceptance_definition':'generation on insufficient-evidence questions; not answer correctness',
        'raw_prompts_in_report':False,'cleanup':state.get('cleanup')}
    write_json(docs/'evaluation-summary.json',result)
    write_json(ROOT/'docs/experiments/bkt-rag-improvement-20261001/data/fulltext-rag-summary.json',result)
    return result


def main()->None:
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--batch-id',default=ACTIVE_BATCH);a=p.parse_args()
    r=summarize(RAW_RESEARCH/a.batch_id)
    print(json.dumps({k:r[k] for k in ['batch_id','batch_status','formal_observed_cells','annotation_counts','budget']},ensure_ascii=False,indent=2))
if __name__=='__main__':main()
