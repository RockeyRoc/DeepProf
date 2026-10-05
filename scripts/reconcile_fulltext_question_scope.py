"""Apply separately recorded Codex evidence reviews to actual first-turn scope.

No provider call and no modification of frozen generation files. Original
multi-turn provider judgments remain in a separate file for audit.
"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.run_m3_fulltext_rag import RAW_RESEARCH,ACTIVE_BATCH,read_csv
from scripts.annotate_fulltext_rag import write_csv,frozen_ocr
from scripts.ocr_full_textbook import write_json,digest

# Each interval was read and semantically reviewed against frozen corrected text.
REVIEWS={
 'DSDEV-016':([(138,20,24),(138,30,34)],'区分根、左子树、右子树的访问顺序及空树条件。','后序先递归左右子树、最后访问根；先序首先访问根。教材操作定义可直接纠正首轮说法。'),
 'DSDEV-021':([(73,45,48),(73,50,53)],'说明循环后头尾指针相等的歧义，以及标志位或预留空位的区分约定。','原文明确空与满都可能满足front=rear，并给出两种区分方法，覆盖首轮询问的原因。'),
 'DSDEV-036':([(34,1,8),(34,9,14),(34,15,17),(34,21,24)],'合法位置、容量检查、从末端向插入位置后移、写入新元素和长度更新。','教材文字明确第n至第i元素后移，并说明插入位置、扩容和长度增加；足以解释操作步骤。损坏的循环代码行不作为可编译源码证据。'),
 'DSDEV-017':([(179,27,33),(180,8,14),(180,15,22)],'区分按层扩展的BFS与递归深入的DFS，说明访问标记和辅助队列。','教材将广度优先遍历定义为按层过程，算法采用入队、队头出队及未访问邻点入队，可纠正首轮把BFS说成递归DFS的表述。'),
 'DSDEV-022':([(138,17,19),(138,20,24),(138,25,29),(138,30,34)],'递归定义的空树条件、先左后右约定，以及三种根访问位置。','教材明确从二叉树递归定义得到三种操作定义，分别列出先序、中序、后序步骤，覆盖首轮推导所需条件。'),
 'DSDEV-037':([(138,20,24)],'先判断空树，非空时先访问根，再递归左右子树。','先序操作定义直接列出空树分支和第一个访问动作，可支持首轮基础讲解。'),
 'DSDEV-018':([(38,18,21),(40,12,18)],'区分头结点、首元结点和头指针；限定教材带头结点实现，并说明前驱链接与被删节点释放。','教材定义头结点并给出从L开始寻找前驱、修改p->next并释放q的删除步骤。首轮方向措辞和头节点名称须澄清，不能把某种实现的前驱链接操作泛化成任意头节点删除。教材支持这一有条件的纠正。'),
 'DSDEV-023':([(179,27,33),(180,12,17),(180,18,22)],'新顶点标记并访问后入辅助队列，随后按先访问者先扩展的次序处理。','算法在发现未访问顶点时调用EnQueue，正文要求先被访问顶点的邻接点先处理，可解释首轮入队位置与原因。'),
 'DSDEV-038':([(171,31,32),(173,6,12)],'矩阵以二维数组表示顶点关系，邻接表为每顶点建立边链表，并说明表头和边节点。','两节分别明确二维数组与按顶点建立单链表的表示方式，足以对照解释首轮混淆。'),
 'DSDEV-019':([(294,1,4)],'每趟线性归并、有序段长度翻倍和对数趟数，区分单次比较与总工作量。','教材描述每趟相邻有序段归并及对数趟数，并明确时间复杂度O(nlogn)，不能以单次比较推出平均O(n)。'),
 'DSDEV-024':([(293,48,52),(293,53,56)],'输入为两个有序段，比较当前头部逐个写出，一段用尽后复制另一段剩余记录。','Merge算法分别写出两段的当前较小记录，并显式复制剩余段；这些条件说明如何避免遗漏元素。'),
 'DSDEV-039':([(273,27,30)],'只比较关键字相等记录的排序前后相对次序，区分稳定与不稳定。','教材直接以相等关键字记录是否仍保持先后次序定义稳定性，可支持首轮基础讲解。'),
 'DSDEV-020':([(169,1,3),(179,27,33)],'路径必须沿实际边连接；无权路径长度按边数，最短取最少边数，与顶点编号无关。','教材将路径长度定义为边或弧的数目，广度遍历按路径长度1、2等由近至远访问；编号不是这一定义的长度量。可纠正首轮序号差说法。'),
 'DSDEV-025':([(34,1,8)],'第i位置插入时将第n至第i元素向后移动一个位置，并保留原次序。','正文明确移动范围、方向与一个位置的位移，可直接回答首轮中间插入问题。'),
 'DSDEV-040':([(179,27,33),(180,8,14),(180,15,22)],'初始化队列、访问并入队、队头出队、发现未访问邻点后标记访问并入队。','教材算法及按层遍历文字完整覆盖队列的基础操作顺序；首轮没有要求未知具体图的访问序列。')}


def review(bid=ACTIVE_BATCH, apply=False):
    batch=RAW_RESEARCH/bid;folder=batch/'ai-annotation'
    cfg=json.loads((batch/'frozen-config.json').read_text(encoding='utf-8'))
    cases={c['case_id']:c for c in json.loads((batch/'cases.json').read_text(encoding='utf-8'))['cases']}
    assert set(REVIEWS)=={c['case_id'] for c in cases.values() if c['split']=='historical' and len(c['user_turns'])>1}
    pages=json.loads(frozen_ocr(batch).read_text(encoding='utf-8'))['pages'];reviews={}
    for case_id,(ranges,conditions,reason) in REVIEWS.items():
        evidence=[]
        for n,a,b in ranges:
            lines=pages[n-1]['corrected_text'].splitlines()
            assert 1<=a<=b<=len(lines) and b-a<8 and pages[n-1]['index_eligible']
            evidence.append({'pdf_page':n,'line_start':a,'line_end':b,'quote':'\n'.join(lines[a-1:b])})
        receipt={'case_id':case_id,'question_text':cases[case_id]['user_turns'][0],
            'original_case_turns_sha256':hashlib.sha256(json.dumps(cases[case_id]['user_turns'],ensure_ascii=False).encode()).hexdigest(),
            'evaluation_scope':'actual_first_user_turn; original historical session state retained',
            'answerability':'answerable','required_conditions':conditions,'rationale':reason,
            'evidence':evidence,'annotation_source':'ai','annotation_model':'Codex',
            'annotation_version':'fulltext-ai-review-20261004-v4-first-turn-scope-v1',
            'annotation_ocr_sha256':cfg['ocr_sha256'],'human_review_status':'not_reviewed',
            'api_requests':0,'frozen_cases_sha256':cfg['cases_sha256']}
        canonical=json.dumps(receipt,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()
        receipt['annotation_request_sha256']=hashlib.sha256(canonical).hexdigest()
        reviews[case_id]=receipt
    write_json(folder/'first-turn-codex-review.json',{'scope':'15 multi-turn historical cases; first submitted turn only','api_requests':0,'records':reviews})
    if apply:
        source=folder/'questions-120-ai.csv';original=folder/'questions-120-ai-original-multiturn.csv'
        if not original.exists():original.write_bytes(source.read_bytes())
        rows=read_csv(original)
        assert len(rows)==120
        for row in rows:
            if row['case_id'] not in reviews:continue
            r=reviews[row['case_id']]
            row.update({k:r[k] for k in ['question_text','answerability','required_conditions','rationale',
                'annotation_source','annotation_model','annotation_version','annotation_ocr_sha256',
                'annotation_request_sha256','human_review_status']})
            row['evidence_locator']=json.dumps(r['evidence'],ensure_ascii=False)
            row['annotation_request_kind']='local_codex_review_receipt'
            row['unverified_model_quotes']='[]'
            row['evaluation_scope']=r['evaluation_scope']
        write_csv(source,rows)
    return {'first_turn_cases_reviewed':len(reviews),'api_requests':0,'applied_to_final_rows':apply}


if __name__=='__main__':print(review(apply='--apply' in sys.argv))
