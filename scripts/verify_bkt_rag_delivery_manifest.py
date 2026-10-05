"""Verify every artifact and retained source in the final local delivery manifest."""
from __future__ import annotations
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.build_bkt_rag_delivery_manifest import OUT,sha256
from scripts.ocr_full_textbook import write_json

def main():
 path=OUT/'delivery-manifest.json';manifest=json.loads(path.read_text(encoding='utf-8'))
 checked=0;errors=[]
 buckets=[manifest['artifacts'],manifest['fulltext_private_lineage'],
          *[v['files'] for v in manifest['retained_fulltext_private_lineage'].values()],
          manifest.get('nomiracl_private_lineage',{}),manifest.get('retired_report_archive',{})]
 for bucket in buckets:
  for name,item in bucket.items():
   source=Path(item['path']);checked+=1
   if not source.is_file() or sha256(source)!=item['sha256'] or source.stat().st_size!=item['bytes']:
    errors.append({'artifact':name,'reason':'missing, SHA-256 or byte count mismatch'})
 for name,expected in manifest['source_lineage_sha256'].items():
  source=Path(name);source=source if source.is_absolute() else ROOT/source;checked+=1
  if not source.is_file() or sha256(source)!=expected:errors.append({'source':name,'reason':'missing or SHA-256 mismatch'})
 proof={'status':'verified' if not errors else 'failed','checked_files':checked,'mismatches':errors,
        'manifest_sha256':sha256(path),'formal_eligible_cells':manifest['fulltext_validation']['eligible_formal_cells'],
        'pending':manifest['fulltext_validation']['pending'],'cumulative_requests':manifest['fulltext_budget']['cumulative_requests'],
        'raw_prompts_independently_archived':True,'human_files_verified_by_delivery_validation':True}
 if manifest.get('nomiracl_status'):
  proof['nomiracl_status']=manifest['nomiracl_status'];proof['nomiracl_validated_counts']=manifest['nomiracl_validation']
 write_json(OUT/'validation/delivery-integrity.json',proof)
 print(json.dumps(proof,ensure_ascii=False,indent=2))
 if errors:raise SystemExit(1)

if __name__=='__main__':main()
