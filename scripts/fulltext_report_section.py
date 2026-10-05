"""Render the active batch's evidence, without inserting request messages."""
from __future__ import annotations

def table(headers, rows):
    return '\n'.join(['| '+' | '.join(headers)+' |','|'+'|'.join(['---']*len(headers))+'|',
        *['| '+' | '.join(str(v).replace('|','／').replace('\n',' ') for v in row)+' |' for row in rows]])

def fraction(x):
    return f"{x['numerator']}/{x['denominator']} ({x['rate']:.1%})" if x['denominator'] else 'N/A'

def number(x):
    return f'{x:.4f}' if x is not None else 'N/A'

def integrate_fulltext(markdown, data):
    coverage, cfg, budget = data['ocr_coverage'], data['configuration'], data['budget']
    counts = data['annotation_counts']; index=data['index_manifest']
    splits={'historical':'旧40题','development':'新开发40题','sealed_test':'封存40题'}
    names={'retrieval-on_constraint-on':'检索开／约束开','retrieval-on_constraint-off':'检索开／约束关',
        'retrieval-off_constraint-on':'检索关／约束开','retrieval-off_constraint-off':'检索关／约束关'}
    status='已完成' if data['matrix_complete'] and data['configuration_eligible'] else '未完成'
    observed=data['formal_observed_cells']; valid=data['formal_eligible_cells']
    headline=f"全文 OCR 为347/347页；新批次正式矩阵{status}，有效／观察／计划格为{valid}/{observed}/480。AI标注已保存检索{counts['retrieval']}条、引用{counts['citation']}条、可答性{counts['questions']}题。真实人员复核待实际评审提交。"
    interpretation=''
    if data['matrix_complete'] and counts['questions']==120:
        pooled={}
        for c,groups in data['conditions_by_split'].items():
            pooled[c]={m:{'numerator':sum(r[m]['numerator'] for r in groups.values()),
                          'denominator':sum(r[m]['denominator'] for r in groups.values())}
                       for m in ['false_accept_generation','false_reject_no_generation']}
            for v in pooled[c].values():v['rate']=v['numerator']/v['denominator'] if v['denominator'] else None
        constrained=pooled['retrieval-on_constraint-on'];open_=pooled['retrieval-on_constraint-off']
        interpretation=f"合并三个分区的描述性结果：检索开启时，证据不足题进入生成由约束关闭的{fraction(open_['false_accept_generation'])}降为约束开启的{fraction(constrained['false_accept_generation'])}；可答题未生成均为{fraction(constrained['false_reject_no_generation'])}。证据约束仍允许部分证据不足题生成，尚不能当作可靠拒答保障。检索关／约束开的零生成来自固定规则，可答题也全部未生成，不构成性能提升证据。分区结果和动作口径见下表；这里评价进入生成的决策，不评价答案正确率。"
    rows=[]
    for condition, groups in data['conditions_by_split'].items():
        for split,r in groups.items():
            rows.append([names[condition],splits[split],f"{r['valid']}/{r['observed']}/{r['planned']}",r['requests'],
                fraction(r['false_accept_generation']),fraction(r['false_reject_no_generation']),
                fraction(r['developer_action_match']) if split=='historical' else '不适用'])
    retrieval=[]
    for pool, groups in data['retrieval_by_pool_and_split'].items():
        for split,r in groups.items():
            for method,v in r['methods'].items():
                retrieval.append(['旧索引' if pool=='historical' else '全文索引',splits[split],method,
                    v['scored_queries'],number(v['recall_at_20']),number(v['ndcg_at_5']),r['unknown_candidate_rows']])
    citations=[]
    for key,r in data['citation_by_pool_condition_split'].items():
        pool,condition,split=key.split('/')
        citations.append(['旧引用' if pool=='historical' else names[condition],splits[split],r['pairs'],
            fraction(r['single_support']),fraction(r['claim_evidence_coverage']),fraction(r['source_location_rate']),r['unknown_pairs']])
    citation_interpretation=''
    if data.get('citation_codex_review'):
        values={}
        for condition in ['retrieval-on_constraint-on','retrieval-on_constraint-off']:
            group=[r for key,r in data['citation_by_pool_condition_split'].items() if key.startswith('fulltext/'+condition+'/')]
            values[condition]={}
            for metric in ['single_support','claim_evidence_coverage']:
                n=sum(r[metric]['numerator'] for r in group);d=sum(r[metric]['denominator'] for r in group)
                values[condition][metric]=fraction({'numerator':n,'denominator':d,'rate':n/d if d else None})
        on=values['retrieval-on_constraint-on'];off=values['retrieval-on_constraint-off']
        citation_interpretation=f"全文批次合并分区：检索开／约束开单条完整支持为{on['single_support']}，主张联合证据覆盖为{on['claim_evidence_coverage']}；检索开／约束关分别为{off['single_support']}和{off['claim_evidence_coverage']}。所有已配对来源均可定位，但定位正确不保证语义支持，且大量主张没有显式引用。两个条件提取的主张与引用分母不同，这些为描述性份额，不作为同一主张的配对因果效应或答案正确率。"
    section='\n'.join([
        '## 全文教材与120题新批次',
        f"当前批次 `{data['batch_id']}`，状态 `{data['batch_status']}`。{headline} 旧40题保留会话历史，新开发和封存各40题采用单轮冷启动；BKT训练结果沿用原版本。",
        f"教材以2倍缩放强制RapidOCR，按不超过50页分成七批，347页无遗漏、重复或乱序。已保存逐页原始／校正文本、行框、置信度、图像哈希及AI复核记录。重点补核覆盖{coverage.get('supplemental_ai_focus_reviewed_pages',0)}页，补充校正{coverage.get('supplemental_corrected_pages',0)}页；印刷页码逐页确认{coverage['printed_pages_confirmed']}页，其余为空。重点核对不等于全部字符、二维图连接或人员复核认证。",
        f"仍有明确未确认重点内容的PDF页：{', '.join(map(str,coverage.get('unresolved_focus_pages',[]))) or '无'}。这类内容保留原始图像和具体原因，不补造图中关系。旧OCR及索引保留，独立新索引按800字分块、120字重叠得到{index['chunks']}片段，其中{index['reliable_chunks']}个属于{index['reliable_pages']}个可作为可靠证据的正文页。",
        '语义复核阶段另在PDF 34、40、171、180页发现代码字符OCR错误，并放大核对原页图形成独立AI勘误。勘误不回写已冻结的实验文本或索引；本轮结果仍使用上述固定版本，其算法文字与代码字符准确性须分别判断。未宣称教材程序整体可编译，人员复核仍待提交。' if data.get('post_freeze_code_errata') else '',
        '检索固定采用字符哈希向量与BM25，两路各取50，RRF常数60，最终证据5条；沿用证据不足判断，未接入BGE在线检索。检索关／约束开按规则零生成。',
        f"生成配置：`{cfg['model']}`，温度{cfg['temperature']}，`thinking.type=disabled`，8192 tokens，自动重试0；每格最多一次生成，未使用种子重复或推断性置信区间。AI评估请求温度为0。预算由本批次冻结授权读取：本轮{budget['batch_requests']}/{budget['additional_ceiling']}次，累计{budget['cumulative_requests']}/{budget['cumulative_ceiling']}次，未决{budget['unresolved_requests']}次。分阶段实际请求：预检{budget['phase_requests']['preflight']}／正式{budget['phase_requests']['formal']}／AI标注{budget['phase_requests']['annotation']}，上限分别为{budget['phase_ceilings']['preflight']}／{budget['phase_ceilings']['formal']}／{budget['phase_ceilings']['annotation']}；失败和未决计入账本。",
        f"每格发送前检查冻结配置、代码、教材、索引、题集、BKT参数及预算。配置SHA-256 `{cfg['config_sha256']}`；教材校正版SHA-256 `{cfg['ocr_sha256']}`；索引内容SHA-256 `{cfg['index_sha256']}`；提示模板指纹 `{cfg['prompt_sha256']}`。原始prompt、模板源码及实际消息均独立归档，不写入正文或附录；参考答案、标签和标注理由仅进入评估。",
        f"停止原因：{data.get('stop_reason') or '无'}；清理状态：`{data.get('cleanup')}`。之前的停止批次及旧指纹不匹配批次保留原状态，仅作历史诊断，不进入当前正式比较。",
        '本批次曾因独立引用诊断文件占用账本保留文件名而安全暂停；更名后核清未决请求并验证服务清理，沿原冻结批次断点续跑。已完成格逐项核对哈希不变，未重发；报告工具等非运行文件的工作区指纹变化单独记为历史，冻结运行文件仍严格逐项比对。' if data.get('checkpoint_resume_evidence_sha256') else '',
        '本批次预检在外发HTTP前被配置守卫拦截：教学图丢失明确关闭思考的上下文，错误记录分支又引用了不存在的属性，未写入模型失败事件。账本保守计入一次失败尝试，实际外发请求为0。已修复上下文传递、失败事件、检索开关及清理落盘，并通过本地Mock和真实教学图的四条件测试；仍保留v3原冻结状态，修复代码需重新冻结，未在该批次自动重发。' if data['batch_status']=='stopped_preflight_configuration_guard' else '',
        '### 分区消融结果',
        interpretation,
        table(['条件','分区','有效／观察／计划','请求','不足时生成','可答时未生成','旧题动作匹配'],rows),
        '![图7｜新批次矩阵覆盖与有效格](figures/figure-07-fulltext-matrix.png)',
        '错误接受以证据不足题进入生成为分子，错误拒答以可答题未进入生成为分子；两者以已完成AI可答性判断的对应题数为分母，不等于答案正确率。缺失标签和无分母指标为N/A。动作匹配只用于原有预期动作的旧40题。停止批次不产生正式比较分数。',
        '![图10｜四条件分区生成决策错误率](figures/figure-10-fulltext-ablation-decisions.png)',
        '图10显示全部四条件、三个40题分区，行末依次列出证据不足题生成的分子／分母和可答题未生成的分子／分母。检索关／约束开按固定规则零生成，因此其不足时生成率为0、可答时未生成率为100%；这项取舍来自规则，不能作为拒答能力提升。所有比例均使用AI可答性标签，无种子重复或推断性置信区间。',
        '![图11｜同题开启证据约束后的生成决策变化](figures/figure-11-fulltext-ablation-paired.png)',
        '图11以同一题从约束关闭到约束开启的决策作配对，分别在检索开、关时保留120题，共240个配对；按分区和AI可答性分层。检索开启时，证据不足题中5道旧题、2道开发题、1道封存题停止生成，另15道仍生成；97道可答题的决策均未改变，其中90道两条件都生成、7道两条件都未生成。检索关闭时，约束开启的规则使23道不足题和90道原本生成的可答题均停止生成，另7道可答题两条件都未生成。上述是有限构造题集的一次运行决策变化，不评价答案正确率或因果性性能增益；逐格哈希和配对计数已另存来源数据。',
        '### AI标注与检索质量',
        f"保存AI语义标注：检索{counts['retrieval']}条，引用{counts['citation']}条，可答性{counts['questions']}题。原人员评审表独立保留。候选按问题与证据去重判断，再映射到方法排名；记录具体理由、证据位置、来源、版本和请求哈希。本地Codex结构审查的哈希明确记为本地复核凭据，不冒充供应商请求。AI标签仅用于探索性指标，未生成双人一致率或人员κ。",
        f"首次检索标注响应虽正常结束，但把问题编号和PDF页码混入标签字段，未通过语义标注格式校验，整次响应不进入指标且仍计入请求账本。随后独立冻结评估器版本 `{data['independent_evaluator']['version']}`，每次528项，使用明确命名的输入字段和逐项来源行号校验；不更改已完成正式生成的冻结配置。评估器源码、输入和规则指纹及实际请求单独归档。最终检索标注协议异常{data.get('retrieval_protocol_exceptions',0)}项；异常保留为无法判定，不补造标签。" if data.get('independent_evaluator') else '',
        f"格式通过不代表语义正确。另以Codex逐条读源核对{data['retrieval_spotcheck']['reviewed_rows']}条历史候选，修正{data['retrieval_spotcheck']['changed_rows']}条：包括把否定错误复杂度的直接证据误记为不相关，以及将仅覆盖算法步骤而缺少适用条件的片段记为直接覆盖。该抽查按已读正例、部分支持和负例对照选取，不是随机样本，不能据此估计总体误标率。供应商原标签表和本地复核凭据均独立保留。其余AI判断尚无真实人员复核，检索及题目评估与生成使用同一模型也可能存在共同偏差，引用另由Codex独立评估，指标仅作探索性比较。" if data.get('retrieval_spotcheck') else '',
        '15道原定义包含三轮的旧题，按正式实验实际发送的首轮另做Codex语义复核，并逐段核对教材行号和必要条件；原多轮判断独立保留。最终120题可答性表与实际首轮输入逐项匹配，复核没有增加供应商请求。' if counts['questions']==120 and data.get('checkpoint_resume_evidence_sha256') else '',
        f"{data['question_span_normalization']['normalized_questions']}题的供应商证据范围超过每段8行约束，但页码与起止行均在来源内；将原范围拆为每段至多8行，逐字核验后恢复供应商原可答性判断。未增加请求、未推断新语义标签，原响应及规范化凭据分别保留；来源越界范围不按此规则恢复。" if data.get('question_span_normalization') else '',
        '当前仅保存15道旧题首轮的本地Codex可答性复核。向外部接口发送全文派生候选进行标注的操作被自动审批拦截，尚未外发；其余题目及检索、引用语义指标仍待明确授权与实际标注，缺失值保留N/A。正式480格的运行完成状态与此标注状态分别记录。' if data.get('annotation_send_status',{}).get('status','').startswith('external_annotation_not_sent') else '',
        table(['候选池','分区','方法','计分题数','条件Recall@20','nDCG@5','无法判定条数'],retrieval) if retrieval else '检索标注尚未完整返回，质量指标为N/A。',
        '![图8｜已标注候选池中的检索质量](figures/figure-08-fulltext-retrieval.png)',
        'Recall@20以每题已标注候选池中等级1或2的片段为分母，不是全教材召回。全文候选池来自两路Top-50并集；含无法判定条目的题目单独记录覆盖，不进入完整候选池指标。候选池全为等级0时相关分母为0，该题同样不进入均值；各方法在同一候选池内共用计分题集，旧索引与全文索引的计分题集可能不同，不能将跨索引均值差当作配对增益。',
        '### 引用与答案证据覆盖',
        '234个主张—来源配对由本地Codex逐条阅读64段实际引用原文，完成单条支持、211条不同主张的联合支持及来源定位评估，新增外部接口请求0次。配对单条标签为完整支持175、部分支持44、无支持10、矛盾1、无法判定4。另1359条无显式引用主张按引用结构记录无支持，2条未提取到主张的引用保留无法判定，总计1595条独立AI标注。此前DeepSeek引用请求HTTP 402作为失败历史保留，未重试；本地评估是用户明确授权的独立标注方式。原文、逐条理由、联合引用清单与哈希凭证单独归档，真实人员复核仍待提交。' if data.get('citation_codex_review') else '',
        '引用AI请求返回HTTP 402，没有任何语义标签，失败已计入账本且未自动重试。1595条引用审计记录已准备：234个主张—来源配对待语义判断，1359条主张没有显式引用，另2条引用未提取到对应主张。单条支持和联合证据覆盖保持N/A；不会把接口失败记成无支持标签或补造结果。' if data.get('annotation_provider_failure') and counts['citation']==0 else '',
        table(['来源／条件','分区','配对','单条完整支持','主张联合覆盖','来源定位','无法判定'],citations) if citations else '引用语义判断尚未完整返回，支持率和主张证据覆盖为N/A。',
        citation_interpretation,
        '![图9｜引用支持与主张证据覆盖](figures/figure-09-fulltext-citation.png)',
        '单条支持、实际引用的联合支持与来源定位分别判断，使用完整支持／部分支持／无支持／矛盾／无法判定五分类。只计回答实际引用；无显式引用主张记为引用支持缺失，不据此判断事实错误。未被回答引用的全文内容不能补充支持率。无法判定保留在引用审计分母并单列数量，完整支持比例表示已确认的支持份额，不把未知标签判成事实错误。',
        '',
    ])
    markdown=markdown.replace('## RAG 消融、检索与引用审计',section+'\n## 历史RAG批次与原版本审计')
    markdown=markdown.replace('### 四条件消融','### 历史修复批次（保留原状态）',1)
    markdown=markdown.replace('四条件的主比较仍为 N/A。原正式 120 格继续保留为历史证据；新修复批次计划 160 格，尚无正式格。',
        '该历史修复批次的四条件比较仍为N/A。原版本120格和该批次未执行的160格计划保留为历史诊断；当前全文120题的比较见前文新批次结果。')
    markdown=markdown.replace('正式条件尚未启动；`retrieval-off_constraint-on`',
        '该历史批次正式条件尚未启动；`retrieval-off_constraint-on`')
    markdown=markdown.replace('图 5｜RAG 检索排序质量状态与延迟','图 5｜旧索引离线检索状态与延迟（历史诊断）')
    markdown=markdown.replace('图 1｜数据覆盖与证据状态','图 1｜历史数据覆盖快照（当前全文批次见图7–11）')
    markdown=markdown.replace('图 6｜引用指标分子、分母与审计来源','图 6｜旧版本引用指标分母与审计来源（历史诊断）')
    markdown=markdown.replace('### 引用口径：旧 120 格','### 原版本审计口径：旧120格',1)
    markdown=markdown.replace('新 80 题（32 可答+8 证据不足/每个分区）','新80题（可答性按全文复核）')
    markdown=markdown.replace('新题答案性和金证据待人工确认','AI可答性按全文独立复核，人员复核待实际提交')
    markdown=markdown.replace('六组图均由','历史六组图和新增五组图均由')
    markdown=markdown.replace('检索质量仍为 N/A：40 题的候选证据尚待盲评，不将排序分数当作 Recall 或回答质量。','该历史BGE语义检索的人员盲评质量仍为N/A；当前字符哈希、BM25与RRF的AI指标见前文。排序分数不作为Recall或回答质量。')
    markdown=markdown.replace('| 修复批次 RAG 四条件补跑 |','| 历史修复批次 RAG 四条件补跑 |')
    markdown=markdown.replace('| 课程检索相关性/回答性 | 等待人工标注 |',
        f"| 课程检索相关性/回答性 | AI检索{counts['retrieval']}条／题目{counts['questions']}题；人员待复核 |")
    markdown=markdown.replace('| 回收双盲评分、裁决分歧、计算分组 Recall@20/nDCG@5 与回答证据门限 |',
        '| 完成AI语义判断后计算探索性分组指标；真实人员提交后另行计算人员一致率 |')
    if counts['retrieval'] and counts['questions']==120:
        markdown=markdown.replace('完成AI语义判断后计算探索性分组指标；真实人员提交后另行计算人员一致率',
            'AI探索性分组指标已计算；真实人员提交后核验标签并另行计算人员一致率')
    if data.get('annotation_provider_failure') and counts['citation']==0:
        markdown=markdown.replace('| NoMIRACL 中文完整固定候选测试 |',
            '| 当前引用配对语义标注 | 234配对待判断；DeepSeek HTTP 402，未自动重试 | 等待用户明确选择后续评估方式；缺失指标为N/A |\n| NoMIRACL 中文完整固定候选测试 |')
    begin=markdown.index('## 本轮结论'); end=markdown.index('## 数据与边界')
    bkt=next(line for line in markdown[begin:end].splitlines() if line.startswith('BKT 的主要优化证据'))
    markdown=markdown[:begin]+'## 本轮结论\n'+bkt+'\n\n'+headline+'\n\n'+markdown[end:]
    return markdown
