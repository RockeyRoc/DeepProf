"""Apply explicitly source-read Codex AI spot reviews; preserve provider-only labels."""
from __future__ import annotations
import argparse
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_m3_fulltext_rag import RAW_RESEARCH,RESEARCH,ACTIVE_BATCH,read_csv
from scripts.annotate_fulltext_rag import write_csv
from scripts.ocr_full_textbook import write_json,digest

VERSION='fulltext-ai-review-20261004-v4-retrieval-spotcheck-v1'
# Each entry was read with its complete question and supplied source, not inferred
# from lexical similarity. These are AI reviews, never personnel reviews.
REVIEWS={
 'R-2A8F55941A3E':('2','邻接表 (Adjacency List)','片段先述二维arcs的相邻点访问，再定义每顶点单链表和三个边结点域，直接提供辨别两种结构的依据。'),
 'R-3B1265361C1A':('2','将剩余的 SR[i.. m]','合并循环取出较小记录后，片段明确列出复制两侧剩余记录的两个分支，覆盖不漏元素的关键条件；评价算法文字，未认证OCR代码可编译。'),
 'R-B74503B8C7B7':('1','带权的邻接矩阵','片段覆盖选最小D值及松弛步骤，却未给出问题要求先说明的非负边权适用条件，不能记为直接覆盖全部关键需求。'),
 'R-EC94FF7F2FD9':('1','移动元素的个数','片段解释插入时的移动成本随位置变化，但没有给出插入算法步骤，仅部分支持。'),
 'R-B6ECE0CCFB5A':('0','树 (Tree)','问题要求邻接表基础检查，片段是树与子树定义，不能据此判断邻接表。'),
 'R-DDCA3E1685A2':('0','中序序列','片段讨论二叉树形态和中序序列，未涉及循环队列的空满状态。'),
 'R-3FC24FD7FBAE':('0','倒排文件','片段讨论多重表及倒排文件，未提供所问优先队列函数签名。'),
 'R-C51594CB3F49':('0','HeapSort','排序接口目录虽含顺序表字样，但未提供中间插入时已有元素如何移动的依据。'),
 'R-6109F1A5AB64':('1','归并排序和计数排序','片段给出排序类别、复杂度分类及比较/移动基本操作，相关但未明确归并排序的逐层总工作量，属于部分支持。'),
 'R-26CA153D1C1F':('2','归并排序 O(nlogn)','排序性能表直接列出归并排序平均O(nlogn)，足以否定问题中平均O(n)的结论；不把否定陈述误判为不相关。'),
 'R-577BB7B7A621':('1','用邻接表表示图比邻接矩阵节省存储空间','片段含邻接表各域及与邻接矩阵的稀疏存储比较，支持区分；未同时完整说明矩阵结构，故记为部分支持。'),
 'R-EEF1676FEEF2':('1','当前分配的存储容量','片段说明顺序表的基址、长度、容量和插入导致容量需求，提供必要背景，但未说明中间插入的移动方向。'),
 'R-8684A3828B96':('0','最佳归并树','片段讨论外排序虚段及最佳归并树，未说明内部合并时如何处理两侧剩余元素。'),
 'R-5E6433735C17':('0','删除非空循环队列','目录只列DeQueue接口，未解释为什么front=rear可能同时表示空满。'),
 'R-751940AD3CAC':('1','地址连续的存储单元','片段给出顺序表示和存储位置关系，是插入的背景条件；前段为另一个合并操作，不能当作完整顺序表插入步骤。'),
 'R-285975D361A0':('1','firstedge 域指示','片段解释邻接多重表顶点域及与邻接表的关系，仅间接支持所问普通邻接表信息，不能将两种结构视为完全等同。'),
 'R-033D568D3A27':('1','左孩子或右孩子结点','二叉排序树插入与左右子树代码提供相关示例，但未直接给出任意二叉树每结点至多两个孩子的定义；部分支持。'),
}

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--apply',action='store_true');a=p.parse_args()
 b=RAW_RESEARCH/ACTIVE_BATCH;f=b/'ai-annotation';source=f/'retrieval-ai-provider-only.csv'
 original=source if source.exists() else f/('retrieval-ai.csv' if a.apply else 'retrieval-ai.partial.csv')
 rows=read_csv(original);lookup={r['item_id']:r for r in rows};records={}
 for ident,(grade,anchor,reason) in REVIEWS.items():
  r=lookup[ident];lines=r['source_excerpt'].splitlines();n=next(i for i,x in enumerate(lines,1) if anchor in x)
  receipt={'item_id':ident,'query':r['query'],'source_excerpt_sha256':hashlib.sha256(r['source_excerpt'].encode()).hexdigest(),
    'source_document_id':r['document_id'],'source_chunk_id':r['chunk_id'],'pdf_page':r['page'],'line_number':n,'quote':lines[n-1],
    'original_grade':r['relevance_grade'],'relevance_grade':grade,'rationale':reason,
    'original_provider_request_sha256':r['annotation_request_sha256'],'annotation_source':'ai','annotation_model':'Codex',
    'annotation_version':VERSION,'annotation_ocr_sha256':r['annotation_ocr_sha256'],'human_review_status':'not_reviewed'}
  receipt['annotation_request_sha256']=hashlib.sha256(json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()).hexdigest()
  records[ident]=receipt
 if a.apply:
  if not source.exists():source.write_bytes((f/'retrieval-ai.csv').read_bytes())
  for r in rows:
   if r['item_id'] not in records:continue
   q=records[r['item_id']]
   r.update(relevance_grade=q['relevance_grade'],relevance_grade_0_1_2=q['relevance_grade'],
    rationale=q['rationale']+'；核对原文：'+q['quote'],
    evidence_locator=f"{r['document_id']} / {r['chunk_id']} / PDF {r['page']} / line {q['line_number']}",
    annotation_model='Codex',annotation_version=VERSION,annotation_request_kind='local_codex_review_receipt',
    annotation_request_sha256=q['annotation_request_sha256'])
  write_csv(f/'retrieval-ai.csv',rows)
 changed=[k for k,r in records.items() if r['original_grade']!=r['relevance_grade']]
 result={'version':VERSION,'api_requests':0,'sampling_scope':'17 explicitly read historical rows: positive and partial judgments plus source-read negative contrasts; not a random error-rate estimate',
         'reviewed_rows':len(records),'changed_rows':len(changed),'changed_ids':changed,'records':records,
         'unreviewed_provider_labels_retained':True,'human_review_status':'not_reviewed'}
 write_json(f/'retrieval-codex-spotcheck.json',result)
 write_json(RESEARCH/ACTIVE_BATCH/'retrieval-spotcheck-validation.json',{k:v for k,v in result.items() if k!='records'})
 print({'reviewed':len(records),'changed':len(changed),'applied':a.apply})

if __name__=='__main__':main()
