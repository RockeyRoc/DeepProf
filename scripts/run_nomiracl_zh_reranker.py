"""Evaluate BGE-M3, BM25, RRF and BGE reranker on official NoMIRACL zh candidates."""
from __future__ import annotations
import argparse, csv, gzip, hashlib, json, math, statistics, time, subprocess, sys
from collections import defaultdict
from pathlib import Path
from typing import Any
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.nomiracl_checkpoints import freeze,read_chunk,signature,write_json
OUT = ROOT / "docs/experiments/bkt-rag-improvement-20261001"
DATA = OUT / "data"

def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(4 * 1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def read_topics(path: Path) -> dict[str, str]:
    result = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip(): continue
        qid, query = line.split("\t", 1)
        if qid in result: raise ValueError("duplicate_topic_id:"+qid)
        if not query.strip(): raise ValueError("empty_query:"+qid)
        result[qid] = query
    return result

def read_qrels(path: Path) -> dict[str, dict[str, int]]:
    result: dict[str, dict[str, int]] = defaultdict(dict)
    for line in path.read_text(encoding="utf-8").splitlines():
        fields = line.split()
        if len(fields) != 4: raise ValueError("invalid_qrel_row")
        qid, _q0, docid, grade = fields[:4]
        if docid in result[qid]: raise ValueError("duplicate_qrel_pair")
        if int(grade) not in {0,1}: raise ValueError("invalid_qrel_grade")
        result[qid][docid] = int(grade)
    return dict(result)

def terms(text: str) -> list[str]:
    import re
    normalized = (text or "").lower()
    parts = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalized)
    result = []
    for token in parts:
        if re.fullmatch(r"[\u4e00-\u9fff]+", token):
            result.extend(token[i:i+2] for i in range(max(1, len(token)-1)))
            if len(token) == 1: result.append(token)
        else: result.append(token)
    return result

def bm25(query: str, docids: list[str], docs: dict[str, dict[str, str]], k1: float = 1.2, b: float = .75) -> dict[str, float]:
    tokenized = {docid: terms(docs[docid].get("title", "") + " " + docs[docid].get("text", "")) for docid in docids}
    n = len(docids)
    if not n: return {}
    lengths = {docid: len(tokens) for docid, tokens in tokenized.items()}
    avgdl = sum(lengths.values()) / n or 1.0
    counts: dict[str, dict[str, int]] = {}
    df: dict[str, int] = defaultdict(int)
    for docid, tokens in tokenized.items():
        c: dict[str, int] = defaultdict(int)
        for token in tokens: c[token] += 1
        counts[docid] = c
        for token in c: df[token] += 1
    query_terms = set(terms(query))
    scores = {}
    for docid in docids:
        score = 0.0
        for term in query_terms:
            tf = counts[docid].get(term, 0)
            if not tf: continue
            idf = math.log(1.0 + (n - df[term] + .5) / (df[term] + .5))
            score += idf * tf * (k1 + 1) / (tf + k1 * (1 - b + b * lengths[docid] / avgdl))
        scores[docid] = score
    return scores

def order(scores: dict[str, float]) -> list[str]:
    return sorted(scores, key=lambda docid: (-scores[docid], docid))

def ndcg_at_k(ranking: list[str], qrels: dict[str, int], k: int = 5) -> float:
    dcg = sum((2 ** qrels.get(doc, 0) - 1) / math.log2(i + 2) for i, doc in enumerate(ranking[:k]))
    ideal = sorted(qrels.values(), reverse=True)[:k]
    idcg = sum((2 ** rel - 1) / math.log2(i + 2) for i, rel in enumerate(ideal))
    return dcg / idcg if idcg else 0.0

def auc(scores: list[float], labels: list[int]) -> float | None:
    npos = sum(labels); nneg = len(labels) - npos
    if not npos or not nneg: return None
    order_idx = sorted(range(len(scores)), key=lambda i: scores[i])
    rank_sum = 0.0; pos_rank_sum = 0.0; i = 0
    while i < len(order_idx):
        j = i + 1
        while j < len(order_idx) and scores[order_idx[j]] == scores[order_idx[i]]: j += 1
        avg_rank = ((i + 1) + j) / 2
        pos_rank_sum += avg_rank * sum(labels[order_idx[t]] for t in range(i, j))
        i = j
    return (pos_rank_sum - npos * (npos + 1) / 2) / (npos * nneg)

