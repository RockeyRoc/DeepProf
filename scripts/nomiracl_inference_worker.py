"""One isolated FP32 inference stage; checkpoint every 256 ordered inputs."""
from __future__ import annotations
import argparse
import json
import os
from pathlib import Path
import sys
import time
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.nomiracl_checkpoints import freeze,read_chunk,sha,validate_array,write_chunk,write_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True)
    p.add_argument('--stage',choices=['documents','queries','reranker'],required=True)
    p.add_argument('--device',choices=['cuda','cpu'],required=True);a=p.parse_args()
    run=a.run;cfg=json.loads((run/'frozen-config.json').read_text(encoding='utf-8'))
    folder=run/'checkpoints'/a.stage/a.device;folder.mkdir(parents=True,exist_ok=True)
    state=folder/'worker-state.json';started=time.perf_counter()
    write_json(state,{'status':'starting','pid':os.getpid(),'stage':a.stage,'device':a.device})
    try:
        for name,expected in cfg['source_sha256'].items():
            if sha(ROOT/name)!=expected:raise ValueError('runtime_source_fingerprint_mismatch:'+name)
        if sha(run/'prepared-input.json')!=cfg['prepared_input_sha256']:raise ValueError('prepared_input_changed')
        os.environ['HF_HUB_OFFLINE']='1';os.environ['TRANSFORMERS_OFFLINE']='1'
        import numpy as np
        import torch
        from FlagEmbedding import BGEM3FlagModel,FlagReranker
        torch.set_num_threads(16);torch.set_num_interop_threads(1)
        torch.backends.cuda.matmul.allow_tf32=False;torch.backends.cudnn.allow_tf32=False
        if a.device=='cuda' and not torch.cuda.is_available():raise RuntimeError('CUDA not available')
        device_name=torch.cuda.get_device_name(0) if a.device=='cuda' else 'CPU FP32 / 16 threads'
        spec={'run_config_sha256':cfg['config_sha256'],'stage':a.stage,'device':a.device,'device_name':device_name,
              'torch':torch.__version__,'cuda_runtime':torch.version.cuda,'dtype':'float32','tf32':False}
        stage=freeze(folder/'stage-config.json',spec,resume=True);stage_hash=stage['config_sha256']
        data=json.loads((run/'prepared-input.json').read_text(encoding='utf-8'))
        docs={r['docid']:r['title']+' '+r['text'] for r in data['documents']}
        qids=sorted(data['queries']);queries=data['queries']
        if a.stage=='documents':keys=sorted(docs);texts=[docs[k] for k in keys];dimension=1024
        elif a.stage=='queries':keys=qids;texts=[queries[k]['query'] for k in keys];dimension=1024
        else:
            keys=[[qid,doc] for qid in qids for doc in queries[qid]['docids']]
            texts=[(queries[qid]['query'],docs[doc]) for qid,doc in keys];dimension=0
        # Examine all checkpoints before model loading; valid completed stages make no new forwards.
        pending=[s for s in range(0,len(keys),256)
                 if read_chunk(folder,s,keys[s:s+256],stage_hash,dimension) is None]
        write_json(state,{'status':'running','pid':os.getpid(),'total_rows':len(keys),'pending_chunks':len(pending),
                          'stage':a.stage,'device':a.device})
        model=None
        if pending:
            print(json.dumps({'stage':a.stage,'device':a.device,'event':'model_loading','pending_chunks':len(pending)}),flush=True)
            model_path=cfg['model_paths']['BAAI/bge-reranker-v2-m3' if a.stage=='reranker' else 'BAAI/bge-m3']
            model=FlagReranker(model_path,use_fp16=False,devices=a.device) if a.stage=='reranker' else BGEM3FlagModel(model_path,use_fp16=False,devices=a.device)
            if any(param.dtype!=torch.float32 for param in model.model.parameters()):raise ValueError('model_not_fp32')
        smoke_passed=False
        for s in pending:
            batch=texts[s:s+256];t=time.perf_counter()
            if not smoke_passed:
                sample=batch[:4]
                if a.stage=='reranker':v=model.compute_score(sample,batch_size=cfg['reranker_batch_size'],max_length=512,normalize=False)
                else:v=model.encode(sample,batch_size=cfg['embedding_batch_size'],max_length=256 if a.stage=='queries' else 512,
                                    return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs']
                validate_array(np.asarray(v).reshape(-1) if not dimension else v,len(sample),dimension)
                smoke_passed=True
                write_json(folder/'smoke-proof.json',{'rows':len(sample),'finite':True,'fp32_parameters_verified':True,
                                                      'stage_config_sha256':stage_hash,'included_in_final_results':False})
            if a.stage=='reranker':
                values=model.compute_score(batch,batch_size=cfg['reranker_batch_size'],max_length=512,normalize=False)
                values=np.asarray(values,dtype=np.float32).reshape(-1)
            else:
                values=model.encode(batch,batch_size=cfg['embedding_batch_size'],max_length=256 if a.stage=='queries' else 512,
                                    return_dense=True,return_sparse=False,return_colbert_vecs=False)['dense_vecs']
            if a.device=='cuda':torch.cuda.synchronize()
            elapsed=time.perf_counter()-t
            write_chunk(folder,s,keys[s:s+256],values,stage_hash,dimension,elapsed)
            print(json.dumps({'stage':a.stage,'device':a.device,'completed_rows':min(s+256,len(keys)),
                              'total_rows':len(keys),'batch_seconds':round(elapsed,2)}),flush=True)
        write_json(state,{'status':'completed','pid':os.getpid(),'stage':a.stage,'device':a.device,
                          'rows':len(keys),'stage_sha256':stage_hash,'invocation_seconds':time.perf_counter()-started,
                          'new_chunks':len(pending)})
        return 0
    except BaseException as exc:
        message=str(exc)
        write_json(state,{'status':'failed','pid':os.getpid(),'stage':a.stage,'device':a.device,
                          'error_type':type(exc).__name__,'error':message,
                          'device_failure':a.device=='cuda' and any(t in message.lower() for t in ['cuda','cublas','cudnn']),
                          'invocation_seconds':time.perf_counter()-started})
        raise

if __name__=='__main__':raise SystemExit(main())
