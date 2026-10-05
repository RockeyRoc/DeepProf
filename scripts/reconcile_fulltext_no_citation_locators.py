"""Locate source-less claims in the answer without inventing a textbook locator."""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_m3_fulltext_rag import RAW_RESEARCH,ACTIVE_BATCH,read_csv
from scripts.annotate_fulltext_rag import write_csv
from scripts.ocr_full_textbook import write_json

def main():
 folder=RAW_RESEARCH/ACTIVE_BATCH/'ai-annotation';path=folder/'citation-ai.csv'
 rows=read_csv(path);original=folder/'citation-ai-before-locator-normalization.csv'
 if not original.exists():original.write_bytes(path.read_bytes())
 receipt_path=folder/'no-citation-location-receipts.json'
 receipts=json.loads(receipt_path.read_text(encoding='utf-8'))['records'] if receipt_path.exists() else {}
 for row in rows:
  if row.get('evidence_locator','').strip():continue
  assert row['record_type']=='claim_without_explicit_citation'
  assert row['individual_support_label']==row['joint_support_label']=='无支持'
  locator={'location_type':'answer_claim_without_cited_source','cell_id':row['cell_id'],
           'claim_id':row['claim_id'],'document_chunk_page':None,
           'reason':'回答中没有来源引用；仅定位回答主张，不臆造教材证据。'}
  receipt={'item_id':row['item_id'],'evidence_locator':locator,'claim_text':row['claim_text'],
           'individual_support_label':'无支持','joint_support_label':'无支持',
           'reason':row['individual_reason'],'annotation_source':'ai','annotation_model':'Codex',
           'annotation_version':'fulltext-ai-review-20261004-v4-no-citation-location-v1',
           'annotation_ocr_sha256':row['annotation_ocr_sha256'],'human_review_status':'not_reviewed'}
  signature=hashlib.sha256(json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  receipts[row['item_id']]={**receipt,'annotation_request_sha256':signature}
  row.update(evidence_locator=json.dumps(locator,ensure_ascii=False),
             annotation_version=receipt['annotation_version'],annotation_request_sha256=signature)
 write_csv(path,rows)
 write_json(receipt_path,{'api_requests':0,'source_locations_invented':0,'records':receipts})
 print({'source_less_claim_locators_normalized':len(receipts),'source_locations_invented':0})

if __name__=='__main__':main()