def pick_threshold(dev: list[dict[str, Any]], far_cap: float = .05) -> dict[str, Any]:
    if not dev or any(r.get("split", "dev") != "dev" for r in dev): raise ValueError("threshold_requires_dev_only")
    if not all(math.isfinite(float(r["max_reranker_score"])) for r in dev): raise ValueError("non_finite_gate_score")
    if {r["gold_relevant"] for r in dev} != {0,1}: raise ValueError("threshold_requires_both_classes")
    scores = sorted({float(row["max_reranker_score"]) for row in dev})
    candidates = [scores[0] - 1e-9] + scores + [scores[-1] + 1e-9]
    feasible = []
    for threshold in candidates:
        neg = [row for row in dev if row["gold_relevant"] == 0]
        pos = [row for row in dev if row["gold_relevant"] == 1]
        far = sum(float(row["max_reranker_score"]) >= threshold for row in neg) / len(neg) if neg else None
        tpr = sum(float(row["max_reranker_score"]) >= threshold for row in pos) / len(pos) if pos else None
        if far is not None and far <= far_cap:
            feasible.append((tpr if tpr is not None else -1, threshold, far))
    if not feasible:
        threshold = scores[-1] + 1e-9
        return {"threshold": threshold, "development_false_accept_rate": 0.0,
                "development_relevant_accept_rate": 0.0, "target_false_accept_rate": far_cap}
    tpr, threshold, far = max(feasible, key=lambda item: (item[0], -item[1]))
    return {"threshold": threshold, "development_false_accept_rate": far,
            "development_relevant_accept_rate": tpr, "target_false_accept_rate": far_cap}


def collect_dataset(root):
    data=Path(root)/'data/chinese';docs={};records={};missing=[];counts={};duplicates=0
    with gzip.open(data/'corpus.jsonl.gz','rt',encoding='utf-8') as stream:
        for line in stream:
            row=json.loads(line);docid=row['docid']
            document={'docid':docid,'title':row.get('title',''),'text':row.get('text','')}
            if docid in docs:
                if docs[docid]!=document:raise ValueError('conflicting_corpus_docid:'+docid)
                duplicates+=1
            docs[docid]=document
    for split in ['dev','test']:
        for subset in ['relevant','non_relevant']:
            group=f'{split}.{subset}';topics=read_topics(data/'topics'/f'{group}.tsv');qrels=read_qrels(data/'qrels'/f'{group}.tsv')
            counts[group]=len(topics)
            for qid,query in topics.items():
                if qid in records:raise ValueError('duplicate_query_id:'+qid)
                if not qrels.get(qid):raise ValueError('no_candidate_qrels:'+qid)
                qmap={doc:grade for doc,grade in qrels[qid].items() if doc in docs}
                missing += [{'qid':qid,'docid':doc,'grade':grade,'group':group} for doc,grade in qrels[qid].items() if doc not in docs]
                if not qmap or (subset=='relevant' and not any(qmap.values())):raise ValueError('query_missing_usable_candidates:'+qid)
                if subset=='non_relevant' and any(qmap.values()):raise ValueError('non_relevant_positive:'+qid)
                records[qid]={'qid':qid,'query':query,'split':split,'subset':subset,'gold_relevant':int(subset=='relevant'),
                              'docids':list(qmap),'qrels':qmap}
    ids=sorted({d for r in records.values() for d in r['docids']})
    return {'documents':[docs[k] for k in ids],'queries':records,'source_topics_by_split_subset':counts,
            'identical_duplicate_corpus_rows':duplicates,'missing_qrels':missing}


