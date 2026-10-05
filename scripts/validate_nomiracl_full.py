"""Independently verify every fixed-candidate score, rank, query metric and dev-only gate."""
import argparse
import csv
import json
import math
import statistics
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.nomiracl_checkpoints import read_chunk,sha,signature,write_json

def ndcg(docs,rels):
    gain=lambda values:sum(v/math.log2(i+2) for i,v in enumerate(values[:5]))
    ideal=gain(sorted(rels.values(),reverse=True))
    return gain([rels[d] for d in docs])/ideal if ideal else 0.

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);p.add_argument('--output',type=Path,required=True);a=p.parse_args()
    run=a.run;results=run/'results';data=json.loads((run/'prepared-input.json').read_text(encoding='utf-8'))
    summary=json.loads((results/'nomiracl-zh-evaluation.json').read_text(encoding='utf-8'))
    cfg=json.loads((run/'frozen-config.json').read_text(encoding='utf-8'))
    assert summary['status']=='completed' and summary['external_model_requests']==0
    assert signature({k:v for k,v in cfg.items() if k!='config_sha256'})==cfg['config_sha256']==summary['config_sha256']
    assert sha(run/'prepared-input.json')==cfg['prepared_input_sha256']
    for name,expected in cfg['source_sha256'].items():assert sha(ROOT/name)==expected
    for name,expected in summary['output_sha256'].items():assert sha(results/name)==expected
    with (results/'nomiracl-zh-per-query.csv').open(encoding='utf-8-sig',newline='') as stream:queries=list(csv.DictReader(stream))
    with (results/'nomiracl-zh-candidate-ranks.csv').open(encoding='utf-8-sig',newline='') as stream:ranks=list(csv.DictReader(stream))
    records=data['queries'];qids=sorted(records);ids=sorted(r['docid'] for r in data['documents'])
    pairs=[[qid,d] for qid in qids for d in records[qid]['docids']]
    assert (len(queries),len(ranks),len(ids),len(pairs))==(3770,150396,31826,37599)
    assert len({r['qid'] for r in queries})==3770 and set(r['qid'] for r in queries)==set(records)
    methods=['BGE-M3 dense','BM25','BGE-M3 + BM25 RRF-60','BGE reranker v2-m3'];groups={};pair_keys=set()
    for r in ranks:
        key=(r['qid'],r['method']);unique=key+(r['docid'],)
        assert unique not in pair_keys;pair_keys.add(unique)
        assert math.isfinite(float(r['score'])) and r['method'] in methods
        rec=records[r['qid']]
        assert r['split']==rec['split'] and r['subset']==rec['subset']
        assert int(r['gold_relevance'])==rec['qrels'][r['docid']]
        assert int(r['candidate_pool_size'])==len(rec['docids'])
        groups.setdefault(key,[]).append(r)
    for r in queries:
        rec=records[r['qid']];assert r['split']==rec['split'] and int(r['gold_relevant'])==rec['gold_relevant']
        assert int(r['candidate_count'])==len(rec['docids'])
        for method in methods:
            ranked=groups[r['qid'],method]
            assert set(v['docid'] for v in ranked)==set(rec['docids'])
            assert [int(v['rank']) for v in ranked]==list(range(1,len(ranked)+1))
            assert ranked==sorted(ranked,key=lambda v:(-float(v['score']),v['docid']))
            assert math.isclose(float(r['ndcg5_'+method]),ndcg([v['docid'] for v in ranked],rec['qrels']),abs_tol=1e-12)
        assert float(r['max_reranker_score'])==max(float(v['score']) for v in groups[r['qid'],'BGE reranker v2-m3'])
    dev=[r for r in queries if r['split']=='dev'];test=[r for r in queries if r['split']=='test']
    assert (len(dev),len(test),sum(int(r['gold_relevant']) for r in dev),sum(int(r['gold_relevant']) for r in test))==(1475,2295,393,920)
    for split,rows in [('dev',dev),('test',test)]:
        for method in methods:
            actual=statistics.mean(float(r['ndcg5_'+method]) for r in rows if int(r['gold_relevant']))
            assert math.isclose(actual,summary['ranking_metrics'][split+':'+method]['mean_nDCG_at_5'],abs_tol=1e-12)
    # Independent sweep uses development records alone; test values cannot affect theta.
    neg=[r for r in dev if not int(r['gold_relevant'])];pos=[r for r in dev if int(r['gold_relevant'])]
    values=sorted({float(r['max_reranker_score']) for r in dev})
    feasible=[]
    for theta in [values[0]-1e-9,*values,values[-1]+1e-9]:
        fa=sum(float(r['max_reranker_score'])>=theta for r in neg);ta=sum(float(r['max_reranker_score'])>=theta for r in pos)
        if fa/len(neg)<=.05:feasible.append((ta,-theta,fa))
    ta,negative_theta,fa=max(feasible);theta=-negative_theta
    assert theta==summary['threshold_selection']['threshold']
    assert summary['threshold_selection']['development_false_accept_rate']==fa/len(neg)
    assert summary['threshold_selection']['development_relevant_accept_rate']==ta/len(pos)
    assert sha(run/'frozen-threshold.json')==summary['threshold_frozen_sha256']
    pos=[r for r in test if int(r['gold_relevant'])];neg=[r for r in test if not int(r['gold_relevant'])]
    fa=sum(float(r['max_reranker_score'])>=theta for r in neg);fr=sum(float(r['max_reranker_score'])<theta for r in pos)
    g=summary['test_evidence_gate'];assert (g['false_accepts'],g['non_relevant_queries'],g['false_rejections'],g['relevant_queries'])==(fa,1375,fr,920)
    assert g['false_accept_rate']==fa/1375 and g['false_rejection_rate']==fr/920
    wins=sum((float(x['max_reranker_score'])>float(y['max_reranker_score']))+.5*(float(x['max_reranker_score'])==float(y['max_reranker_score'])) for x in pos for y in neg)
    assert math.isclose(wins/(920*1375),g['reranker_max_score_auc'],abs_tol=1e-12)
    missing=data['missing_qrels'];assert (len(missing),sum(r['grade'] for r in missing))==(95,35)
    checkpoints=0
    for stage,keys,dim in [('documents',ids,1024),('queries',qids,1024),('reranker',pairs,0)]:
        state=summary['stages'][stage];folder=run/'checkpoints'/stage/state['device']
        assert state['status']=='completed' and state['rows']==len(keys)
        smoke=json.loads((folder/'smoke-proof.json').read_text(encoding='utf-8'));assert smoke['finite'] and smoke['fp32_parameters_verified']
        for start in range(0,len(keys),256):
            assert read_chunk(folder,start,keys[start:start+256],state['stage_sha256'],dim) is not None;checkpoints+=1
    cleanup=json.loads((run/'cleanup-proof.json').read_text(encoding='utf-8'));assert cleanup['all_workers_exited']
    latency=summary['latency_seconds']['bge_reranker_pair_scoring']*1000/37599
    assert latency==summary['latency_milliseconds']['reranker_mean_per_candidate_pair']
    write_json(a.output,{'status':'passed','queries':len(queries),'pairs':len(pairs),'rank_rows':len(ranks),
        'unique_documents':len(ids),'dev_queries':len(dev),'test_queries':len(test),'verified_atomic_chunks':checkpoints,
        'missing_qrels':95,'missing_positive_qrels':35,'query_exclusions':0,'all_scores_finite':True,
        'all_scores_and_ranks_complete':True,'threshold_independently_recomputed_dev_only':True,
        'test_metrics_independently_recomputed':True,'run_config_sha256':cfg['config_sha256'],
        'output_sha256':summary['output_sha256'],'external_model_requests':0,'cleanup_proof_sha256':sha(run/'cleanup-proof.json')})
    print(json.dumps({'status':'passed','queries':3770,'pairs':37599,'ranks':150396,'chunks':checkpoints}))
if __name__=='__main__':main()
