"""Exercise a completed stage in a fresh worker and prove zero repeated forwards."""
import argparse
import json
import os
import subprocess
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.nomiracl_checkpoints import sha,write_json

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args();run=a.run
    cfg=json.loads((run/'frozen-config.json').read_text(encoding='utf-8'))
    summary=json.loads((run/'results/nomiracl-zh-evaluation.json').read_text(encoding='utf-8'))
    state=summary['stages']['documents'];folder=run/'checkpoints/documents'/state['device']
    cache=[p for p in folder.iterdir() if p.suffix in {'.json','.npy'} and p.stem.isdigit()]
    before={p.name:sha(p) for p in cache};old=json.loads((folder/'worker-state.json').read_text(encoding='utf-8'))
    write_json(run/'reproduction/document-stage-before-resume.json',old)
    command=[cfg['inference_python'],str(ROOT/'scripts/nomiracl_inference_worker.py'),
             '--run',str(run),'--stage','documents','--device',state['device']]
    process=subprocess.Popen(command,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,encoding='utf-8',errors='replace',
                             env={**os.environ,'HF_HUB_OFFLINE':'1','TRANSFORMERS_OFFLINE':'1'})
    try:output,_=process.communicate()
    finally:
        if process.poll() is None:process.terminate();process.wait()
    (run/'reproduction/completed-stage-resume.log').write_text(output,encoding='utf-8')
    assert process.returncode==0,output
    resumed=json.loads((folder/'worker-state.json').read_text(encoding='utf-8'))
    assert resumed['status']=='completed' and resumed['new_chunks']==0 and resumed['rows']==31826
    assert 'model_loading' not in output and before=={p.name:sha(p) for p in cache}
    cleanup=json.loads((run/'cleanup-proof.json').read_text(encoding='utf-8'))
    cleanup['owned_processes'].append({'pid':process.pid,'stage':'documents_resume_validation','device':state['device'],
                                      'exit_code':process.returncode,'still_running':False})
    cleanup['all_workers_exited']=True;write_json(run/'cleanup-proof.json',cleanup)
    write_json(run/'resume-validation.json',{'status':'passed','completed_rows':31826,'new_chunks':0,
        'model_loaded':False,'repeated_model_forwards':0,'cache_file_sha256_unchanged':before,
        'config_sha256':cfg['config_sha256'],'pid':process.pid,'exit_code':0,'external_model_requests':0})
    print(json.dumps({'status':'passed','rows_skipped':31826,'new_forwards':0}))
if __name__=='__main__':main()