def run_stage(run,stage,device,python,cleanup):
    import os
    folder=run/'checkpoints'/stage/device;folder.mkdir(parents=True,exist_ok=True)
    command=[str(python),str(ROOT/'scripts/nomiracl_inference_worker.py'),'--run',str(run),'--stage',stage,'--device',device]
    env={**os.environ,'PYTHONIOENCODING':'utf-8','PYTHONUNBUFFERED':'1','HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'}
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',env=env)
    write_json(run/'owned-process.json',{'pid':process.pid,'stage':stage,'device':device})
    try:
        with (folder/'worker.log').open('a',encoding='utf-8') as log:
            for line in process.stdout:
                log.write(line);log.flush()
                if line.strip().startswith('{'):print(line.strip(),flush=True)
        code=process.wait()
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=10)
            except subprocess.TimeoutExpired:process.kill();process.wait(timeout=10)
        cleanup.append({'pid':process.pid,'stage':stage,'device':device,'exit_code':process.returncode,'still_running':process.poll() is None})
        write_json(run/'cleanup-proof.json',{'owned_processes':cleanup,'all_workers_exited':all(not r['still_running'] for r in cleanup),'external_model_requests':0})
    state=json.loads((folder/'worker-state.json').read_text(encoding='utf-8'))
    if code and device=='cuda' and state.get('device_failure'):
        print(json.dumps({'stage':stage,'event':'CUDA_failure_CPU_entire_stage','error':state.get('error')}),flush=True)
        return run_stage(run,stage,'cpu',python,cleanup)
    if code:raise RuntimeError(f'inference_stage_failed:{stage}:{device}:{state.get("error")}')
    return state


def load_stage(run,state,keys,dimension):
    folder=run/'checkpoints'/state['stage']/state['device']
    arrays=[]
    for s in range(0,len(keys),256):
        values=read_chunk(folder,s,keys[s:s+256],state['stage_sha256'],dimension)
        if values is None:raise ValueError('missing_or_corrupt_completed_checkpoint')
        arrays.append(values)
    return np.concatenate(arrays),sum(json.loads((folder/f'{s:07d}.json').read_text(encoding='utf-8'))['elapsed_seconds'] for s in range(0,len(keys),256))


