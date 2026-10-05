"""Split valid provider evidence ranges into <=8-line, exact source witnesses.

No new semantic label is inferred: only the provider's original answerable verdict
is restored when every cited range was in bounds and the sole failure was length.
"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_m3_fulltext_rag import RAW_RESEARCH,RESEARCH,ACTIVE_BATCH,ANNOTATIONS,read_csv
from scripts.annotate_fulltext_rag import write_csv,parse_json
from scripts.ocr_full_textbook import write_json,digest

VERSION='fulltext-ai-review-20261004-v4-question-span-normalization-v1'

def main():
 b=RAW_RESEARCH/ACTIVE_BATCH;f=b/'ai-annotation';cfg=json.loads((b/'frozen-config.json').read_text(encoding='utf-8'))
 pages=json.loads(Path(cfg['ocr_path']).read_text(encoding='utf-8'))['pages']
 assert digest(Path(cfg['ocr_path']))==cfg['ocr_sha256']
 source=f/'questions-120-ai-before-span-normalization.csv';path=f/'questions-120-ai.csv'
 if not source.exists():source.write_bytes(path.read_bytes())
 rows=read_csv(source);by_case={r['case_id']:r for r in rows}
 cases=json.loads((b/'cases.json').read_text(encoding='utf-8'))['cases']
 input_rows=read_csv(ANNOTATIONS/'new-80-question-owner.csv')+[{'item_id':c['case_id']} for c in cases if c['split']=='historical']
 receipts={}
 for start in range(0,120,20):
  response=b/f'reproduction/annotation-responses/questions-{start:02d}.json'
  request=b/f'reproduction/annotation-requests/questions-{start:02d}.json'
  raw=json.loads(response.read_text(encoding='utf-8'));judgments=parse_json(raw['choices'][0]['message']['content'])
  request_sha=json.loads(request.read_text(encoding='utf-8'))['body_sha256']
  for verdict in judgments:
   row=by_case[input_rows[start+verdict['i']]['item_id']]
   if row['answerability']!='无法判定' or verdict['answerability']!='answerable':continue
   cited=verdict.get('evidence',[]);verified=[];valid=bool(cited);long=False
   for e in cited:
    n,a,z=int(e['pdf_page']),int(e['line_start']),int(e['line_end'])
    lines=pages[n-1]['corrected_text'].splitlines() if 1<=n<=347 else []
    if not 1<=a<=z<=len(lines):valid=False;break
    long=long or z-a>=8
    for offset in range(a,z+1,8):
     end=min(offset+7,z)
     verified.append({'pdf_page':n,'line_start':offset,'line_end':end,'quote':'\n'.join(lines[offset-1:end])})
   if not valid or not long:continue
   receipt={'case_id':row['case_id'],'original_answerability':row['answerability'],
     'provider_answerability':verdict['answerability'],'restored_answerability':'answerable',
     'original_provider_request_sha256':request_sha,'original_ranges':cited,'verified_split_ranges':verified,
     'annotation_source':'ai','human_review_status':'not_reviewed','annotation_version':VERSION,
     'semantic_verdict_changed_from_provider':False,'reason':'来源页码和范围均合法；仅超过每段8行约束。拆成至多8行的片段后逐字验证。'}
   signature=hashlib.sha256(json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
   receipts[row['case_id']]={**receipt,'normalization_receipt_sha256':signature}
   row.update(answerability='answerable',evidence_locator=json.dumps(verified,ensure_ascii=False),
     rationale=verdict['reason']+'；'+receipt['reason'],unverified_model_quotes='[]',
     annotation_version=VERSION,annotation_normalization_receipt_sha256=signature)
 write_csv(path,rows);write_csv(f/'new-80-ai.csv',[r for r in rows if r['split']!='historical'])
 write_json(f/'question-span-normalization.json',{'api_requests':0,'version':VERSION,'normalized_questions':len(receipts),'records':receipts})
 write_json(RESEARCH/ACTIVE_BATCH/'question-span-normalization-validation.json',{'api_requests':0,'normalized_questions':len(receipts),'new_semantic_labels_inferred':0,'source_out_of_range_not_restored':True})
 print({'normalized_questions':len(receipts),'api_requests':0})

if __name__=='__main__':main()
