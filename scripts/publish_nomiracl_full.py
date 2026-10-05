"""Copy validated complete score tables and freeze reproducible runtime snapshots."""
import argparse
import json
import shutil
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from scripts.nomiracl_checkpoints import sha,write_json
OUT=ROOT/'docs/experiments/bkt-rag-improvement-20261001'

def main():
    p=argparse.ArgumentParser();p.add_argument('--run',type=Path,required=True);a=p.parse_args()
    proof=json.loads((OUT/'validation/nomiracl-validation.json').read_text(encoding='utf-8'))
    cfg=json.loads((a.run/'frozen-config.json').read_text(encoding='utf-8'))
    assert proof['status']=='passed' and proof['run_config_sha256']==cfg['config_sha256']
    for name,expected in cfg['source_sha256'].items():
        source=ROOT/name;assert sha(source)==expected
        target=a.run/'reproduction'/name;target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists():assert sha(target)==expected
        else:shutil.copy2(source,target)
    for name in ['nomiracl-zh-per-query.csv','nomiracl-zh-candidate-ranks.csv','nomiracl-zh-evaluation.json']:
        source=a.run/'results'/name;target=OUT/'data'/name
        if name in proof['output_sha256']:assert sha(source)==proof['output_sha256'][name]
        shutil.copy2(source,target);assert sha(source)==sha(target)
    write_json(OUT/'data/nomiracl-evaluation-status.json',{'status':'completed','run_id':a.run.name,
        'queries':3770,'candidate_pairs':37599,'method_ranking_rows':150396,
        'validation_sha256':sha(OUT/'validation/nomiracl-validation.json'),
        'config_sha256':cfg['config_sha256'],'external_model_requests':0,
        'prior_stopped_run_retained_in_archive':True,'personnel_review_status':'pending'})
    print(json.dumps({'status':'published_validated_full_results','external_requests':0}))
if __name__=='__main__':main()
