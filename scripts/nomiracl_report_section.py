"""Publish only completed NoMIRACL results and retire archived placeholders."""
from pathlib import Path
import json
import re

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'docs/experiments/bkt-rag-improvement-20261001'

def table(headers,rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|',
                      *['| '+' | '.join(map(str,r))+' |' for r in rows]])

def integrate_nomiracl(markdown,summary,course_table,model_table):
    # The archived stop evidence remains available through the delivery lineage.
    markdown=re.sub(r'### 历史修复批次（保留原状态）.*?(?=### 原版本审计口径)', '', markdown, flags=re.S)
    markdown='\n'.join(line for line in markdown.splitlines()
                       if not any(s in line for s in ['figure-04-rag-ablation-status','figure-05-retrieval-quality-and-latency',
                                                       '| 历史修复批次 RAG 四条件补跑 |']))
    markdown=markdown.replace('## 历史RAG批次与原版本审计','## 原版本引用审计')
    markdown=re.sub(r'图 1｜历史数据覆盖快照（当前全文批次见图7–11）','图 1｜数据覆盖与证据状态',markdown)
    markdown=re.sub(r'历史六组图和新增五组图均由.*?生成；每组提供 SVG、PDF、600-dpi PNG、R 脚本与来源 CSV。',
                    '当前图表均由R 4.6.1生成，提供SVG、PDF、600-dpi PNG、R源码和来源数据。历史占位图及停止证据已独立归档；不纳入当前比较。',markdown)
    if not summary or summary.get('status')!='completed':
        return markdown
    course_table=course_table.replace('检索质量仍为 N/A：40 题的候选证据尚待盲评，不将排序分数当作 Recall 或回答质量。',
        '该旧BGE检索的人员盲评质量为N/A；全文检索AI指标见前文。排序分数不作为Recall或回答质量。')
    c=summary['counts'];g=summary['test_evidence_gate'];t=summary['threshold_selection'];lat=summary['latency_seconds']
    methods=['BGE-M3 dense','BM25','BGE-M3 + BM25 RRF-60','BGE reranker v2-m3']
    ranks=[[m,f"{summary['ranking_metrics']['dev:'+m]['mean_nDCG_at_5']:.4f}",
              f"{summary['ranking_metrics']['test:'+m]['mean_nDCG_at_5']:.4f}"] for m in methods]
    gate=[['错误接受 FAR',f"{g['false_accepts']}/{g['non_relevant_queries']}",f"{g['false_accept_rate']:.2%}"],
          ['错误拒绝 FRR',f"{g['false_rejections']}/{g['relevant_queries']}",f"{g['false_rejection_rate']:.2%}"],
          ['最大重排分数 AUC',f"{g['relevant_queries']}相关 × {g['non_relevant_queries']}非相关",f"{g['reranker_max_score_auc']:.4f}"]]
    stage_rows=[[name,f'{lat[key]:.2f}','秒／完整阶段'] for name,key in
                [('文档编码','bge_m3_document_encode'),('查询编码','bge_m3_query_encode'),
                 ('四方法排序与指标','bge_m3_query_candidate_scoring'),('配对重排','bge_reranker_pair_scoring')]]
    stage_rows += [['本次运行总墙钟',f"{lat['current_orchestration_wall']:.2f}",'秒／含加载与编排'],
                   ['重排平均每配对',f"{summary['latency_milliseconds']['reranker_mean_per_candidate_pair']:.3f}",'毫秒／配对；不是中位数']]
    section='\n\n'.join([
        '### 旧课程索引的离线耗时',course_table,
        '## NoMIRACL 中文完整固定候选评测',
        f"已完成全部{c['queries_total']:,}题：开发集{c['dev_queries']:,}题（相关{c['dev_relevant']}、非相关{c['dev_non_relevant']}），测试集{c['test_queries']:,}题（相关{c['test_relevant']}、非相关{c['test_non_relevant']}）。评分覆盖{c['candidate_documents_unique']:,}个不同文档、{c['candidate_pairs']:,}个配对，保存150,396条四方法排名记录。",
        f"数据revision `{summary['dataset_revision']}`。按固定版本的[官方加载逻辑](https://huggingface.co/datasets/miracl/nomiracl/resolve/{summary['dataset_revision']}/nomiracl.py)过滤语料不存在的95条qrel，其中35条正例（开发32条／13正例；测试63条／22正例）。不排除任何题目，不补造缺失文本；语料中{c['identical_duplicate_corpus_rows']:,}条内容完全相同的重复行合并，冲突ID拒绝运行。",
        '固定候选内比较BGE-M3 dense、BM25、两路RRF-60和BGE reranker v2-m3；BM25 k1=1.2、b=0.75，沿用中文二字切分与小写字母数字分词。文本为标题加正文，文档与配对最多512 tokens，查询256 tokens；分数并列按docid升序。此处RRF使用每题完整固定候选的两路排名，与教材在线Top-50检索范围不同。',
        f"专用本地环境FP32、关闭TF32，编码批大小2，重排4；实际阶段设备：{', '.join(k+'='+v for k,v in summary['device'].items())}。每256项原子保存，输入、模型、代码、评分配置和设备指纹绑定缓存；CUDA阶段失败时完整改用CPU FP32、16线程，隔离设备缓存。有限输出检查和数量校验通过，新增外部模型请求0次，累计账本857/887保持不变。",
        '### 固定候选排序',table(['方法','dev相关题平均 nDCG@5 (n=393)','test相关题平均 nDCG@5 (n=920)'],ranks),
        '![NoMIRACL 中文固定候选排序](figures/figure-12-nomiracl-ranking.png)',
        'nDCG@5在保留的候选与qrel内计算，只对相关题取均值。没有全库Recall，没有生成答案评测；本轮单次固定快照不报告重复实验置信区间。',
        f"两个分区的重排平均nDCG@5均最高；测试集{summary['ranking_metrics']['test:BGE reranker v2-m3']['mean_nDCG_at_5']:.4f}，高于BGE-M3 dense的{summary['ranking_metrics']['test:BGE-M3 dense']['mean_nDCG_at_5']:.4f}。两路RRF为{summary['ranking_metrics']['test:BGE-M3 + BM25 RRF-60']['mean_nDCG_at_5']:.4f}，低于稠密单路，说明本固定候选与分词配置下，融合不保证排序收益。这里只比较本快照，不据此推广到全库或其他配置。",
        '### 冻结门限的测试集结果',
        f"仅用开发集最大重排分数选门限θ={t['threshold']:.8f}，接受规则为最大分数≥θ。在非相关题FAR≤5%条件下最大化相关题接受率，并列选较低门限。开发FAR={t['development_false_accept_rate']:.2%}，相关题接受率={t['development_relevant_accept_rate']:.2%}。门限在测试指标计算前独立冻结；测试标签未用于选型或调参。",
        table(['测试指标','分子／分母或AUC比较单位','结果'],gate),
        '![NoMIRACL 测试集证据门限错误率](figures/figure-13-nomiracl-gate.png)',
        'FAR以非相关题接受数为分子、1,375题为分母；FRR以相关题拒绝数为分子、920题为分母。AUC使用最大重排分数和题级相关标签，分数并列取平均秩。这些是固定候选排序及门限指标，不能当作教材实验的回答正确率或全库召回能力。预训练模型与公开基准的潜在数据重叠仍是解释限制。',
        f"开发集5%错误接受上限对应的门限在测试集拒绝了{g['false_rejections']}/{g['relevant_queries']}道相关题（{g['false_rejection_rate']:.2%}）。因此较低FAR伴随较高FRR，不能仅凭错误接受率宣称门限可靠或适合课程直接部署。未利用测试结果调整门限。",
        '### 耗时与冻结证据',table(['阶段','耗时','单位'],stage_rows),
        f"评分配置SHA-256 `{summary['config_sha256']}`；门限文件SHA-256 `{summary['threshold_frozen_sha256']}`。总墙钟包含加载、数值预检及编排，阶段计时来自保存批次；平均每配对明确以毫秒给出，不冒充P95或中位数。完整逐题数据和四方法分数排名见[`nomiracl-zh-per-query.csv`](data/nomiracl-zh-per-query.csv)及[`nomiracl-zh-candidate-ranks.csv`](data/nomiracl-zh-candidate-ranks.csv)。配置、缓存、日志、清理证明和归档哈希由交付清单追踪。",
        model_table,
    ])
    markdown=re.sub(r'### 离线语义检索.*?(?=## 人工标注包与复核流程)',section+'\n\n',markdown,flags=re.S)
    markdown='\n'.join(line for line in markdown.splitlines() if '| NoMIRACL 中文完整固定候选测试 |' not in line)
    markdown=markdown.replace('NoMIRACL 本轮未得出完整评测结果；MIRACL 全库未建 490 万级语料向量索引。',
                              'NoMIRACL 全部3,770题固定候选评测已完成；MIRACL 全库未建490万级语料向量索引。')
    position=markdown.index('## 数据与边界')
    conclusion=(f"NoMIRACL中文固定候选全量评测已完成：37,599配对；测试门限FAR={g['false_accept_rate']:.2%}、"
                f"FRR={g['false_rejection_rate']:.2%}、AUC={g['reranker_max_score_auc']:.4f}。这是候选排序与证据接受判断，人员复核保持待提交。\n\n")
    markdown=markdown[:position]+conclusion+markdown[position:]
    # Number by final reading order, with a reproducible mapping from stable stems.
    images=list(re.finditer(r'!\[([^\]]+)\]\((figures/[^)]+)\)',markdown))
    old_numbers={}
    mapping=[]
    for number,match in enumerate(images,1):
        old=re.match(r'图\s*(\d+)\s*｜',match[1])
        if old:old_numbers[int(old[1])]=number
        mapping.append({'figure_number':number,'path':match[2]})
    def renumber(match):
        old=int(match[1]);return '图'+str(old_numbers.get(old,old))
    markdown=re.sub(r'图\s*(\d+)(?!\d)',renumber,markdown)
    i=iter(range(1,len(images)+1))
    markdown=re.sub(r'!\[([^\]]+)\]\((figures/[^)]+)\)',
                    lambda m:'![图'+str(next(i))+'｜'+re.sub(r'^图\s*\d+\s*｜','',m[1])+']('+m[2]+')',markdown)
    (OUT/'data/report-figure-order.json').write_text(json.dumps(mapping,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    return markdown+'\n'
