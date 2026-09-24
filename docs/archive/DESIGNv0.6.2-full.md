# DeepProf 项目结构化设计文档

> **版本**：v0.6.2  
> **更新日期**：2026-09-23  
> **文档性质**：研究设计、系统架构与工程实施基线  
> **上一版本**：v0.6.1（2026-09-22）  
> **适用场景**：大学生创新创业训练计划的课程试点、原型研发、离线评测及后续学生实验

## 版本变更记录

| 版本 | 日期 | 变更摘要 |
|---|---|---|
| v0.1–v0.4 | 2026-09-14 至 2026-09-17 | 建立教育智能体、Runtime、教学图、课程资料和团队协作的初始设计 |
| v0.4.1 | 2026-09-19 | 确立 D-7：教学图输出声明式 PedagogicalDecision；RuntimePort 收窄为 execute 与 emit；RuntimeHost 承担执行能力；动作到能力的绑定由组合根注入 |
| v0.5 | 2026-09-21 | 增加统一客户端、CLI、Provider Hub、插件和本地/校园部署设计 |
| v0.6 | 2026-09-21 | 增加 Desktop、桌宠、API Key 接入、资源库及 MVP-1 至 MVP-5 路线 |
| v0.6.1 | 2026-09-22 | 记录 MVP-1、MVP-2 契约和实现进度，补充 Skill 结果透传、教材证据拼装及 Attempt 事件 |
| **v0.6.2** | **2026-09-23** | **将主线调整为学情驱动的自适应教学闭环；冻结数据结构试点范围和 A/B/DeepProf 三组；学生端收敛为 CLI，保留 Gateway/API 与本地只读回放；重写 MVP、团队分工、架构图和实验规范；扩展能力继续保留并标为非核心** |

> 本版本记录的是设计基线，不表示所列功能已经完成。v0.6.1 的交付描述属于该版本原文所载历史状态。原文对部分前端交付状态存在冲突，需在工程仓库和验收记录中逐项核实；本文件不据此推定当前完成度。

## 目录

