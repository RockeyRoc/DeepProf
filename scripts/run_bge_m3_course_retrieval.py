"""Versioned, offline BGE-M3 retrieval comparison over the frozen course index."""
from __future__ import annotations
import argparse, csv, hashlib, json, sqlite3, sys, time
from pathlib import Path
from typing import Any
import numpy as np
import torch
from FlagEmbedding import BGEM3FlagModel, FlagReranker

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path: sys.path.insert(0, str(ROOT))
from evaluation.dev_cases import build_cases
from library.concept_queries import expand_concept_query
from library.embeddings import HashingEmbedder, retrieval_index_profile
from library.hybrid_retrieval import reciprocal_rank_fusion
from library.chunking import chunking_version
from runtime.storage.resource_store import SqliteResourceStore


def sha(path: Path) -> str:
    h=hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(4*1024*1024), b''): h.update(block)
    return h.hexdigest()

def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('w', encoding='utf-8-sig', newline='') as stream:
        writer=csv.DictWriter(stream, fieldnames=list(rows[0]) if rows else [])
        writer.writeheader(); writer.writerows(rows)

def main() -> int:
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--database',type=Path,required=True)
    parser.add_argument('--model-manifest',type=Path,required=True)
    parser.add_argument('--private-output-dir',type=Path,required=True)
    parser.add_argument('--index-dir',type=Path,required=True)
    parser.add_argument('--summary-output',type=Path,required=True)
    parser.add_argument('--per-stage-k',type=int,default=50)
    parser.add_argument('--device',choices=('auto','cpu','cuda'),default='auto')
    args=parser.parse_args()
    if args.per_stage_k != 50: raise ValueError('study_protocol_requires_stage_k_50')
    manifest=json.loads(args.model_manifest.read_text(encoding='utf-8'))
    model_paths={m['repo_id']:Path(m['local_path']) for m in manifest['models']}
    revisions={m['repo_id']:m['revision'] for m in manifest['models']}
    embed_path=model_paths['BAAI/bge-m3']; rerank_path=model_paths['BAAI/bge-reranker-v2-m3']
    device='cuda' if args.device=='auto' and torch.cuda.is_available() else ('cpu' if args.device=='auto' else args.device)
    if device=='cuda' and not torch.cuda.is_available(): raise RuntimeError('cuda_requested_but_unavailable')
    args.private_output_dir.mkdir(parents=True,exist_ok=True); args.index_dir.mkdir(parents=True,exist_ok=True)

    con=sqlite3.connect(args.database.resolve().as_uri()+'?mode=ro',uri=True,timeout=10)
    con.row_factory=sqlite3.Row
    store=SqliteResourceStore(con)
    chunks=con.execute("SELECT c.document_id,c.chunk_id,c.resource_id,r.course_id,c.page,c.printed_page,c.chapter,c.ordinal,c.section,c.text,r.title,r.source_url,r.status,r.visibility,r.owner_id FROM library_chunks c JOIN library_resources r ON r.resource_id=c.resource_id WHERE r.status='active' AND r.course_id=? AND c.reliable=1 AND (r.visibility='public' OR r.owner_id=?) ORDER BY c.resource_id,c.ordinal",('ds.c_language.v1','local')).fetchall()
    if not chunks: raise ValueError('no_reliable_course_chunks')
    chunk_rows=[dict(row) for row in chunks]
    cases=build_cases()['cases']; queries=[]
    for case in cases:
        query=str((case.get('user_turns') or [''])[0]).strip()
        expanded,_=expand_concept_query(query,'ds.c_language.v1',[case['concept_id']])
        queries.append({'case':case,'query':query,'expanded':expanded})
    chunk_texts=[str(row.get('text') or '') for row in chunk_rows]

    index_started=time.perf_counter()
    embedder=BGEM3FlagModel(str(embed_path),use_fp16=False,devices=device,normalize_embeddings=True)
    encoded=embedder.encode(chunk_texts,batch_size=2,max_length=512,return_dense=True,
        return_sparse=False,return_colbert_vecs=False)['dense_vecs']
    vectors=np.asarray(encoded,dtype=np.float32)
    vectors/=np.maximum(np.linalg.norm(vectors,axis=1,keepdims=True),1e-12)
    if vectors.ndim != 2 or vectors.shape[1] != 1024:
        raise ValueError(f"unexpected_bge_m3_embedding_dimension:{vectors.shape}")
    index_elapsed=time.perf_counter()-index_started
    config={'schema_version':'deepprof-rag-index-profile-v1','model_id':'BAAI/bge-m3',
        'model_revision':revisions['BAAI/bge-m3'],'embedding_dimension':int(vectors.shape[1]),
        'reranker_id':'BAAI/bge-reranker-v2-m3','reranker_revision':revisions['BAAI/bge-reranker-v2-m3'],
        'dtype':'float32','inference_precision':'float32','embedding_batch_size':2,
        'normalization':'L2','chunking_version':chunking_version(800,120),
        'chunk_size':800,'chunk_overlap':120,'source_database_sha256':sha(args.database),
        'source_chunk_count':len(chunk_rows),'retrieval_query_expansion':'expand_concept_query + concept_ids',
        'configuration_note':'versioned semantic index; no hashing vectors mixed'}
    config_id=hashlib.sha256(json.dumps(config,sort_keys=True,separators=(',',':')).encode()).hexdigest()
    config['config_id']=config_id
    versioned_index_dir=args.index_dir/config_id[:16]
    versioned_index_dir.mkdir(parents=True,exist_ok=True)
    np.save(versioned_index_dir/'dense-vectors.npy',vectors,allow_pickle=False)
    index_rows=[{'chunk_id':row['chunk_id'],'document_id':row['document_id'],'resource_id':row['resource_id'],
                 'page':row['page'],'printed_page':row['printed_page'],'chapter':row['chapter'],
                 'section':row['section'],'ordinal':row['ordinal']} for row in chunk_rows]
    (versioned_index_dir/'chunk-map.json').write_text(json.dumps(index_rows,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    config['vector_file_sha256']=sha(versioned_index_dir/'dense-vectors.npy')
    config['chunk_map_sha256']=sha(versioned_index_dir/'chunk-map.json')
    (versioned_index_dir/'index-profile.json').write_text(json.dumps(config,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')

    profile_embedder=HashingEmbedder()
    old_profile=retrieval_index_profile(profile_embedder,chunking_version=chunking_version(800,120))
    filters={'course_id':'ds.c_language.v1','owner_id':'local'}
    raw_by_method={key:[] for key in ['hash_dense','bm25','hash_bm25_rrf','bge_m3_dense','bge_m3_bm25_rrf','bge_m3_bm25_reranked']}
    timing=[]
    chunk_by_id={row['chunk_id']:row for row in chunk_rows}
    reranker=FlagReranker(str(rerank_path),use_fp16=False,devices=device)
    for i,item in enumerate(queries):
        started=time.perf_counter(); qvec=profile_embedder.embed([item['expanded']])[0]
        dense=store.search(qvec,score=HashingEmbedder.similarity,top_k=50,min_score=-1.0,
            retrieval_profile=old_profile,**filters); hash_ms=(time.perf_counter()-started)*1000
        started=time.perf_counter(); lexical=store.search_bm25(item['expanded'],top_k=50,**filters)
        bm25_ms=(time.perf_counter()-started)*1000
        started=time.perf_counter(); hash_fused=reciprocal_rank_fusion({'dense':dense,'bm25':lexical},rank_constant=60,candidate_limit=100)
        hash_fusion_ms=(time.perf_counter()-started)*1000
        dense_rank={x['chunk_id']:j+1 for j,x in enumerate(dense)}
        bm25_rank={x['chunk_id']:j+1 for j,x in enumerate(lexical)}
        started=time.perf_counter(); query_vector=embedder.encode([item['expanded']],batch_size=1,max_length=256,return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs'][0]
        query_vector=np.asarray(query_vector,dtype=np.float32); query_vector/=max(float(np.linalg.norm(query_vector)),1e-12); scores=vectors @ query_vector
        dense_order=np.argsort(-scores,kind='stable')[:50]
        bge_dense=[{**chunk_rows[j],'score':float(scores[j])} for j in dense_order]
        bge_ms=(time.perf_counter()-started)*1000
        started=time.perf_counter(); bge_fused=reciprocal_rank_fusion({'dense':bge_dense,'bm25':lexical},rank_constant=60,candidate_limit=100)
        bge_fusion_ms=(time.perf_counter()-started)*1000
        rerank_candidates=bge_fused[:50]
        pairs=[(item['expanded'],str(chunk_by_id[h['chunk_id']].get('text') or '')) for h in rerank_candidates]
        started=time.perf_counter(); rerank_scores=reranker.compute_score(pairs,batch_size=4,max_length=512,normalize=False)
        rerank_ms=(time.perf_counter()-started)*1000
        if np.isscalar(rerank_scores): rerank_scores=[rerank_scores]
        reranked=[dict(hit,reranker_score=float(score)) for hit,score in zip(rerank_candidates,rerank_scores)]
        reranked.sort(key=lambda row:(-row['reranker_score'],row['chunk_id']))
        hash_fused_rank={h['chunk_id']:j+1 for j,h in enumerate(hash_fused)}
        bge_dense_rank={h['chunk_id']:j+1 for j,h in enumerate(bge_dense)}
        bge_fused_rank={h['chunk_id']:j+1 for j,h in enumerate(bge_fused)}
        reranked_rank={h['chunk_id']:j+1 for j,h in enumerate(reranked)}
        mappings=[('hash_dense',dense),('bm25',lexical),('hash_bm25_rrf',hash_fused),
                  ('bge_m3_dense',bge_dense),('bge_m3_bm25_rrf',bge_fused),('bge_m3_bm25_reranked',reranked)]
        for method,results in mappings:
            limit=50 if method in {'hash_dense','bm25','bge_m3_dense','bge_m3_bm25_reranked'} else 100
            for rank,hit in enumerate(results[:limit],1):
                cid=hit['chunk_id']; row=chunk_by_id[cid]
                raw_by_method[method].append({'case_id':item['case']['case_id'],'concept_id':item['case']['concept_id'],
                    'query':item['query'],'expanded_query':item['expanded'],'expected_no_evidence':int(item['case'].get('category')=='insufficient_evidence'),
                    'method':method,'rank':rank,'chunk_id':cid,'document_id':row['document_id'],'resource_id':row['resource_id'],
                    'page':row['page'],'printed_page':row['printed_page'],'chapter':row['chapter'],'section':row['section'],
                    'source':row.get('title') or row.get('source_url') or '', 'excerpt':str(row.get('text') or '')[:500],
                    'score':hit.get('reranker_score',hit.get('rrf_score',hit.get('score',''))),
                    'hash_dense_rank':dense_rank.get(cid,''),'bm25_rank':bm25_rank.get(cid,''),
                    'hash_rrf_rank':hash_fused_rank.get(cid,''),'bge_dense_rank':bge_dense_rank.get(cid,''),
                    'bge_rrf_rank':bge_fused_rank.get(cid,''),'bge_reranker_rank':reranked_rank.get(cid,''),
                    'hash_dense_latency_ms':round(hash_ms,3),'bm25_latency_ms':round(bm25_ms,3),
                    'hash_rrf_merge_latency_ms':round(hash_fusion_ms,3),'bge_dense_latency_ms':round(bge_ms,3),
                    'bge_rrf_merge_latency_ms':round(bge_fusion_ms,3),'reranker_latency_ms':round(rerank_ms,3),
                    'index_config_id':config_id,'model_revision':revisions['BAAI/bge-m3']})
        timing.append({'case_id':item['case']['case_id'],'hash_dense_ms':round(hash_ms,3),'bm25_ms':round(bm25_ms,3),
            'hash_rrf_ms':round(hash_fusion_ms,3),'bge_dense_query_ms':round(bge_ms,3),
            'bge_rrf_merge_ms':round(bge_fusion_ms,3),'bge_reranker_50_ms':round(rerank_ms,3),
            'metrics_status':'pending_human_relevance_labels'})
    write_csv(args.private_output_dir/'course-rag-method-candidates.csv',sum(raw_by_method.values(),[]))
    write_csv(args.private_output_dir/'course-rag-query-latency.csv',timing)
    store.close(); con.close()
    del reranker, embedder
    summary={'schema_version':'deepprof-course-rag-offline-eval-v1','source_database_sha256':config['source_database_sha256'],
        'model_revisions':revisions,'embedding_dimension':int(vectors.shape[1]),'index_config_id':config_id,
        'device':torch.cuda.get_device_name(0) if device=='cuda' else 'CPU (offline fallback)',
        'chunking':{'version':chunking_version(800,120),'chunk_size':800,'chunk_overlap':120},
        'fixed_queries':len(queries),'frozen_chunk_count':len(chunk_rows),'per_stage_recall_k':50,
        'versioned_index_path':str(versioned_index_dir.resolve()),
        'rrf_constant':60,'rrf_candidate_limit':100,'rerank_candidates':50,'return_top_k':5,
        'reranker_precision':'float32','reranker_batch_size':4,
        'retrieval_pool_note':'candidate rankings retained through top 50/100 for human relevance review; not corpus-level judged recall yet',
        'quality_metrics':{'Recall@20':'N/A; human relevance labels pending','nDCG@5':'N/A; human relevance labels pending',
            'no_evidence_false_accept_rate':'N/A; human-confirmed answerability and gate threshold pending'},
        'latency_ms_summary':{key:{'median':float(np.median([x[key] for x in timing])),'p95':float(np.percentile([x[key] for x in timing],95))}
            for key in ['hash_dense_ms','bm25_ms','hash_rrf_ms','bge_dense_query_ms','bge_rrf_merge_ms','bge_reranker_50_ms']},
        'index_build_seconds':index_elapsed,'index_vectors_sha256':config['vector_file_sha256'],
        'private_candidate_file_sha256':sha(args.private_output_dir/'course-rag-method-candidates.csv'),
        'status':'offline_pretrained_model_ranking_complete; semantic quality awaits human annotations'}
    args.summary_output.parent.mkdir(parents=True,exist_ok=True)
    args.summary_output.write_text(json.dumps(summary,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(summary,ensure_ascii=False,indent=2))
    return 0

if __name__=='__main__': raise SystemExit(main())
