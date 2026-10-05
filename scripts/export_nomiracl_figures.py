"""Export completed fixed-candidate aggregates with explicit units and denominators."""
from pathlib import Path
import json
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.run_nomiracl_zh_reranker import output_csv
from scripts.nomiracl_checkpoints import sha,write_json
OUT=ROOT/'docs/experiments/bkt-rag-improvement-20261001'

def main():
    path=OUT/'data/nomiracl-zh-evaluation.json';s=json.loads(path.read_text(encoding='utf-8'))
    assert s['status']=='completed'
    ranks=[]
    for key,r in s['ranking_metrics'].items():
        split,method=key.split(':',1)
        ranks.append({'split':split,'method':method,'mean_ndcg5':r['mean_nDCG_at_5'],
                      'relevant_queries':r['relevant_query_count'],'scope':'fixed judged candidates'})
    g=s['test_evidence_gate']
    gates=[{'metric':'False acceptance (FAR)','numerator':g['false_accepts'],'denominator':g['non_relevant_queries'],'value':g['false_accept_rate']},
           {'metric':'False rejection (FRR)','numerator':g['false_rejections'],'denominator':g['relevant_queries'],'value':g['false_rejection_rate']}]
    output_csv(OUT/'data/nomiracl-ranking-metrics.csv',ranks);output_csv(OUT/'data/nomiracl-gate-metrics.csv',gates)
    t=s['threshold_selection']
    write_json(OUT/'data/nomiracl-figure-contract.json',{
        'backend':'r','archetype':'quantitative grid; one plot area per figure',
        'claim':'Compare four methods on all fixed judged candidates; quantify the held-out gate tradeoff after dev-only threshold selection.',
        'ranking_role':'External fixed-candidate method comparison, relevant-query mean nDCG@5 on dev and test.',
        'gate_role':'Test false acceptance and false rejection with their distinct question denominators.',
        'dimensions_mm':[183,115],'minimum_glyph_pt':5,'exports':['SVG','PDF','600-dpi PNG'],
        'uncertainty':'Single fixed snapshot; descriptive aggregates, no seeds, confidence intervals or significance claims.',
        'exclusions':'95 absent corpus qrels (35 positive); no question excluded; complete usable candidate pool.',
        'alignment_gate':'Not applicable: each export contains one plot area, no comparable panel rectangles.',
        'threshold':t['threshold'],'dev_far':t['development_false_accept_rate'],
        'dev_relevant_acceptance':t['development_relevant_accept_rate'],
        'test_auc':g['reranker_max_score_auc'],'source_sha256':sha(path),'config_sha256':s['config_sha256']})
    print(json.dumps({'ranking_rows':len(ranks),'gate_rows':len(gates)}))
if __name__=='__main__':main()