1. [文档目标与使用规则](#一文档目标与使用规则)
2. [项目定位与研究问题](#二项目定位与研究问题)
3. [研究贡献与范围分级](#三研究贡献与范围分级)
4. [试点课程与数据包](#四试点课程与数据包)
5. [教学闭环与策略规则](#五教学闭环与策略规则)
6. [研究设计与评价指标](#六研究设计与评价指标)
7. [总体架构与数据流](#七总体架构与数据流)
8. [D-7 契约与关键数据接口](#八d-7-契约与关键数据接口)
9. [Runtime、事件、记忆与 Provider](#九runtime事件记忆与-provider)
10. [CLI 与本地只读回放服务](#十cli-与本地只读回放服务)
11. [工程目录与依赖边界](#十一工程目录与依赖边界)
12. [MVP、交付路线与验收](#十二mvp交付路线与验收)
13. [团队分工](#十三团队分工)
14. [部署、安全、隐私与教育伦理](#十四部署安全隐私与教育伦理)
15. [扩展能力设计](#十五扩展能力设计)
16. [决策记录与版本迁移](#十六决策记录与版本迁移)
17. [待满足条件与验收清单](#十七待满足条件与验收清单)

---

## 一、文档目标与使用规则

本文档把 DeepProf 定义为一个可实验的自适应教学系统，并给出研究问题、课程数据、教学决策、工程接口、对照实验和交付门槛。研发和答辩材料应先说明学生学习问题及验证方法，再介绍系统实现。

核心闭环为：

**学生输入与作答 → 学情状态估计 → 教学策略决策 → 教学干预 → 学习反馈 → 学情更新 → 下一轮决策与评测。**

可信教材证据为教学内容提供可定位依据。伴学交互用于承载动作和反馈，属于后续产品扩展。

### 1.1 文档约定

- 本文中的“核心”指研究 MVP 必须完成并用于主要实验的能力；“支撑”指让核心闭环可靠运行的工程能力；“拓展”指保留设计但不阻塞核心验收的功能。
- TBD 表示需要由指定责任人根据真实材料补齐。TBD 不得伪装成已确认事实。
- 试验结果、代码完成度、准确率和样本数量只能引用可复现的运行记录。没有实测结果时写“待测”。
- 教学阈值、提示文案和标注规则必须有版本号；指导教师校准后冻结版本，实验中途变更时另起版本。
- 参考实现只用于设计借鉴。引入代码、数据或模型前，应核查许可证、来源及使用条件。

### 1.2 面向评审的项目表述

DeepProf 面向大学生自主学习，通过估计学生对课程知识点的掌握状态，在讲解、追问、提示、纠错和测验之间选择教学动作，并使用可定位教材证据约束讲解。系统记录每次决策的状态依据和执行结果，使教学过程可以审查、回放和比较。项目先以离线案例评测检验策略与证据质量，再在招募、审批和样本方案落实后开展学生试点。

---

## 二、项目定位与研究问题

### 2.1 要解决的问题

通用对话模型能够回答问题，但未必能持续估计学生对具体知识点的掌握状态，也未必会因学生的连续错误、提示历史或证据不足调整教学方式。DeepProf 研究并实现一种显式、可解释的教学决策机制，验证学情信息和教材证据是否能改善教学行为与学生学习结果。

项目的核心差异在于教学决策如何产生、依据什么状态产生、如何约束执行，以及怎样用实验检验其效果。

### 2.2 研究问题与假设

| 研究问题 | 假设 | 主比较 | 主要证据 |
|---|---|---|---|
| RQ1：显式教学策略图能否提升教学行为的稳定性与适切性？ | H1：相较普通教学 Prompt，Pedagogical Graph 降低提示或追问阶段的答案泄露，并提高策略合规和教师评定的干预适切性。 | A 对 B | Answer Leakage Rate、Pedagogical Compliance、Appropriate Intervention Rate |
| RQ2：BKT 学情估计能否改善个性化干预？ | H2：在同一策略图下加入 BKT 与学习历史，可提高后续独立作答率，并减少无效提示。 | B 对 DeepProf | Independent Solve Rate、Hints Before Success、学生前后测增益 |
| RQ3：教材 RAG 与证据约束能否提高教学可信度？ | H3：可定位证据约束提高引用准确率，并在检索证据不足时减少无依据教学陈述。 | 固定案例上的 RAG/约束消融 | Citation Accuracy、Unsupported Claim Rate、Recall@k、Insufficient Evidence 行为 |
| RQ4：声明式决策和事件轨迹能否支持复核与策略比较？ | H4：对固定输入、状态和版本进行回放，可重建策略选择及执行结果，并定位不同策略造成的行为差异。 | 固定案例跨策略回放 | 决策解释完整率、回放一致率、教师可审查性 |

RQ4 的离线回放只支持“可追溯、可复核、可比较”的结论。它不能单独证明真实学生学得更多。

---

## 三、研究贡献与范围分级

### 3.1 核心研究贡献

1. **可解释、可审计的教学决策**：教学图输出带有动作、状态快照、证据状态和理由码的声明式决策；事件轨迹区分“决定做什么”与“执行实际做了什么”。
2. **BKT 驱动的渐进式干预**：把知识点掌握概率、作答历史、连续错误和提示历史映射到教学动作、提示强度和测验难度。
3. **可回放的教学过程评测**：固定案例、课程材料、策略版本和模型配置，比较不同决策策略；通过事件记录复现执行过程。

### 3.2 能力分级

| 级别 | 能力 | 验收用途 |
|---|---|---|
| 核心 | 数据结构课程包、教材 RAG、Pedagogical Graph、BKT 基线、CLI、Gateway/API、本机只读轨迹回放、事件审计、A/B/DeepProf 离线实验 | 完成研究闭环并报告可复现结果 |
| 支撑 | Session/Event 持久化、Attempt 与 LearnerEstimate 契约、单一固定 Provider Profile、基础 SQLite 与向量索引、配置和健康检查、架构守卫 | 保证核心实验可重现、可恢复、可审查 |
| 拓展 | 多 Provider 路由与自动降级、Plugin Runtime、复杂 Sandbox、IRT/DKT 等知识追踪、长期语义/情节/情感记忆、爬虫、Campus 部署、桌面工作台、桌宠、语音、PaperReader、高级资源管理、Rust 优化 | 不进入核心实验验收条件 |

多模型 Profile 的配置和密钥隔离设计继续保留；实验运行固定 Provider Profile 和模型，避免条件漂移。BKT 所需的学习历史属于核心实验数据，不等同于一般长期记忆系统。

---

## 四、试点课程与数据包

### 4.1 冻结课程范围

首期试点课程为《数据结构》，只覆盖四个模块：

1. 线性表：线性表抽象、顺序表、链表、栈、队列及典型应用。
2. 树：树与二叉树、遍历、二叉搜索树、堆与优先队列。
3. 图：图表示、BFS、DFS、连通性、最短路径、最小生成树、拓扑排序。
4. 排序：稳定性与复杂度、插入排序、选择排序、冒泡排序、归并排序、快速排序、堆排序。

首期目标约 30 个知识点，允许教师审核后在 25–35 个范围内调整。每个知识点必须有唯一 concept_id、前置关系、教学目标和难度等级。冻结后的课程清单和映射表按版本管理。

首轮知识点清单冻结为下表 30 项。指导教师可以在 M0 阶段调整术语或前置边，但调整后须发布新的课程版本并同步题目与评测案例。

| 模块 | concept_id | 知识点 | 前置知识 | 难度 |
|---|---|---|---|---|
| 线性表 | DS-LIN-01 | 线性表 ADT 与基本操作 | 无 | 1 |
| 线性表 | DS-LIN-02 | 顺序表的存储、访问、插入与删除 | DS-LIN-01 | 1 |
| 线性表 | DS-LIN-03 | 动态数组扩容与均摊复杂度 | DS-LIN-02 | 2 |
| 线性表 | DS-LIN-04 | 单链表的查找、插入与删除 | DS-LIN-01 | 2 |
| 线性表 | DS-LIN-05 | 双向链表与循环链表 | DS-LIN-04 | 2 |
| 线性表 | DS-LIN-06 | 栈及其典型应用 | DS-LIN-01 | 1 |
| 线性表 | DS-LIN-07 | 队列、循环队列及其典型应用 | DS-LIN-01 | 2 |
| 线性表 | DS-LIN-08 | 线性结构的选型与操作复杂度比较 | DS-LIN-02、DS-LIN-04、DS-LIN-06、DS-LIN-07 | 2 |
| 树 | DS-TREE-01 | 树的术语、性质与存储 | DS-LIN-01 | 1 |
| 树 | DS-TREE-02 | 二叉树性质与链式/顺序表示 | DS-TREE-01 | 1 |
| 树 | DS-TREE-03 | 二叉树先序、中序、后序遍历 | DS-TREE-02 | 2 |
| 树 | DS-TREE-04 | 二叉树层序遍历 | DS-TREE-02、DS-LIN-07 | 2 |
| 树 | DS-TREE-05 | 二叉搜索树的查找、插入与删除 | DS-TREE-02、DS-TREE-03 | 2 |
| 树 | DS-TREE-06 | 堆与优先队列 | DS-TREE-02、DS-LIN-07 | 3 |
| 图 | DS-GRAPH-01 | 图的基本术语及邻接矩阵/邻接表 | DS-LIN-01 | 1 |
| 图 | DS-GRAPH-02 | 广度优先搜索 BFS | DS-GRAPH-01、DS-LIN-07 | 2 |
| 图 | DS-GRAPH-03 | 深度优先搜索 DFS | DS-GRAPH-01 | 2 |
| 图 | DS-GRAPH-04 | 连通性与连通分量 | DS-GRAPH-02、DS-GRAPH-03 | 2 |
| 图 | DS-GRAPH-05 | 无权图最短路径与 BFS 距离 | DS-GRAPH-02 | 2 |
| 图 | DS-GRAPH-06 | 非负权图最短路径与 Dijkstra | DS-GRAPH-01、DS-TREE-06 | 3 |
| 图 | DS-GRAPH-07 | 最小生成树与 Prim/Kruskal 思路 | DS-GRAPH-01、DS-TREE-06、DS-SORT-01 | 3 |
| 图 | DS-GRAPH-08 | 有向无环图与拓扑排序 | DS-GRAPH-01、DS-GRAPH-03 | 3 |
| 排序 | DS-SORT-01 | 排序复杂度、稳定性与原地性 | DS-LIN-02 | 1 |
| 排序 | DS-SORT-02 | 插入排序 | DS-SORT-01 | 1 |
| 排序 | DS-SORT-03 | 选择排序 | DS-SORT-01 | 1 |
| 排序 | DS-SORT-04 | 冒泡排序 | DS-SORT-01 | 1 |
| 排序 | DS-SORT-05 | 希尔排序 | DS-SORT-02 | 2 |
| 排序 | DS-SORT-06 | 归并排序 | DS-SORT-01、DS-TREE-03 | 2 |
| 排序 | DS-SORT-07 | 快速排序与划分过程 | DS-SORT-01、DS-TREE-03 | 2 |
| 排序 | DS-SORT-08 | 堆排序 | DS-SORT-01、DS-TREE-06 | 3 |

难度采用 1–3 级，代表入门、综合和进阶，不直接等同于学生能力。该清单是首轮课程图谱；具体教材章节、题目和学习目标在 M0 按教师审核结果绑定。

### 4.2 教材和题库准入

- 教材由指导教师确认具体书名、作者、版本、章节页码、来源、许可证/授权状态和可用于本项目的范围。
- 题目须有来源、知识点、难度、标准答案与评分依据。需要人工判分的题目不得自动写入 BKT 的正确/错误 Attempt。
- 无法确认许可、来源或页码定位的材料不进入共享课程语料。
- 教材正文只在检索和生成当次使用；跨层传递和事件回放使用可定位引用，不复制原文。

### 4.3 数据文件与字段

课程数据建议按课程版本保存在 data/course_data_structures/：

| 数据对象 | 最低字段 |
|---|---|
| Concept | concept_id、name、module、prerequisites、difficulty、learning_objectives、source_refs |
| Question | item_id、concept_ids、question_type、difficulty、stem、answer_key、solution、grading_rubric、source_ref、version |
| Evidence | document_id、chapter、page、chunk_id、source、license、content_hash、text_ref |
| ResourceRecord | resource_id、course_id、type、tags、source_type、source_url、license、hash、status、created_by、updated_at |
| TeachingCase | case_id、concept_id、initial_state、attempt_sequence、hint_history、evidence_status、expected_actions、case_version |
| Annotation | case_id、rater_id、action_label、appropriateness、answer_leaked、citation_supported、comment、annotation_version |
| Attempt | attempt_id、learner_id、item_id、concept_ids、scored_concept_id、correct、timestamp、hint_count、grading_source、confidence |

原始学生身份信息不进入教学案例集。用于公开或团队共享的案例需脱敏；真实学生记录应与课程公开材料分开存储。

---

## 五、教学闭环与策略规则

### 5.1 决策循环

1. **Assess**：识别当前知识点、任务类型、证据需求和学情信息是否充分。
2. **Recall**：按 learner_id 和 concept_id 读取允许用于本轮决策的学习状态；只取必要字段。
3. **Decide**：规则和策略图根据 BKT、错误模式、提示级别及证据状态生成 PedagogicalDecision。
4. **Intervene**：Runtime 根据绑定表调用确定性模板、检索、技能或模型。
5. **Observe**：记录学生作答、提示次数、判分来源、能力执行状态与引用。
6. **Update**：只有归属明确、题目可追踪、判分可靠的作答才更新 BKT；其他情况记录跳过原因。
7. **Replay/Evaluate**：按固定版本重放离线案例，汇总教育指标和失败样例。

### 5.2 可执行的初始策略

以下掌握度区间是离线起始规则，来自指导意见的示例。正式用于学生试点前，指导教师需审核动作与阈值，形成 policy_version 并冻结。

| 状态 | 初始动作 |
|---|---|
| 无估计或证据量不足 | 先做低风险诊断或 Guided Ask；不得把缺少数据写成低掌握度 |
| P(K) < 0.30 | Teach：先讲必要概念并给出可定位教材证据 |
| 0.30 ≤ P(K) < 0.60 | Guided Ask 或低级 Hint：给步骤线索，要求学生继续推理 |
| 0.60 ≤ P(K) < 0.85 | Socratic Ask：用问题检查概念关系和推理过程 |
| P(K) ≥ 0.85 | Test 或 Transfer Question：验证掌握并检查迁移 |
| 连续答错或出现概念混淆 | Correct：指出错误类型并用对比例说明；保留一次重新作答机会 |
| 证据不足 | 停止教材事实性讲解，明确说明当前无可定位依据；可澄清问题或请求教师材料 |
| 多轮无进展 | Reflect：降低难度、切换策略或结束本轮；不得无限追问 |

提示按 policy_version 中的固定模板逐级增加信息量，Hint 阶段不得给最终答案。MVP 默认每题最多三级提示；达到上限仍未成功时转 Reflect，并记录终止原因。教师可以在离线校准中调整上限，调整后增加策略版本。

策略冲突按以下顺序处理：证据不足时先执行证据保护；学生停止或达到回合上限时结束/Reflect；wrong_streak ≥ 2 且证据充分时优先 Correct 并检查误解；其他情况下按 P(K) 区间选择基础动作。Hint 的等级由本题已给提示数决定：0 次为 level 1、1 次为 level 2、2 次为 level 3；已给 3 次仍未成功则转 Reflect，不再输出 Hint。Ask 与 Hint 的 reveal_answer 恒为 false。每个动作记录命中的规则及其输入状态。

测验难度由知识点难度标签与掌握度区间共同决定。BKT 的估计必须携带模型版本、证据数量和不确定性；不同知识点概率不可直接求和为一个未经定义的总分。

### 5.3 BKT 基线契约

首期采用每个知识点独立的标准四参数 BKT，不启用遗忘项。参数为初始掌握概率 P(L0)、学习转移概率 P(T)、猜测概率 P(G) 和失误概率 P(S)。每个 concept_id 保存参数来源、拟合数据版本、估计日期和 model_version。作答前的预测正确概率为 p × (1 − P(S)) + (1 − p) × P(G)，用于计算预测指标。

令 p 为本次作答观察前的掌握概率。正确作答后的观测后验为：

P(L | correct) = p × (1 − P(S)) / [p × (1 − P(S)) + (1 − p) × P(G)]

错误作答后的观测后验为：

P(L | incorrect) = p × P(S) / [p × P(S) + (1 − p) × (1 − P(G))]

若本次 Attempt 可用于更新，完成观测更新后再执行学习转移：

P(L next) = P(L | observation) + [1 − P(L | observation)] × P(T)

MVP 优先使用训练数据拟合参数；样本不足时使用指导教师审核并冻结的参数配置，明确标记为初始化参数，不宣称已经校准。测试集和学生试点结果不得用于反向调参。MVP 每道计入 BKT 的题目必须有一个明确的 scored_concept_id；多知识点题只有在评分量规能分别给出各知识点结果时才更新对应状态，否则只用于综合评测，不进入知识点 BKT 更新。原始 BKT 定义见 [Corbett 与 Anderson（1995）](https://doi.org/10.1007/BF01099821)，实现参考 [pyBKT](https://github.com/CAHLR/pyBKT)。

### 5.4 教师可审查材料

教学策略由两部分组成：policies/ 中的阈值、动作条件和可见文案；bindings 中动作到通用执行能力的映射。指导教师可不阅读图节点实现，直接审查策略版本及样例输入输出。

---

## 六、研究设计与评价指标

### 6.1 三组对照

| 组别 | 系统条件 | 用途 |
|---|---|---|
| Baseline A | 相同教材 RAG + 普通“苏格拉底教师”教学 Prompt；无教学策略图、无学情模型 | 对照通用提示词教学 |
| Baseline B | 相同教材 RAG + Pedagogical Graph；无 BKT 学情状态和跨题学习历史 | 检验显式策略图增益 |
| DeepProf | 相同教材 RAG + Pedagogical Graph + BKT + 必要的学习历史 | 检验学情驱动干预的增量 |

三组固定课程版本、题目、教材语料、检索配置、模型及 Provider Profile、模型参数、会话时长和前后测。每次运行记录实验组、模型、策略、语料和评分规则版本。实验期间不得静默切换模型或检索配置。

RAG 证据约束另在固定离线案例上做消融：比较“有/无证据约束”和“有/无可定位检索结果”。该消融回答可信度问题，不混入三组的主学习效果比较。

### 6.2 离线案例评测

- 建立 30–50 个教师标注案例，覆盖冷启动、先验不足、连续答错、提示后成功、证据不足、误解概念、主动请求讲解和多轮无进展。
- 在尚无招募条件时，案例中的状态和作答序列是教师构造的离线 fixture；不得报告为真实学生数据或学习效果。
- 每个案例固定输入、Attempt 序列、初始 LearnerEstimate、证据检索结果、预期动作和允许的变体。
- 同一案例在 A、B、DeepProf 条件下运行；固定模型和随机参数。保存配置、事件轨迹、完整输出和标注结果。
- 教师标注至少覆盖动作适切性、答案泄露、引用支持和不确定性处理。建议双人独立标注；报告一致性和分歧仲裁方法。
- 离线案例数量较少时，以比例、置信区间和失败样例为主，不宣称统计显著或普遍学习效果。
- 决策回放须固定代码版本、策略版本、输入状态、模型配置和检索语料。带生成模型的输出未必逐字确定；分别报告“决策轨迹一致性”和“生成文本一致性”。

### 6.3 指标定义

| 维度 | 指标及计算口径 |
|---|---|
| 教学策略 | Pedagogical Compliance = 符合冻结策略和转移条件的回合数 / 可判定策略回合数 |
| 答案泄露 | Answer Leakage Rate = 在 Ask/Hint 阶段被标注为泄露最终答案的回合数 / 被评审的 Ask/Hint 回合数 |
| 干预适切性 | Appropriate Intervention Rate = 教师标注为适切的决策数 / 被评审决策总数 |
| 学习效果 | 个体 Pre/Post Gain = 有效后测分数 − 有效前测分数；组内报告具有成对有效测验的参与者平均变化、分布和区间估计，并单列仅完成前测、仅完成后测及两测均缺失人数；前后测采用教师审核的等值题卷，报告总分与知识点分项 |
| 独立解决 | Independent Solve Rate = 在规定探测题上不需新增提示而正确完成的人次 / 完成该探测题的人次；同时报告未完成比例 |
| 提示效率 | Hints Before Success = 成功作答前的提示数均值/中位数；未成功案例单独报告，不从分母中隐去 |
| 引用可信度 | Citation Accuracy = 被教材证据实际支持的引用陈述数 / 抽查引用陈述数；同时报告可定位引用比例 |
| 无依据陈述 | Unsupported Claim Rate = 无证据支持的事实性教学陈述数 / 抽查事实性陈述数 |
| 检索 | Recall@k = 前 k 个检索结果中包含教师标注相关证据的查询数 / 有相关证据的查询数；无相关证据查询另报 |
| BKT 预测 | 以合格 Attempt 的作答前预测概率和可靠 correct 标签为单位，报告 AUC、LogLoss、Brier Score 与 10 个等宽概率箱的 ECE（各非空箱按样本占比加权的平均置信度—准确率绝对差）；同时报告样本量、正负例比例、知识点覆盖和拆分方式 |
| 决策审计 | Explanation Completeness = 含动作、policy_version、关键状态依据、证据状态和执行状态的决策数 / 决策总数 |
| 回放 | Replay Decision Agreement = 重放后决策与冻结期望决策一致的次数 / 可判定重放次数；执行与生成误差分开统计 |

AUC 在单一类别数据上不计算。数据拆分以学习者为单位并遵循时间顺序，禁止同一学习者或未来作答同时泄漏到训练和测试。任何指标均需保存原始计数和计算脚本版本。

### 6.4 真实学生试点

当前尚未落实招募条件。真实学生实验是后续阶段，进入前须完成：

1. 指导教师批准课程、题目、干预规则、数据字段和风险说明。
2. 确认教材/题库使用许可及实验场地、招募渠道和退出流程。
3. 按适用的学校要求完成研究审批或伦理审查；取得参与者知情同意。
4. 在招募前根据可用人数和研究目标确定样本方案、随机分配方法、前后测题卷和分析计划；预先登记主要指标。
5. 优先采用三组平行设计，并按前测水平分层随机。各组使用相同课程、题库、材料、模型、时长和测验安排。
6. 参与者可随时退出；不采集非必要身份信息。研究数据使用编码 learner_id，访问权限受限，并按审批方案规定保存和删除。
7. 若实际样本不足以支持预设比较，结果定位为可行性试点和描述性统计，不扩大结论。

报告只描述实际招募、分组、退出、缺失数据和结果。离线回放、教师评分和真实学生学习效果分别报告。

---

## 七、总体架构与数据流

### 7.1 教育闭环图

~~~mermaid
flowchart LR
    A[学生提问或作答] --> B[课程知识点与证据定位]
    B --> C[学情估计 BKT]
    C --> D[策略决策]
    D --> E[讲解 Teach]
    D --> F[追问 Ask]
    D --> G[提示 Hint]
    D --> H[纠错 Correct]
    D --> I[测验 Test]
    E --> J[学生反馈与 Attempt]
    F --> J
    G --> J
    H --> J
    I --> J
    J --> C
    D --> K[决策和执行事件]
    K --> L[离线评测与只读回放]
~~~

### 7.2 技术架构图

~~~mermaid
flowchart LR
    CLI[DeepProf CLI] --> SDK[Client SDK]
    SDK --> GW[Gateway / API]
    GW --> SES[Session 与 Event Store]
    GW --> GRAPH[Pedagogical Graph]
    GRAPH --> DEC[PedagogicalDecision]
    DEC --> PORT[RuntimePort execute / emit]
    PORT --> DISP[Runtime Dispatcher]
    DISP --> BIND[组合根注入的动作绑定表]
    BIND --> HOST[RuntimeHost]
    HOST --> CAP[通用能力与 Skills]
    CAP --> RAG[课程 RAG 与检索]
    CAP --> PROV[固定 Provider Profile]
    CAP --> MEM[BKT 状态与受控学习历史]
    HOST --> RESULT[CapabilityResult]
    RESULT --> GRAPH
    SES --> VIEW[本机只读回放页]
    GRAPH --> EVT[决策、Attempt 与错误事件]
    EVT --> SES
~~~

浏览器回放页不执行学习命令，不更改策略、学情或 Provider 配置。它只通过 Gateway 的只读接口查看脱敏轨迹与评测结果。

### 7.3 核心层职责

| 层 | 职责 |
|---|---|
| CLI | 学生交互、课程选择、提问、答题、Session 恢复、查看引用和教学动作 |
| Gateway/API | 命令校验、Session 接入、事件流、只读回放查询、健康检查 |
| Pedagogical Graph | 维护可序列化教学状态、按冻结策略选择动作、输出声明式决策 |
| Runtime | 执行绑定能力、调用 Provider/Skill/Tool/Memory，规范化结果与事件 |
| 课程资源与 RAG | 管理获准的课程材料，返回可定位证据及检索状态 |
| Learner Model | 消费合格 Attempt，输出带模型版本和不确定性的知识点掌握估计 |
| Evaluation | 固定离线案例、组别配置、指标口径、标注结果和报告 |

---

## 八、D-7 契约与关键数据接口

### 8.1 不可破坏的职责边界

- 教学图只回答“此刻应采取什么教学动作”，不直接调用模型 SDK、工具、数据库、Skill 或沙箱。
- Runtime 只执行声明式请求，不决定“是否应该提示或测验”，也不依赖教学动作词汇。
- 绑定表由组合根注入，把 action 映射到通用 capability 和执行参数。
- RuntimePort 是图唯一可依赖的接口，只有 execute(request, ctx) 与 emit(event)。
- RuntimeHost 提供能力实现所需的宽接口；仅受信 Skill 在明确声明需要时获得它。
- 更换模型、检索器或 Skill 实现不得隐式改变冻结策略。

### 8.2 核心契约

| 契约 | 字段/职责 |
|---|---|
| PedagogicalDecision | action、concept、level、require_evidence、evidence_sufficient、reveal_answer、reason_codes、params、policy_version；params 仅携带本次执行需要的输入 |
| RuntimePort | execute 与 emit；图侧窄接口 |
| RuntimeHost | invoke_skill、call_tool、generate、read_memory、write_memory；能力实现侧宽接口 |
| CapabilityResult | content、evidence_refs、records、status、capability、action、error、metadata |
| Attempt | attempt_id、learner_id、item_id、concept_ids、scored_concept_id、correct、timestamp、hint_count、grading_source、confidence |
| LearnerEstimate | learner_id、concept_id、model_type、model_version、mastery_probability、evidence_count、uncertainty、updated_at |
| EvidenceRef | document_id、chapter、page、chunk_id、source、license_ref；跨策略图只传引用 |
| ClientCommand | command_id、client_id、surface、session_id、learner_id、type、payload、timestamp |
| RuntimeEvent | event_id、session_id、trace_id、sequence、type、payload、source、timestamp、policy_version、model_version |

只有归属明确、题目可追踪、判分可靠的结果生成可用于 BKT 更新的 Attempt。不满足条件时不推测 correct 值；记录 attempt_skipped 及原因。

### 8.3 失败状态

| 情况 | 处理 |
|---|---|
| 无 action 绑定 | 返回 no_binding；不生成自由文本掩盖装配错误 |
| 绑定指向未知 capability | 返回 capability_not_found 与注册能力清单 |
| 决策参数缺失或类型错误 | 返回 invalid_request 与缺失字段 |
| 证据要求未满足 | 返回 insufficient_evidence；不调用生成模型补写教材事实 |
| Provider 不支持必要能力 | 返回 provider_capability_missing；实验组保持原模型配置 |
| 工具或 Skill 失败 | 返回结构化错误并记录 trace；只允许策略表中明确声明的回退 |
| 图循环超限 | 转 Reflect 或结束本轮，记录终止原因 |
| 学情冷启动 | 显式返回信息不足并使用教师审核的保守策略 |

---

## 九、Runtime、事件、记忆与 Provider

### 9.1 Runtime Core

Runtime Core 保留 Agent 生命周期、Session、Event、Message、Tool、Provider 和 Capability Dispatcher。Core 保持轻量、可测试，并与教学图解耦。Session 支持创建、恢复、分支和压缩；EventStore 支持追加、订阅和按 sequence 回放。

### 9.2 事件审计

核心事件类型包括：

- session.started、session.resumed、session.compacted、session.ended
- agent.turn.started、agent.turn.completed、agent.failed
- model.requested、model.stream.delta、model.completed、model.failed
- tool.requested、tool.approved、tool.started、tool.completed、tool.failed
- memory.read、memory.write
- pedagogy.node.entered、pedagogy.decision、pedagogy.node.exited
- pedagogy.attempt、pedagogy.attempt_skipped
- library.imported、library.indexed

pedagogy.decision 至少记录 action、reason_codes、policy_version、BKT model_version、掌握估计引用/摘要、attempt_count、wrong_streak、hint_level、evidence_sufficient、evidence_refs、实际 capability、capability_status、实验组和 case_id（适用时）。该事件不得携带学生原始正文、API Key 或不必要的个人信息。Session 正文如需保存，必须与决策审计字段分开管理并遵循隐私策略。

Event 采用追加写入，包含 event_id、session_id、trace_id、sequence、timestamp、type、payload 和 source。重复 event_id 幂等处理；重连按 sequence 续传。日志落盘前清除 Secret 和敏感字段。

### 9.3 Memory 边界

- Session Log 保存交互事实，服务恢复、审计和回放。
- Working Memory 保存当前任务和短期上下文。
- 学情记录保存 BKT 所需的 Attempt 与 LearnerEstimate，是核心实验所需的最小学习历史。
- Episodic、Semantic、Affective Memory 作为拓展；每项长期记录需有来源、置信度、有效期和可撤回/删除标记。
- 学生可查看、纠正、删除或关闭可选记忆；学情估计不得成为永久标签。

### 9.4 Provider Hub

保留统一 Provider Registry、OpenAI-compatible Adapter、Capability 探测、Secret 引用与结构化错误。Profile 至少包含 profile_id、display_name、protocol、base_url、api_key_ref、default_model、models、api_mode、extra_headers、timeout_ms、max_retries、capabilities 和 enabled。

- MVP 实验固定一个模型、一个 Profile 和生成参数；运行时记录实际 Profile 与模型。
- 实验链路禁止静默 fallback。需要切换时必须显式配置并写入事件，受影响的运行单独标记。
- API Key 只存在系统凭据库或受控 Secret Adapter；不得进入 CLI 历史、Renderer、Session Event、Graph State、错误堆栈或仓库。
- Provider 健康检查使用固定最小探测请求，不发送学生对话内容。

---

## 十、CLI 与本地只读回放服务

### 10.1 CLI 作为学生主入口

CLI 经 Client SDK 访问 Gateway，不建立第二套 Agent Runtime。首批命令：

| 命令 | 行为 |
|---|---|
| login / models / doctor | 配置或检查模型接入；凭据不显示在历史输出 |
| course list / course use | 查看和选择冻结课程 |
| new / ask | 创建 Session、提问并流式接收响应 |
| answer / hint / quiz | 提交作答、请求下一层提示或开始测验 |
| resume / tree / fork / compact | 恢复、查看分支、创建分支和压缩 Session |
| sources | 查看本轮可定位教材引用 |
| trace | 输出脱敏的当前回合决策摘要 |
| export | 导出获准的脱敏记录 |

交互始终显示当前课程、Session、模型 Profile、学情冷启动状态和本轮引用状态。工具审批、取消、错误及 sequence 重连通过共享 Client SDK 契约处理。

### 10.2 本地只读回放页

Gateway 在本机环回地址提供只读浏览页面，展示：

- Session/trace 时间线和节点顺序；
- 教学动作、状态依据、policy_version、BKT model_version；
- 引用定位、证据充分度、能力执行状态和错误；
- 实验组、固定案例和离线指标结果。

回放页不提供作答、改策略、改 Profile、导入资源或删除数据的写操作。接口使用只读路由；默认仅绑定 127.0.0.1，学生正文脱敏或按权限隐藏，Secret 永不呈现。该页面是评测与审查工具，不构成第二个学生学习前端。

---

## 十一、工程目录与依赖边界

~~~text
DeepProf/
├── apps/
│   ├── cli/                    # 学生主入口
│   └── replay_web/             # 本机只读回放页
├── packages/
│   ├── client_sdk/             # Session、Event、Command 客户端
│   └── contracts/              # 共享 schema
├── api/                        # Gateway、Session、Events、Replay、Health
├── runtime/
│   ├── core/                   # Agent、Session、Event、Message、Port
│   ├── capabilities.py         # Dispatcher 与通用执行原语
│   ├── providers/              # Profile、Adapter、Secret 引用
│   ├── memory/                 # BKT 所需作答/画像接口及拓展记忆
│   ├── storage/                # SQLite 等存储适配
│   ├── plugins/                # 拓展
│   ├── sandbox/                # 拓展
│   └── testing.py              # FakeRuntime、RecordingHost
├── graph/
│   └── education/
│       ├── state.py
│       ├── contracts.py
│       ├── router.py
│       ├── bindings.py
│       ├── builder.py
│       ├── nodes/
│       └── policies/            # 可审查的阈值、文案、版本
├── models/
│   └── learner/                 # Attempt、LearnerEstimate、BKT
├── skills/
│   ├── rag/
│   ├── socratic/
│   ├── quiz/
│   └── diagnosis/
├── library/                     # 课程资源、解析、切块、检索索引
├── data/
│   └── course_data_structures/  # manifest、concepts、questions、cases、annotations
├── evaluation/
│   ├── cases/
│   ├── offline/
│   ├── student_pilot/
│   ├── metrics/
│   └── reports/
├── tests/
│   ├── runtime/
│   ├── graph/
│   ├── contracts/
│   ├── evaluation/
│   └── architecture/
└── docs/
    ├── DESIGNv0.6.2.md
    └── archive/                 # 旧版设计与验收记录
~~~

### 11.1 依赖规则

- apps/cli 和 apps/replay_web 只依赖 Client SDK / Gateway 契约，不导入 Runtime 内部实现。
- replay_web 的后端路由只读；不得调用 RuntimeHost。
- graph/education 只依赖 RuntimePort 的 execute/emit；不得直接导入 Provider、Skill、Library、数据库或 Memory Store。
- runtime 不导入 graph/education；动作词汇和教学阈值留在策略层与绑定数据中。
- Skill 只有声明需要 RuntimeHost 且经装配授权后才能访问模型、工具或记忆。
- library 只负责来源、解析、索引和可定位引用；不得修改图状态或教学策略。
- Provider Secret 只经 Secret Adapter 访问；普通配置和事件只保存 api_key_ref。
- Plugin 和 Sandbox 不可被核心教学图直接依赖；关闭全部插件后核心闭环仍能运行。

架构守卫至少覆盖 Runtime 教学词汇扫描、图节点宽面访问、RuntimePort 窄面、前端导入边界、Secret 泄露、回放只读、课程资源来源/许可字段和 Attempt 判分条件。

---

## 十二、MVP、交付路线与验收

### 12.1 核心 MVP 验收范围

核心 MVP 包含一个获准的数据结构课程包、可定位教材 RAG、Pedagogical Graph、BKT 基线、作答与决策事件、CLI、Gateway/API、本机只读回放页和离线三组评测。插件、复杂沙箱、桌面工作台、桌宠、语音、多 Provider 自动路由、IRT/DKT 不得阻塞该范围。

### 12.2 五阶段交付

| 阶段 | 主责 | 交付物 | 进入下一阶段条件 |
|---|---|---|---|
| M0：课程与评测材料冻结 | 许阳毅、孙一新、欧阳文凯；唐欢容审核 | 课程 manifest、25–35 个知识点、获准教材与题库、30–50 个案例、指标手册、策略初版 | 来源/许可可核验；知识点和题目映射通过教师审核；三组配置和标注规则冻结 |
| M1：最小闭环与三组接入 | 刘俊鹏、孙一新、张钧翔 | RuntimePort、绑定表、Graph、CLI、Gateway、只读回放、Baseline A/B 可运行 | 固定案例可端到端运行；证据不足不调用无依据生成；Session 可恢复；关键架构守卫通过 |
| M2：BKT 与学习历史 | 欧阳文凯、谢浪、孙一新 | Attempt 管线、BKT、LearnerEstimate、版本化策略路由 | 固定 Attempt 序列更新可复现；低样本和不可靠判分路径有明确行为；C 组使用学情状态且 A/B 不读取该状态 |
| M3：离线实验和 RAG 消融 | 欧阳文凯、孙一新、许阳毅；唐欢容审核 | A/B/DeepProf 离线结果、RAG 消融、教师评分、一致性与失败案例 | 输入和版本可追溯；指标有分子分母；未把离线回放写成真实学习增益 |
| M4：真实学生试点 | 唐欢容、欧阳文凯及全组 | 审批材料、招募/同意材料、分组与前后测方案、脱敏数据和试点报告 | 具备招募、许可、审查和样本方案；完成后如实报告退出、缺失和局限 |

阶段可并行准备，但 M4 的参与者数据采集必须等待审批与同意流程完成。

### 12.3 新旧 MVP 对照

v0.6.1 的 MVP-1 至 MVP-5 是历史工程模块划分。v0.6.2 将其映射至上述研究阶段：Runtime/契约进入 M1；Graph、教育能力和 BKT 分别进入 M1/M2；RAG 与资源库进入 M0/M1；CLI 进入 M1；桌面端与桌宠移至拓展章节。旧验收记录保留为证据来源，不自动等同于本版本研究验收。

---

## 十三、团队分工

| 成员/角色 | v0.6.2 主责 | 关键交付 |
|---|---|---|
| 刘俊鹏，项目负责人 | Runtime、契约、Gateway 装配、固定实验配置与集成 | D-7 契约、端到端运行、版本冻结、集成记录 |
| 许阳毅，课程证据与 RAG | 数据结构课程材料、合法来源、切块、检索和引用 | course manifest、EvidenceRef、检索评测与失败样例 |
| 孙一新，教学策略 | Graph、策略规则、动作绑定、离线案例设计 | policies、PedagogicalDecision、教师审查案例 |
| 张钧翔，CLI 与回放 | CLI 全流程、本机只读回放页和事件展示 | 学生交互、只读页面、重连及脱敏展示 |
| 谢浪，事件与数据 | Session/Event 存储、Attempt 生命周期、备份和删除 | 幂等写入、learner_id 隔离、数据审计 |
| 欧阳文凯，BKT 与评测 | BKT 基线、离线/学生实验统计、指标计算 | 模型版本、评测脚本、结果表和分析计划 |
| 刘雨烟 | 计划书商业部分 | 商业模式、目标用户、成本口径；不负责 DESIGN 架构 |
| 唐欢容，指导教师 | 课程、题库、策略、标注和实验把关 | 课程批准、策略审阅、实验审批与阶段验收意见 |

所有模块交接包含版本、启动方式、输入输出样例、预期结果、实际结果、失败样例和责任人。姓名与职责以团队正式分工为准。

---

## 十四、部署、安全、隐私与教育伦理

### 14.1 MVP 部署

MVP 采用本机运行：CLI → 本机 Gateway/API → Runtime → 本机 SQLite/向量索引与获准课程资料 → 固定 Provider。API 与只读回放服务默认绑定环回地址。SQLite 放 Runtime 所在机器的本地磁盘，不放网络共享目录。

Local 与 Campus 两种模式的 ClientCommand/RuntimeEvent 契约可继续保留；Campus Runtime、校园统一身份、校级资源和集中 Provider 属于拓展部署，待权限、运维和数据治理条件具备后实施。

### 14.2 隐私与安全

- 只采集教学决策和实验所需的最小字段；不主动索取姓名、学号等信息。
- Session 内容、事件轨迹、Attempt、LearnerEstimate 和课程资源分开管理并按 learner_id 隔离。
- API Key 不进入日志、事件、命令历史、错误堆栈或导出文件。
- 学生可停止会话；长期记忆可查看、纠正、删除或关闭。
- 第三方内容、检索结果、上传文件均视为数据，不作为系统指令执行。
- Tool 按 schema 校验、权限审批和审计；Sandbox 作为风险控制措施，不承诺绝对隔离。
- 课程材料只使用公开或已授权来源；爬虫遵守站点条款和 robots 规则，不绕过登录墙、付费墙或 DRM。
- 模型输出不替代教师意见；学情估计展示不确定性，不给学生贴固定能力标签。

### 14.3 实验条件控制

实验配置至少冻结 provider_profile_id、model_id、API 模式、提示模板、温度/采样参数、检索语料版本、向量模型、策略版本、BKT 模型版本、题目版本和代码版本。无法冻结的配置必须写入事件并在分析中标明。

---

## 十五、扩展能力设计

以下能力继承 v0.6.1 的设计意图，服务于核心闭环的后续演进。其实现不属于核心 MVP 的完成门槛。

### 15.1 Desktop 工作台

保留 Electron + React/TypeScript 的薄壳方案：Main 管理窗口、运行进程、Tray、凭据和更新；隔离 Runtime Supervisor 管理 Python Runtime；本机工作台仅监听环回动态端口；Renderer 启用 contextIsolation、sandbox、webSecurity 和最小 Preload IPC 白名单。首启 API Key 接入流程、Provider Settings、课程/资源库面板及 Session/Trace 视图继续作为未来工作台设计参考。

Desktop Renderer 不持有密钥，不直连 Tool、Memory 或数据库；禁用 webview，限制导航和新窗口。升级不覆盖 Session、Profile、插件和用户数据。安全模式仅加载官方核心组件并保留用户数据。

### 15.2 桌宠与语音

保留 Codex 兼容宠物包设计：pet.json 与 spritesheet.webp；事件驱动的 idle、running、waiting、failed、review、jumping、waving 等状态。桌宠只消费事件投影并发送轻交互，不独立持有 Session、Provider、Memory 或密钥。透明置顶浮窗、点击穿透、托盘控制及轻面板属于后续交互工作。

ASR/TTS 经 Provider Adapter 接入。语音转录进入学生主链路前仍遵循同意和数据最小化要求；语音和桌宠效果若需形成研究主张，必须单独开展 UI 对照实验。

### 15.3 Plugin Runtime 与 Sandbox

保留受控 Python 插件子集。Plugin manifest 声明唯一 id、版本、依赖、能力和权限；生命周期为 install → start → stop → uninstall。插件通过 Service Registry 获取服务，不访问 Runtime 私有状态；文件、网络、进程和密钥权限分别声明。第三方插件默认关闭，插件事件纳入审计。高风险工具经审批并在隔离环境运行；安全模式可在插件故障时关闭第三方组件。

### 15.4 Provider Hub 与多模型

保留 Provider Registry、OpenAI-compatible Profile、能力探测、逻辑模型角色路由、显式 fallback chain、本地模型 Adapter 与 Secret Adapter。Profile 支持 base_url、api_key_ref、default_model、models、api_mode、headers、timeout、retry 和 capability flags。实际调用记录 profile/model 和降级来源，不记录密钥。

能力兼容分为基础文本、流式、Agent 工具/结构化输出及可选 vision/reasoning/embedding。探测失败时只禁用对应能力，不伪装兼容。

### 15.5 学情模型与记忆扩展

BKT 是首期模型。保留统一 LearnerModel 接口，使未来可接 IRT、DKT 或其他知识追踪模型。IRT 需学生—题目作答矩阵和足够样本；在数据不足时不进入主比较。复杂长期语义、情节和情感记忆保留来源、置信度、过期和撤回边界，不与 BKT 的作答序列混为同一模型输入。

### 15.6 资源库与 PaperReader

核心只需一套经教师审核的课程语料和可定位 RAG。保留资源库后续扩展：PDF、EPUB、DOCX、PPTX、Markdown、TXT 导入；章节/页码解析、切块、向量索引、SHA-256 去重、版本回滚、标签归档、学生私有上传和教师审核。爬虫限白名单域、按域限速并限制并发，记录 source_url、抓取时间、许可和哈希，并遵守 robots/ToS。

PaperReader、跨课程资源库、学生上传共享、批量爬取和 OCR 属于拓展。教材原文只供能力层本次检索/生成；Graph、事件和回放接口保存定位引用与必要元数据。

### 15.7 Campus 与辅助实现

保留 Local/Campus 双部署思路、学校 Gateway、校内 Learner Store、校园模型网关和权限控制。Campus 模式须明确数据存储位置、处理方和 Provider 去向。Rust 只在性能测量证明需要时考虑；Pi RPC 只作为开发者集成，不替代 DeepProf Runtime，也不写学生学习记忆。

### 15.8 v0.6.1 扩展设计保留清单

| v0.6.1 设计 | v0.6.2 保留内容 | 级别 |
|---|---|---|
| Runtime Core | Agent Loop、Session load/append/fork/compact、追加式 Event Stream、Message、Tool schema 与 Provider Adapter | 支撑 |
| Pedagogical Graph | Assess、Teach、Ask、Hint、Correct、Test、Reflect、UpdateProfile 节点及策略路由 | 核心；围绕冻结实验范围实现 |
| Education Skills | Socratic、RAG、Quiz、Diagnosis、PaperReader；Skill 不持有全局 Session，通过 RuntimeContext 和授权 Host 访问服务 | RAG/Socratic/Quiz/Diagnosis 为核心所需子集；PaperReader 扩展 |
| Client SDK 与事件 | ClientCommand、RuntimeEvent、sequence 重连、取消、审批、Session 恢复和 trace 关联 | CLI 核心所需子集；跨桌面端共享为拓展 |
| Provider Hub | Profile、OpenAI-compatible Adapter、模型能力探测、本地模型 Adapter、Secret 引用和显式 fallback | 单 Profile 支撑；多 Profile 路由拓展 |
| Memory | Session Log、Working、Long-term Learning、Episodic、Affective 分类及来源/置信度/到期/撤回字段 | Attempt 与 LearnerEstimate 核心；其余拓展 |
| Plugin Runtime | manifest、版本/依赖/能力声明、install/start/stop/uninstall 生命周期、Service Registry、插件事件与安全模式 | 拓展 |
| Tools / Storage / Sandbox | 结构化 Tool 参数、权限审批、Runtime 存储接口、SQLite、向量存储、隔离与审计 | 存储/最小权限支撑；通用沙箱拓展 |
| Resource Library | PDF/EPUB/DOCX/PPTX/Markdown/TXT 导入、来源和许可元数据、SHA-256 去重、页码/chunk 定位、版本回滚、白名单抓取 | 单课程审核语料和 RAG 核心；通用导入与抓取拓展 |
| Desktop 工作台 | Electron Main/Preload/Renderer、安全 IPC、Runtime Supervisor、环回服务、Provider Settings、课程和 Sources/Trace 面板、安全模式与更新保护 | 拓展 |
| Pet 与 Voice | pet.json + spritesheet.webp；8 列图集、v1 的 9 行动作、可选 v2 16 向朝向；事件到 idle/running/waiting/failed/review/jumping/waving/running-left/running-right 映射；ASR/TTS Adapter | 拓展 |
| Local/Campus 与 Pi | 本机 SQLite/向量索引、校内 Gateway/Store/Provider 部署；Pi RPC 仅开发者模式且与学生 Memory 隔离 | Local 核心；Campus/Pi 拓展 |
| 质量与安全守卫 | Runtime/Graph 依赖边界、端口窄面、前端不读 Secret、Profile 不存明文密钥、资源来源审计、回放只读、插件权限检查 | 核心和支撑守卫按实际模块启用 |

推荐技术基线沿用 v0.6.1：Python 3.12+ Runtime，FastAPI Gateway/API，LangGraph 教学策略图，TypeScript CLI 与 Client SDK，SQLite 本地事务存储及可替换向量索引，OpenAI-compatible Provider Adapter。桌面端继续采用 Electron + React；它不进入本版核心交付。

---

## 十六、决策记录与版本迁移

### 16.1 既有决策在 v0.6.2 的状态

| 决策 | v0.6.2 状态 |
|---|---|
| D-1 Electron Desktop | 保留为拓展客户端设计，不属于学生核心入口 |
| D-2 试点课程 | 更新为数据结构，四模块、约 30 个知识点；教材版本和许可待教师确认 |
| D-3 Event/Memory/Profile Store 与 SQLite | 保留；MVP 先实现本地核心存储 |
| D-4 Python Plugin | 保留为拓展 |
| D-5 Sandbox 与审批 | 保留为拓展安全设计；Tool 最小权限和审计仍为支撑要求 |
| D-6 BKT/IRT/知识追踪 | BKT 进入核心；IRT 与 DKT 保留为拓展 |
| D-7 PedagogicalDecision、RuntimePort、RuntimeHost、绑定表 | 原样保留，是架构硬约束 |
| D-8 Desktop/CLI/Pet 共享客户端 | 调整为 CLI 核心；Gateway/API 为服务入口；浏览器仅只读回放；Desktop/Pet 为拓展 |
| D-9 Pi 集成 | 保留开发者适配，学生链路不依赖 Pi Agent Core |
| D-10 Provider Hub | 保留为支撑架构；核心实验固定一个 Profile/模型 |
| D-11 Desktop Supervisor 与安全壳 | 保留为拓展 |
| D-12 Local + Campus | 本地优先进入 MVP；Campus 作为部署拓展 |
| D-13 两大模块与桌宠挂件 | 由 v0.6.2 的 CLI + Gateway/API + 只读回放替代核心交互叙事 |
| D-14 不做账号系统、API Key 凭据隔离 | 保留；实验前固定并登记 Provider |
| D-15 资源库 | 核心保留课程语料、来源许可、检索和引用；上传/爬虫全面扩展属于拓展 |
| D-16 Codex 兼容宠物包 | 保留为拓展 |
| D-17 负责人先做演示、再按 MVP-1～5 维护 | 更新为 M0–M4 研究阶段和按闭环交付 |
| D-18 研究主线 | 新增：学情驱动的自适应教学与实验验证 |
| D-19 前端边界 | 新增：CLI 为学生主入口，本地浏览器只读回放 |
| D-20 实验协议 | 新增：固定 A/B/DeepProf 三组、离线评测先行、真实学生试点设准入门槛 |

### 16.2 历史状态核验

v0.6.1 原文记录 MVP-1、MVP-2 和 MVP-3/4/5 的交付状态，并同时出现“apps 交付前无法验证演示闭环”的描述。新版本以原验收记录作为待核验来源，不把各模块交付等同于研究 MVP 完成。维护者核验时逐条记录验收文件、提交版本、执行环境、命令/步骤、实际结果和复核日期；无法复核的状态标为“原文记录，待复核”。

---

## 十七、待满足条件与验收清单

### 17.1 外部条件

| 条件 | 责任人 | 满足标准 |
|---|---|---|
| 教材和题库来源 | 许阳毅、唐欢容 | 版本、来源、许可/授权、章节页码和使用范围齐全 |
| 知识点与策略 | 孙一新、唐欢容 | 知识点映射、难度、阈值、提示模板和停止规则审核并冻结版本 |
| 离线案例与评分 | 孙一新、欧阳文凯、唐欢容 | 30–50 个案例具备输入状态、预期动作、评分规则和标注记录 |
| 学生招募与实验审批 | 唐欢容、欧阳文凯 | 招募、同意、样本方案、随机化、数据处理和分析计划批准 |
| 历史工程状态 | 刘俊鹏 | 对照仓库与验收记录，修正旧版本冲突；未核实部分保留待复核标记 |

### 17.2 v0.6.2 文档验收清单

- 研究主线、项目定位、三项贡献、RQ/H 与实验条件前后一致。
- 目录锚点有效；教育闭环图和技术架构图组件名称与目录树一致。
- CLI 是唯一核心学生交互面；本机浏览器页明确为只读回放。
- D-7 的职责边界、端口、绑定表和结果接口完整且没有反向依赖。
- BKT、Attempt、LearnerEstimate、EvidenceRef 和事件审计字段能够支持实验复现。
- 三组只在预先声明的教学策略和学情状态上存在差异；模型、课程、材料和测验保持固定。
- 指标包含口径、分子分母、失败/缺失情况及必要的模型评测限制。
- 离线回放、教师评分和真实学生学习效果各自报告，不互相替代。
- v0.6.1 的 Provider、Session/Event/Memory、Plugin/Sandbox、Desktop、Pet、Voice、Library、PaperReader、IRT/DKT、Campus 与 Pi 开发者集成均有保留位置和清晰优先级。
- 所有历史完成度均注明来源和核验状态；没有编造实测结果、样本量、教师批准或材料许可。
