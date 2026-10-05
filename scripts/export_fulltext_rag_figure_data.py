"""Export every partition/condition point from observed evidence for R figures."""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:sys.path.insert(0,str(ROOT))
from scripts.annotate_fulltext_rag import write_csv
from scripts.ocr_full_textbook import write_json,digest
OUT=ROOT/'docs/experiments/bkt-rag-improvement-20261001'

def main()->None:
    data=json.loads((OUT/'data/fulltext-rag-summary.json').read_text(encoding='utf-8'))
    rows=[]
    for condition,groups in data['conditions_by_split'].items():
        for split,r in groups.items():
            row={'condition':condition,'split':split,**{k:v for k,v in r.items() if not isinstance(v,dict)}}
            for metric,values in r.items():
                if isinstance(values,dict):row.update({f'{metric}_{k}':v for k,v in values.items()})
            rows.append(row)
    write_csv(OUT/'data/fulltext-matrix.csv',rows)
    rows=[]
    for split in ['historical','development','sealed_test']:
        r=data['retrieval_by_pool_and_split'].get('fulltext',{}).get(split,{})
        for method in ['hash_dense','bm25','rrf']:
            values=r.get('methods',{}).get(method,{'recall_at_20':None,'ndcg_at_5':None,'scored_queries':0})
            for metric in ['recall_at_20','ndcg_at_5']:
                rows.append({'split':split,'method':method,'metric':metric,'value':values[metric],
                             'n':values['scored_queries'],'pool_rows':r.get('candidate_rows',0),
                             'unknown_rows':r.get('unknown_candidate_rows',0),
                             'status':'ai_exploratory' if values[metric] is not None else 'unavailable'})
    write_csv(OUT/'data/fulltext-retrieval.csv',rows)
    rows=[]
    for condition in data['conditions_by_split']:
        for split in ['historical','development','sealed_test']:
            r=data['citation_by_pool_condition_split'].get(f'fulltext/{condition}/{split}',{})
            for metric in ['single_support','claim_evidence_coverage']:
                v=r.get(metric,{'numerator':0,'denominator':0,'rate':None})
                rows.append({'condition':condition,'split':split,'metric':metric,'value':v['rate'],
                             'numerator':v['numerator'],'denominator':v['denominator']})
    write_csv(OUT/'data/fulltext-citation.csv',rows)
    write_json(OUT/'data/fulltext-figure-contract.json',{
        'backend':'R','archetype':'quantitative grid; three independent single-panel charts',
        'batch_id':data['batch_id'],'batch_status':data['batch_status'],
        'conclusions':{'07':f"show {data['formal_eligible_cells']} eligible and {data['formal_observed_cells']} observed cells of 480 planned",
                       '08':'show available conditional judged-pool metrics; missing judgments remain N/A',
                       '09':'show available pair support and claim coverage with distinct denominators'},
        'panel_alignment':'not applicable; each chart has one plot area',
        'unit':'constructed questions, citation pairs or extracted factual claims; no human learners',
        'uncertainty':'descriptive AI point estimates; no seed replicates or inferential intervals claimed',
        'missing_data':'all source rows retained; N/A points not drawn; zero denominators remain undefined',
        'human_review':'not completed','export':'183 x 115 mm, SVG/PDF and 600 dpi PNG',
        'source_data_sha256':{p.name:digest(p) for p in (OUT/'data').glob('fulltext-*.csv')}})
if __name__=='__main__':main()