def output_csv(path,rows):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True);tmp=path.with_suffix('.csv.tmp')
    with tmp.open('w',encoding='utf-8-sig',newline='') as stream:
        writer=csv.DictWriter(stream,fieldnames=list(rows[0]) if rows else []);writer.writeheader();writer.writerows(rows)
    tmp.replace(path)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    for name in ['dataset-manifest','model-manifest','output-dir','checkpoint-dir','inference-python']:parser.add_argument('--'+name,type=Path,required=True)
    parser.add_argument('--device',choices=['cuda','cpu'],default='cuda')
    parser.add_argument('--embedding-batch-size',type=int,default=2);parser.add_argument('--reranker-batch-size',type=int,default=4)
    parser.add_argument('--resume',action='store_true');a=parser.parse_args()
    if a.embedding_batch_size!=2 or a.reranker_batch_size!=4:raise ValueError('approved_batch_sizes_are_2_and_4')
    run=a.checkpoint_dir.resolve();run.mkdir(parents=True,exist_ok=True);out=a.output_dir.resolve()
    ds=json.loads(a.dataset_manifest.read_text(encoding='utf-8'));models=json.loads(a.model_manifest.read_text(encoding='utf-8'))
    for entry in ds['files']:
        if sha(Path(ds['local_path'])/entry['path'])!=entry['sha256']:raise ValueError('dataset_file_hash_mismatch')
    for model in models['models']:
        for entry in model['files']:
            if sha(Path(model['local_path'])/entry['path'])!=entry['sha256']:raise ValueError('model_file_hash_mismatch')
    data=collect_dataset(ds['local_path']);qrecords=data['queries'];qids=sorted(qrecords)
    ids=sorted(d['docid'] for d in data['documents']);pairs=[[qid,doc] for qid in qids for doc in qrecords[qid]['docids']]
    if (len(qids),len(ids),len(pairs),len(data['missing_qrels']))!=(3770,31826,37599,95):raise ValueError('frozen_expected_full_counts_mismatch')
    prepared=run/'prepared-input.json'
    if prepared.exists():
        if json.loads(prepared.read_text(encoding='utf-8'))!=data:raise ValueError('existing_prepared_input_mismatch')
    else:write_json(prepared,data)
    source_names=['scripts/run_nomiracl_zh_reranker.py','scripts/nomiracl_checkpoints.py','scripts/nomiracl_inference_worker.py']
    cfg=freeze(run/'frozen-config.json',{'version':'nomiracl-zh-full-v2','dataset_revision':ds['revision'],
        'dataset_manifest_sha256':sha(a.dataset_manifest),'model_manifest_sha256':sha(a.model_manifest),
        'model_revisions':{m['repo_id']:m['revision'] for m in models['models']},
        'model_paths':{m['repo_id']:m['local_path'] for m in models['models']},'prepared_input_sha256':sha(prepared),
        'source_sha256':{n:sha(ROOT/n) for n in source_names},'source_files':ds['files'],
        'inference_python':str(a.inference_python.resolve()),'dependency_lock_sha256':sha(OUT/'tools/rag-requirements.lock'),
        'initial_device':a.device,'cpu_fallback':'restart entire failed CUDA stage in isolated CPU FP32 worker, 16 threads',
        'precision':'float32','embedding_batch_size':2,'reranker_batch_size':4,'checkpoint_rows':256,
        'document_max_length':512,'query_max_length':256,'reranker_max_length':512,'normalize_reranker':False,
        'bm25':{'k1':1.2,'b':.75,'tokenizer':'existing Chinese bigram / lowercase alphanumeric'},
        'rrf_k':60,'sort_ties':'docid ascending','gate_far_cap':.05,'external_model_requests':0},resume=a.resume)
    output_csv(run/'missing-qrels.csv',data['missing_qrels'])
    cleanup=[];started=time.perf_counter()
    try:
        write_json(run/'run-state.json',{'status':'running','config_sha256':cfg['config_sha256'],'external_model_requests':0})
        stages={name:run_stage(run,name,a.device,a.inference_python,cleanup) for name in ['documents','queries','reranker']}
        document_vectors,t_docs=load_stage(run,stages['documents'],ids,1024)
        query_vectors,t_queries=load_stage(run,stages['queries'],qids,1024)
        pair_scores,t_pairs=load_stage(run,stages['reranker'],pairs,0)
        document_vectors/=np.linalg.norm(document_vectors,axis=1,keepdims=True)
        query_vectors/=np.linalg.norm(query_vectors,axis=1,keepdims=True)
        docs={d['docid']:d for d in data['documents']};doc_index={d:i for i,d in enumerate(ids)}
        rerank={};rank_rows=[];per_query=[];scoring_start=time.perf_counter()
        for (qid,doc),score in zip(pairs,pair_scores,strict=True):rerank.setdefault(qid,{})[doc]=float(score)
        for qi,qid in enumerate(qids):
            rec=qrecords[qid];dense={doc:float(np.dot(query_vectors[qi],document_vectors[doc_index[doc]])) for doc in rec['docids']}
            lexical=bm25(rec['query'],rec['docids'],docs);dr={d:i+1 for i,d in enumerate(order(dense))};br={d:i+1 for i,d in enumerate(order(lexical))}
            scores={'BGE-M3 dense':dense,'BM25':lexical,'BGE-M3 + BM25 RRF-60':{d:1/(60+dr[d])+1/(60+br[d]) for d in rec['docids']},'BGE reranker v2-m3':rerank[qid]}
            row={'qid':qid,'split':rec['split'],'subset':rec['subset'],'gold_relevant':rec['gold_relevant'],
                 'candidate_count':len(rec['docids']),'max_reranker_score':max(rerank[qid].values())}
            for method,values in scores.items():
                if set(values)!=set(rec['docids']) or not all(math.isfinite(v) for v in values.values()):raise ValueError('incomplete_or_invalid_candidate_scores')
                ranked=order(values);row[f'ndcg5_{method}']=ndcg_at_k(ranked,rec['qrels'])
                for rank,doc in enumerate(ranked,1):rank_rows.append({'qid':qid,'split':rec['split'],'subset':rec['subset'],'method':method,
                    'rank':rank,'docid':doc,'gold_relevance':rec['qrels'][doc],'score':values[doc],'candidate_pool_size':len(rec['docids'])})
            per_query.append(row)
        scoring_elapsed=time.perf_counter()-scoring_start
        dev=[r for r in per_query if r['split']=='dev'];test=[r for r in per_query if r['split']=='test']
        if (len(dev),len(test),len(rank_rows))!=(1475,2295,150396):raise ValueError('final_output_coverage_mismatch')
        threshold={**pick_threshold(dev),'selection_rule':'maximize dev relevant acceptance subject to FAR <=5%; ties choose lower threshold',
                   'development_rows_sha256':signature(dev),'config_sha256':cfg['config_sha256']}
        threshold_path=run/'frozen-threshold.json'
        if threshold_path.exists() and json.loads(threshold_path.read_text(encoding='utf-8'))!=threshold:raise ValueError('threshold_changed_on_resume')
        if not threshold_path.exists():write_json(threshold_path,threshold)
        theta=threshold['threshold'];neg=[r for r in test if not r['gold_relevant']];pos=[r for r in test if r['gold_relevant']]
        far=sum(r['max_reranker_score']>=theta for r in neg);frr=sum(r['max_reranker_score']<theta for r in pos)
        missing=data['missing_qrels']
        summary={'schema_version':'deepprof-nomiracl-zh-candidate-eval-v2','status':'completed','dataset_id':ds['dataset_id'],
            'dataset_revision':ds['revision'],'model_revisions':cfg['model_revisions'],'config_sha256':cfg['config_sha256'],
            'inference_precision':'float32; TF32 disabled','device':{s:r['device'] for s,r in stages.items()},
            'embedding_batch_size':2,'reranker_batch_size':4,'external_model_requests':0,
            'candidate_pool_note':'All available officially judged candidates across every dev/test query; 95 absent corpus qrels disclosed, no query excluded. Conditional ranking/gate, not corpus-wide retrieval or generated-answer accuracy.',
            'counts':{'queries_total':len(qids),'candidate_documents_unique':len(ids),'candidate_pairs':len(pairs),
                'identical_duplicate_corpus_rows':data['identical_duplicate_corpus_rows'],
                'dev_queries':len(dev),'test_queries':len(test),'dev_relevant':sum(r['gold_relevant'] for r in dev),
                'dev_non_relevant':sum(not r['gold_relevant'] for r in dev),'test_relevant':len(pos),'test_non_relevant':len(neg),
                'source_topics_by_split_subset':data['source_topics_by_split_subset'],
                'missing_qrel_rows_by_split_subset':{g:sum(r['group']==g for r in missing) for g in data['source_topics_by_split_subset']},
                'missing_positive_qrel_rows_by_split_subset':{g:sum(r['group']==g and r['grade']>0 for r in missing) for g in data['source_topics_by_split_subset']},
                'excluded_queries_without_present_positive_by_split_subset':dict.fromkeys(data['source_topics_by_split_subset'],0)},
            'threshold_selection':threshold,'threshold_frozen_sha256':sha(threshold_path),
            'test_evidence_gate':{'false_accepts':far,'non_relevant_queries':len(neg),'false_accept_rate':far/len(neg),
                'false_rejections':frr,'relevant_queries':len(pos),'false_rejection_rate':frr/len(pos),
                'reranker_max_score_auc':auc([r['max_reranker_score'] for r in test],[r['gold_relevant'] for r in test])},
            'ranking_metrics':{f'{split}:{method}':{'relevant_query_count':sum(r['gold_relevant'] for r in rows),
                 'mean_nDCG_at_5':statistics.mean(r[f'ndcg5_{method}'] for r in rows if r['gold_relevant']),
                 'candidate_conditional':True} for split,rows in [('dev',dev),('test',test)] for method in scores},
            'latency_seconds':{'bge_m3_document_encode':t_docs,'bge_m3_query_encode':t_queries,'bge_m3_query_candidate_scoring':scoring_elapsed,
                'bge_reranker_pair_scoring':t_pairs,'current_orchestration_wall':time.perf_counter()-started},
            'latency_milliseconds':{'reranker_mean_per_candidate_pair':t_pairs*1000/len(pairs)},
            'stages':stages,'download_manifest_sha256':sha(a.dataset_manifest),'model_manifest_sha256':sha(a.model_manifest)}
        out.mkdir(parents=True,exist_ok=True)
        output_csv(out/'nomiracl-zh-per-query.csv',per_query);output_csv(out/'nomiracl-zh-candidate-ranks.csv',rank_rows)
        summary['output_sha256']={p.name:sha(p) for p in [out/'nomiracl-zh-per-query.csv',out/'nomiracl-zh-candidate-ranks.csv']}
        write_json(out/'nomiracl-zh-evaluation.json',summary)
        write_json(run/'run-state.json',{'status':'completed','config_sha256':cfg['config_sha256'],'output_sha256':summary['output_sha256'],
                                        'external_model_requests':0})
        print(json.dumps({k:summary[k] for k in ['status','counts','ranking_metrics','test_evidence_gate']},ensure_ascii=False),flush=True)
        return 0
    except BaseException as exc:
        write_json(run/'run-state.json',{'status':'interrupted' if isinstance(exc,KeyboardInterrupt) else 'failed',
                                        'error':str(exc),'config_sha256':cfg['config_sha256'],'external_model_requests':0})
        raise

if __name__=='__main__':raise SystemExit(main())

