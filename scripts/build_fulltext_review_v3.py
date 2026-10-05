"""Save the completed Codex visual review as an independent OCR revision.

The contact sheets and nine enlarged low-confidence pages were actually inspected.
This is a focused AI review, not a character-perfect or human certification.
"""
from __future__ import annotations
import copy
import json
from pathlib import Path
from scripts.ocr_full_textbook import digest, write_json, batches
from scripts.run_m3_fulltext_rag import SOURCE_HOME

VERSION = 'textbook-ai-focus-review-20261004-v3'
HOME = SOURCE_HOME / 'course/fulltext-ocr/bce3d6d54eaf'
UNRESOLVED = {
    79: '图3.9事件驱动模拟箭头及事件流不能可靠地由扁平OCR恢复。',
    101: '图5.1矩阵图中下标和行列对应关系未全部恢复；已单独校正可确认的C类型定义。',
    102: '图5.2两种存储图的箭头和地址对应关系未全部恢复；已校正可确认的地址公式。',
    147: '图6.14至6.16孩子链表、双亲域和树转换的二维连接不能由文本保证。',
    214: '图8.8伙伴系统空闲表的指针连接和分配前后对应关系不能由扁平OCR保证。',
    220: '图8.11指针逆转遍历广义表的多阶段箭头不能由扁平OCR保证。',
    313: '图11.6败者树各阶段的节点及工作区记录对应不能由扁平OCR保证。',
}
CONFIRMED_FOOTERS = {253:243,324:314,333:323,334:324,337:327,339:329,340:330,341:331}

FORMULA_163 = '''B(z) = b_0 + b_1 z + b_2 z^2 + ... + b_n z^n + ...
= sum_{k=0}^{infinity} b_k z^k. (6-8)
因为
B(z)^2 = b_0 b_0 + (b_0 b_1 + b_1 b_0)z + (b_0 b_2 + b_1 b_1 + b_2 b_0)z^2 + ...
= sum_{p=0}^{infinity}(sum_{i=0}^{p} b_i b_{p-i})z^p.
根据(6-7)
B(z)^2 = sum_{p=0}^{infinity} b_{p+1} z^p. (6-9)
由此得 z B(z)^2 = B(z) - 1，即 z B(z)^2 - B(z) + 1 = 0。
解此二次方程得 B(z) = (1 ± sqrt(1-4z))/(2z)。
由初值 b_0=1，应有 lim_{z->0} B(z)=b_0=1。
所以 B(z) = (1-sqrt(1-4z))/(2z)。
利用二项式展开
(1-4z)^(1/2) = sum_{k=0}^{infinity} binom(1/2,k)(-4z)^k. (6-10)
当 k=0 时，式(6-10)的第一项为1，故有
B(z) = (1/2) sum_{k=1}^{infinity} binom(1/2,k)(-1)^(k-1) 2^(2k) z^(k-1)
= sum_{m=0}^{infinity} binom(1/2,m+1)(-1)^m 2^(2m+1) z^m
= 1 + z + 2z^2 + 5z^3 + 14z^4 + 42z^5 + ... . (6-11)
对照(6-8)和(6-11)而得
b_n = binom(1/2,n+1)(-1)^n 2^(2n+1)
= [(1/2)(1/2-1)(1/2-2)...(1/2-n)/(n+1)!](-1)^n 2^(2n+1)。
印刷页153。
'''

