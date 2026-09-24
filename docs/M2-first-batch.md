# DeepProf v0.6.2 · M2 首批重构交付记录

日期：2026-09-24（历史交付记录）  
状态：**记录首批交付时的状态，已由 [最新 M2 离线验收记录](M2-offline-acceptance.md) 取代。**

本记录保留当时的验收缺口和实施顺序作为历史，不代表当前代码状态。当前已实现固定序列 BKT、Attempt 持久化及 C 组估计读取；最新工程验收与仍待完成的教师审核、参数校准和学生效果见上述记录。

## 交付范围

本批建立 M1 测验作答到学习数据层的第一段链路，并整理 Attempt、LearnerEstimate 与 LearnerModel 的领域契约。它不包含 BKT 算法、学情估计持久化或 DeepProf/C 组运行。

- 测验只从来源已复核、允许运行的题目中出题；响应返回题面和作答要求，不暴露标准答案或答案页定位。当前题库版本固化在实验会话中，题库版本漂移时停止沿用旧会话题目。
- `quiz.answer` 必须对应当前会话的当前题目。只有来源、评分规则均已复核且采用 `exact_normalized_match` 的题目可自动判分；开放题或不可靠规则走人工评分错误，不创建正确/错误 Attempt。
- 成功判分后以 `command_id` 作为 Attempt 幂等标识，持久化学习者、会话、trace、题目、`scored_concept_id`、正确性、提示数、评分来源、置信度、题库版本和时间。重复命令不会重复插入或累计。
- 当前题的作答结果更新本会话的 `attempt_count`、`wrong_streak` 和提示历史，供同一题的后续教学回合使用；这属于会话内状态，不是跨会话学情估计，也不会被 A/B 组当作长期画像读取。
- `models/learner/` 定义 `Attempt`、`LearnerEstimate`、`ModelType` 与 `LearnerModel` Protocol。估计契约区分 BKT 概率与 IRT 能力量表，携带模型版本、证据数和不确定性；证据不足不能伪装成零掌握度。

## 实现边界和已知差异

- `models/learner/Attempt` DTO 当前仍是基础交接字段（作答 ID、学习者、题目、知识点列表、正误、时间、提示数）；实际 Gateway 持久化还记录 `scored_concept_id`、`grading_source`、`confidence`、题库版本、session/trace 和本地答案值。二者尚未通过统一 mapper 对齐，后续应扩展 DTO 并为旧记录制定兼容读取规则。
- SQLite 已有 `attempts` 表和学习者/知识点、会话索引；当前没有 `learner_estimates` 表、参数注册表或 BKT checkpoint。
- Attempt 写入仍在 API 作答管线完成；`LearnerModel` 仅为接口，仓库没有 BKT `update/estimate` 实现，教学图也未读取持久化 LearnerEstimate。
- 本地 Attempt 表会保存答案值以支持后续复核；只读回放和普通事件对外输出不暴露答案。真实学生数据接入前仍需明确保留期限、访问控制和删除流程。

## 验证证据

- 测验集成测试核对当前题目绑定、答案与答案页隔离、重复命令幂等、判分后 session 状态更新，以及同题后续回合读取本地错误/提示状态。
- 评分测试验证来源与量规必须都已复核；题库覆盖/版本检查阻止未核题目进入运行路径。图节点测试确认未配置题库时不生成替代题，不写 Attempt 或错误学情事实。
- 当前全量 Python 回归 275 项通过、0 项跳过、1 项非阻断 deprecation warning；CLI 类型检查通过、3 项 CLI 测试通过。以上是工程回归，不是 BKT 数值验收或学生学习效果证据。

## M2 阶段验收缺口

按照 [DESIGNv0.6.2 §12.2](DESIGNv0.6.2.md)，M2 的入口条件尚未满足：

1. 固定 Attempt 序列的 BKT 后验与学习转移尚无可复现实现和测试；初值、猜测、失误、学习转移参数及其来源未冻结。
2. 低样本、缺失或不可靠判分目前在题库评分入口会拒绝写 Attempt，但没有模型层的估计降级、置信区间/不确定性策略和持久化验证。
3. DeepProf/C 组尚未接入 LearnerEstimate；A/B 不读取跨题学习状态这一隔离约束已经保留，但不能替代 C 组验证。
4. Attempt DTO 与实际数据库行结构尚未统一；答案值保留策略及学习者标识边界需要在真实参与者数据进入前审查。
5. 教师对题库、评分规则、BKT 参数和教学阈值的审批仍待完成。M1 历史真实 A/B 运行有 4 格失败；当时的修复后复测被 Provider HTTP 401 阻止。这不作为 M2 通过条件，也不能替代 M2 的固定序列验收。后续 M3 DeepSeek 试点状态见 [M3-live-pilot](experiments/M3-live-pilot.md)，属于独立批次。

## 后续交付顺序

1. 统一 Attempt DTO 与 SQLite 写入/读取映射，明确历史兼容和本地答案保留策略。
2. 实现带版本与参数来源的单概念 BKT，处理时间排序、重复输入、低证据与不可靠评分；用固定 Attempt 序列核对每一步后验和学习转移。
3. 持久化并检索 LearnerEstimate，冷启动返回明确的信息不足状态；为可靠评分、人工评分、缺失作答和低样本路径分别建立测试。
4. 增加 DeepProf/C 组的冻结会话配置与学情读取；通过隔离测试证明 A/B 不读取跨题状态，C 组按 learner/concept 使用有版本的估计。
5. 经教师审核冻结参数和策略后，再开始 M2 固定案例验收；没有审批前不把初始化参数描述为已校准。

## 关联实现与记录

- M1 基线与人工收验记录：[M1 首批重构交付记录](M1-first-batch.md)。
- 学情契约：[`models/learner/attempt.py`](../models/learner/attempt.py)、[`models/learner/estimate.py`](../models/learner/estimate.py)、[`models/learner/__init__.py`](../models/learner/__init__.py)。
- 作答入口与评分：[`api/sessions.py`](../api/sessions.py)、[`evaluation/question_bank.py`](../evaluation/question_bank.py)。
- 持久化结构：[`runtime/storage/migrations.py`](../runtime/storage/migrations.py)。
- 主要验收测试：[`tests/api/test_ab_and_replay.py`](../tests/api/test_ab_and_replay.py)、[`tests/graph/test_education_attempt.py`](../tests/graph/test_education_attempt.py)、[`tests/test_question_bank.py`](../tests/test_question_bank.py)。
