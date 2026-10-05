"""Independently versioned evaluator repair; never changes frozen generation code.

The predecessor HTTP response is retained and charged. Requests have zero retries.
Instructions, payloads, replies, and repair receipts remain private.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from scripts import annotate_fulltext_rag as ai
from scripts.run_m3_fulltext_rag import RAW_RESEARCH, RESEARCH, ACTIVE_BATCH, read_csv, budget_state
from scripts.ocr_full_textbook import digest, write_json

VERSION = 'fulltext-ai-review-20261004-v4-evaluator2'
SIZE = 528
RULE = ('你是教材证据相关性评审员。输入的 question_id、text_id、pdf_page 仅是定位字段，绝不是标签。'
        '对每个 judgments 条目依其 question_id 找到问题，依 text_id 找到教材片段，逐条判断。'
        'grade 只能为 0、1、2 或 ?：0=不相关或仅词语/主题相似；1=仅覆盖必要条件的一部分；'
        '2=片段直接覆盖所问关键证据需求；?=片段无法解读。不得用外部知识补足片段没有的条件。'
        'reason_code 只能为 D/A/P/B/T/U/M/O，含义如下：'
        + json.dumps(ai.REASONS, ensure_ascii=False) + '。'
        'line_number 必须是该 text 内的原文行号，从1开始，绝不能填写 PDF 页码。无对应原文可填0。'
        'grade=2 必须用 D 或 A 且 line_number>0。'
        '严格输出四列CSV：i,grade,reason_code,line_number。i必须完整覆盖judgments中的每个i，恰好一次。'
        '例如 0,0,U,0 或 1,2,D,3。只输出CSV数据行，无标题、Markdown或额外说明。')


def freeze(batch: Path, rows: list[dict]) -> dict:
    private = batch / 'reproduction/evaluator2'
    spec = {'version': VERSION, 'batch_id': batch.name, 'retrieval_batch_size': SIZE,
            'rule_sha256': hashlib.sha256(RULE.encode()).hexdigest(),
            'evaluator_source_sha256': digest(Path(__file__)),
            'input_sha256': {name: digest(batch / 'ai-annotation' / name) for name in
                            ['retrieval-historical-input.csv', 'retrieval-fulltext-input.csv']},
            'frozen_generation_config_sha256': json.loads((batch/'frozen-config.json').read_text(encoding='utf-8'))['config_sha256'],
            'model': 'deepseek-flash', 'thinking': {'type': 'disabled'}, 'max_tokens': 8192,
            'temperature': 0, 'automatic_retries': 0, 'planned_retrieval_requests': 26,
            'predecessor_requests_retained_and_charged': 1,
            'semantic_invalid_predecessor_is_excluded': True,
            'human_review_status': 'not_reviewed', 'candidate_rows': len(rows)}
    path = private / 'frozen-evaluator.json'
    if path.exists():
        if json.loads(path.read_text(encoding='utf-8')) != spec:
            raise RuntimeError('independent_evaluator_fingerprint_changed')
    else:
        write_json(path, spec)
        (private/'source.py').write_text(Path(__file__).read_text(encoding='utf-8'), encoding='utf-8')
        write_json(private/'protocol.json', {'rule': RULE, 'version': VERSION})
    return spec


def main():
    p=argparse.ArgumentParser(description=__doc__); p.add_argument('--batch-id', default=ACTIVE_BATCH)
    a=p.parse_args(); batch=RAW_RESEARCH/a.batch_id; folder=batch/'ai-annotation'
    rows=read_csv(folder/'retrieval-historical-input.csv')+read_csv(folder/'retrieval-fulltext-input.csv')
    freeze(batch, rows)
    groups={}
    for row in rows:
        key=hashlib.sha256((row['query']+'\0'+row['source_excerpt']).encode()).hexdigest()
        groups.setdefault(key,[]).append(row)
    unique=list(groups.items()); judged=[]; exceptions=[]
    frozen=json.loads((batch/'frozen-config.json').read_text(encoding='utf-8'))
    ai.VERSION=VERSION  # Request provenance only; frozen files on disk are unchanged.
    for start in range(0,len(unique),SIZE):
        block=unique[start:start+SIZE]; texts={}; questions={}; pairs=[]
        for i,(_,mapped) in enumerate(block):
            r=mapped[0]; q=questions.setdefault(r['query'],len(questions)); t=texts.setdefault(r['source_excerpt'],len(texts))
            pairs.append({'i':i,'question_id':str(q),'text_id':str(t),'pdf_page':r['page']})
        data={'questions':{str(v):k for k,v in questions.items()},
              'texts':{str(v):'\n'.join(f'{i}: {s}' for i,s in enumerate(k.splitlines(),1)) for k,v in texts.items()},
              'judgments':pairs}
        messages=[{'role':'system','content':RULE},
                  {'role':'user','content':json.dumps(data,ensure_ascii=False)+'\n\n最终输出要求：'+RULE}]
        answer,request=ai.model_request(batch,f'retrieval-v2-{start:05d}',messages)
        verdicts={}; invalid={}; extra=[]
        for line in answer.splitlines():
            if not line.strip() or line.strip().startswith('```'):continue
            m=re.fullmatch(r'\s*(\d+)\s*,\s*([012?])\s*,\s*([DAPBTUMO])\s*,\s*(\d+)\s*',line)
            if not m:
                ident=re.match(r'\s*(\d+)\s*,',line)
                if ident and int(ident[1])<len(block):invalid[int(ident[1])]='返回行不符合冻结标签格式：'+line[:120]
                else:extra.append(line[:120])
                continue
            i,g,c,n=m.groups(); i,n=int(i),int(n)
            if i>=len(block):extra.append(line[:120]);continue
            if i in verdicts:invalid[i]='同一条目被重复输出，无法唯一确定标签';continue
            lines=block[i][1][0]['source_excerpt'].splitlines()
            if n>len(lines):invalid[i]=f'模型给出的证据行号 {n} 超出片段 {len(lines)} 行，来源验证失败'
            elif g=='2' and (c not in {'D','A'} or not n):invalid[i]='完整相关标签没有直接证据代码和有效原文行'
            elif g=='1' and c not in {'P','B','A','D'}:invalid[i]='部分支持标签与原因代码不一致'
            verdicts[i]=(g,c,n,lines[n-1] if 0<n<=len(lines) else '')
        for i in range(len(block)):
            if i not in verdicts and i not in invalid:invalid[i]='模型响应遗漏该条目，没有合法语义判断'
        for i,(key,mapped) in enumerate(block):
            error=invalid.get(i,''); g,c,n,quote=verdicts.get(i,('?','O',0,''))
            if error:g,c,n,quote='?','O',0,''
            for r in mapped:
                reason=error+'；原始响应已保留，未补造相关性。' if error else ai.REASONS[c]+'；判别原文：'+(quote or '该片段没有对应依据')
                judged.append({**r,'relevance_grade_0_1_2':'无法判定' if g=='?' else g,
                    'relevance_grade':'无法判定' if g=='?' else g,'relevance_status':'reviewed',
                    'rationale':reason+'；问题：'+r['query'],
                    'evidence_locator':f"{r['document_id']} / {r['chunk_id']} / PDF {r['page']} / line {n}",
                    'annotation_source':'ai','annotation_version':VERSION,'annotation_model':'deepseek-flash',
                    'annotation_request_sha256':request['body_sha256'],'deduplicated_judgment_id':key,
                    'annotation_request_kind':'provider_http_body','annotation_ocr_sha256':frozen['ocr_sha256'],
                    'human_review_status':'not_reviewed','annotation_protocol_exception':error})
            if error:exceptions.append({'judgment_id':key,'request_sha256':request['body_sha256'],'i':i,'reason':error})
        ai.write_csv(folder/'retrieval-ai.partial.csv',judged)
        write_json(folder/'retrieval-protocol-exceptions.json',{'version':VERSION,'exceptions':exceptions,'extra_lines':extra})
        print(f'Retrieval v2 reviewed {min(start+SIZE,len(unique))}/{len(unique)}, protocol exceptions={len(invalid)}',flush=True)
        if len(invalid)>max(5,len(block)//100) or extra:
            raise RuntimeError('evaluator_protocol_quality_gate_failed; saved response; no automatic retry')
    ai.write_csv(folder/'retrieval-ai.csv',judged)
    result={'version':VERSION,'rows':len(judged),'unique_judgments':len(unique),
            'labels':dict(Counter(r['relevance_grade'] for r in judged)),
            'protocol_exceptions':len(exceptions),'predecessor_invalid_requests':1,
            'budget':budget_state(batch)}
    write_json(RESEARCH/batch.name/'retrieval-annotation-validation.json',result)
    print(json.dumps(result,ensure_ascii=False,indent=2))

if __name__=='__main__':main()