def build() -> dict:
    old = HOME / 'textbook-full-ocr.json'
    parent_hash = digest(old)
    original = json.loads(old.read_text(encoding='utf-8'))
    data = copy.deepcopy(original)
    out = HOME / 'review-v3'
    screening = json.loads((out / 'screening.json').read_text(encoding='utf-8'))
    receipts = []
    for page in data['pages']:
        n = page['pdf_page']
        image = HOME / page['image_path']
        if digest(image) != page['image_sha256']:
            raise ValueError(f'image_lineage_changed:{n}')
        start = ((n-1)//8)*8+1
        sheet = out / 'contacts' / f'{start:03d}-{min(start+7,347):03d}.jpg'
        focus = '公式、代码、表格和印刷页码的重点目视核对；未认证全部字符或全部图连接。'
        receipt = {'pdf_page':n, 'annotation_source':'ai', 'annotation_model':'Codex',
            'annotation_version':VERSION, 'image_path':str(image), 'image_sha256':digest(image),
            'contact_sheet_sha256':digest(sheet), 'image_locator':[0,0,*page['image_size']],
            'scope':focus, 'enlarged_page_inspected':n in {79,101,102,147,163,200,214,220,313},
            'human_review_status':'not_reviewed', 'character_perfect_certification':False,
            'unresolved_reason':UNRESOLVED.get(n,'')}
        page['supplemental_review'] = receipt
        page['annotation_version'] = VERSION
        page['review_source'] = 'ai'
        page['human_review_status'] = 'not_reviewed'
        if n in CONFIRMED_FOOTERS:
            page['printed_page'] = CONFIRMED_FOOTERS[n]
            page['printed_page_verification'] = {'source':'ai_visual_footer',
                'image_sha256':digest(image),'confirmed_value':CONFIRMED_FOOTERS[n],
                'uniform_offset_used':False,'human_review_status':'not_reviewed'}
        page['image_path'] = str(image)
        before = page['corrected_text']
        after = before
        reason = ''
        if n == 101:
            after = after.replace('typedef ElemType Arrayl[n];','typedef ElemType Array1[n];').replace('typedef ArraylArray2[m];','typedef Array1 Array2[m];')
            reason = '逐页图像确认Array1中的数字1与Array2前的空格，保留数组图未确认状态。'
        elif n == 102:
            after = after.replace('LOC(i,j)=LOC(0,0)+(b2Xi+j)L','LOC(i,j) = LOC(0,0) + (b_2 * i + j) * L')
            reason = '图像确认行优先二维数组地址公式中的第二维长度、乘号及括号。'
        elif n == 163:
            after = FORMULA_163
            reason = '放大原始页逐项核对生成函数、求和上下界、二项式系数、指数和初值；将数学排版转为无歧义纯文本。'
            page['index_eligible'] = True
        elif n == 200:
            # Correct the verified exponents; the trace table remains explicitly unresolved.
            marker = after.index('7.6.2')
            after = after[:marker] + after[marker:].replace('O(n²）','O(n^3)').replace('O(n²）,','O(n^3),')
            reason = '放大图像确认每对顶点最短路径的重复Dijkstra和Floyd均为O(n^3)，不是OCR中的平方。'
            receipt['unresolved_reason'] = 'Dijkstra过程表的空白单元格、顶点下标及路径对应仍未完全恢复；页内Floyd复杂度已校正。'
        if after != before:
            page['corrected_text'] = after
            page.setdefault('corrections',[]).append({'original_text':before,'corrected_text':after,
                'image_locator':[0,0,*page['image_size']], 'reason':reason, 'annotation_source':'ai',
                'annotation_model':'Codex', 'annotation_version':VERSION, 'image_sha256':digest(image),
                'human_review_status':'not_reviewed'})
        receipt['correction_applied'] = after != before
        receipts.append(receipt)
        write_json(out / 'pages' / f'{n:03d}.json',page)
    if [p['pdf_page'] for p in data['pages']] != list(range(1,348)):
        raise ValueError('page_order_or_coverage_invalid')
    data['schema_version'] = 'deepprof-fulltext-ocr-v3'
    data['parent_ocr_sha256'] = parent_hash
    data['annotation_version'] = VERSION
    data['status'] = 'ocr_complete; ai_focus_review_complete_with_explicit_unresolved_content; human_review_pending'
    data['coverage'].update({'supplemental_ai_focus_reviewed_pages':347,
        'supplemental_corrected_pages':sum(r['correction_applied'] for r in receipts),
        'unresolved_focus_pages':[r['pdf_page'] for r in receipts if r['unresolved_reason']],
        'printed_pages_confirmed':sum(p.get('printed_page') is not None for p in data['pages']),
        'index_eligible_pages':sum(p['index_eligible'] for p in data['pages'])})
    write_json(out / 'textbook-full-ocr.json',data)
    (out / 'textbook-full-ocr.md').write_text('\n\n'.join(f"<!-- PDF page {p['pdf_page']} -->\n{p['corrected_text']}" for p in data['pages']),encoding='utf-8')
    proof = {'version':VERSION,'parent_ocr_sha256':parent_hash,'ocr_sha256':digest(out/'textbook-full-ocr.json'),
        'pages':347,'review_source':'ai','human_review_status':'not_reviewed','receipts':receipts,
        'batch_ranges':batches(347),'screening_sha256':digest(out/'screening.json'),
        'services_started':[],'request_count':0,'old_ocr_preserved':digest(old)==parent_hash}
    write_json(out/'review-manifest.json',proof)
    return {k:v for k,v in proof.items() if k!='receipts'}

if __name__ == '__main__':
    print(json.dumps(build(),ensure_ascii=False,indent=2))
