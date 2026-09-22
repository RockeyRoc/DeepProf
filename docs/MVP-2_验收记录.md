# MVP-2 验收记录：教学策略图与教育能力

| 项目 | 内容 |
| --- | --- |
| 项目 | DeepProf（大创） |
| 模块主责 | 孙一新（MVP-2 主责；协同 许阳毅） |
| 本轮落地 | 刘俊鹏（项目负责人，在 v0.6 工作区完成 `graph/` · `skills/` · `models/learner` 的移植与对 v0.6 契约的适配；交付后由模块主责接管维护） |
| 模块 | `graph/education/`（`policies/` · `bindings.py` · `contracts.py` · `state.py` · `router.py` · `builder.py` · `nodes/`）· `skills/`（socratic · rag · quiz · diagnosis · paper_reader）· `models/learner/`（契约与接口） |
| 版本 | DESIGNv0.6.1 / MVP-2（D-7 不变：图只产出声明式决策，Runtime 按注入绑定表执行） |
| 验收依据 | §10.3 MVP-2 验收标准、§16.3 第一轮任务与验收、§6.2 绑定表、§7.1 端口、§18.2 交接字段、§18.3 验收记录要求 |
| 验收日期 | 2026-09-22 |
| 验收环境 | Windows · Python 3.14.7 · pytest 9.1.1 · LangGraph 1.2.11 · provider=fake（零密钥、零网络、可完全离线复现） |
| 结论 | **通过**（§10.3 两项验收标准与 §16.3 五项验收全部达成；4 项非阻塞待补依赖见 §6） |

---

## 一、交付范围与责任边界

本模块严格遵循 D-7（§4.4 / §7.1）：**图只做 WHAT，Runtime 只做 HOW**。

| 归属 | 内容 | 落点 |
| --- | --- | --- |
| 教学策略（图侧） | 阈值、分支条件、文案模板、提示词模板、绑定表 | `graph/education/policies/` · `graph/education/bindings.py` |
| 声明式决策 | `PedagogicalDecision`（action / concept / level / params / reason） | `graph/education/contracts.py` |
| 执行能力（Runtime 侧） | 六个通用原语 `render_template` / `invoke_skill` / `retrieve_evidence` / `generate_grounded` / `read_memory` / `write_memory` | `runtime/capabilities.py`（非本模块，仅按绑定表引用） |
| 教育能力（Skill） | 苏格拉底追问、教材检索、出题与反馈、学情诊断、论文理解 | `skills/`（经绑定表按名引用，节点不知道它们的名字） |
| 教材证据 | 只以可定位引用（document_id / chunk_id / page / source）进入图状态 | 经绑定表向 RAG 能力消费（§20.5） |

**边界由守卫测试强制**（`tests/test_architecture_boundaries.py`，本轮 graph/ 已存在故守卫真正生效）：
`graph/**` 只访问窄面 `execute` / `emit`；`runtime/**` 不含教学动作名字符串常量、不反向导入 `graph/` / `skills/`。

### 本轮对 Runtime 的增量（MVP-1 范围，最小侵入）

v0.6 的 Runtime 精简版缺少三样能力，绑定表没有它们就无处安放“提示词怎么写、取 Skill 结果的哪一段、教材原文怎么进模型”，
D-7 的“节点不含提示词”与“有依据的讲解”会同时失效。因此按**纯增量**补回：

| # | 补回的能力 | 落点 |
| --- | --- | --- |
| 1 | `invoke_skill` 的 `content_field`（点路径 / 按取值选字段）与 `passthrough` 白名单 | `runtime/capabilities.py` |
| 2 | `generate_grounded` 的 `system_prompt + prompt_template + values + max_chars`，以及证据**原文**拼装（回传仍只给可定位引用） | 同上 |
| 3 | `retrieve_evidence` 的 Skill 入口（保留 Tool 入口） | 同上 |
| 4 | Skill 宽面口径：Skill 声明 `uses_host = True` 才拿到 `RuntimeHost`；声明了却没 host 则显式失败 | `runtime/skills.py` · `runtime/service.py` |
| 5 | `pedagogy.attempt` 事件类型与契约条目 | `runtime/core/events.py` · `packages/contracts/events.json` |

---

## 二、启动方式（可复现）

无需网络与密钥，全部为确定性离线运行。实测命令与结果：

```powershell
# 1) 全量回归（-o addopts="" 是为了让 pytest 打印汇总行；pytest.ini 自带 -q）
python -m pytest -o addopts="" tests          # 264 passed, 0 skipped

# 2) 本模块用例（图分支 + 图契约 + Skill 契约 + Runtime 能力）
python -m pytest -o addopts="" tests/graph tests/runtime/test_capability_dispatch.py tests/runtime/test_skills.py
```

图侧统一入口：`graph.education.builder.run_teaching_turn(port, state)`；
测试替身：`runtime.testing.FakeRuntime`（`execute` 走**真实**分发器，只有模型与 Skill 被脚本化，绑定表由测试显式注入）。
组合根装配在 `api/app.py::build_service_with_bindings`（注入 `ACTION_BINDINGS` + 注册五个 Skill + 注册检索 Tool），
`/health` 的 `bindings` / `skills` / `tools` 可直接核对装配结果。

---

## 三、验收判据与逐项结果

### 3.1 §10.3 MVP-2 验收标准

| 编号 | 判据 | 判定方法 | 结果 |
| --- | --- | --- | --- |
| A | 分支测试覆盖六类教学动作 | `test_education_routing.py` 中 teach / ask / hint / correct / test / reflect 各至少一条 | 通过 |
| B | 学情增量可复现、可审计 | `test_education_evidence.py` + `test_education_attempt.py`：写入记录带 source / confidence / revocable / model_version；Attempt 三条件闸门 | 通过 |

### 3.2 §16.3 教育层第一轮验收

| 编号 | 判据 | 判定方法 | 结果 |
| --- | --- | --- | --- |
| C | 学生停止时退出，且不再检索、不再产出教学动作 | `test_student_stopped_exits_without_teaching_action` | 通过 |
| D | 无证据不编造引用 | `test_education_evidence.py` 4 例 + 实跑场景 8 | 通过 |
| E | 图不能形成无限追问 | `test_max_turns_never_loops_forever` · `test_hint_ladder_reaches_every_level_before_correcting` | 通过 |
| F | 节点源码里不再出现提示模板 / 提示词 / 证据获取 / Skill 名 | `test_nodes_do_not_carry_templates_or_skill_names`（含检测器自检 + docstring 豁免自检）+ `test_graph_only_uses_narrow_port` | 通过（启用守卫时同步修掉 2 处真实泄漏：`ask.py` 的 `socratic_skill` 审计标签、`test.py` 的 "Quiz Skill" 措辞；其中后者由守卫抓出） |

补充达成的第一轮任务要点：

- **Assess → 条件路由 → 六类动作 → UpdateProfile** 全链路可用，节点只产出 `PedagogicalDecision`；
- **教学样例**覆盖主动求讲解 / 先验不足 / 连续答错 / 稳定错误 / 测验（出题与评价）/ 无证据 / 学生停止（见 §4）；
- **与数据组定义答题事件**：Attempt 契约 `models/learner/attempt.py`，教育组在 Test / Correct 节点产出并经 `pedagogy.attempt` 事件交接（见 §5）；
- **最大轮次、退出与 Reflect 回退**：三层防无限追问（轮次上限 + 唯一回退边同时受 `hint_level` 与 `turn_count` 约束 + `recursion_limit` 兜底）。

---

## 四、教学样例：输入 → 预期 → 实际输出

以下为**本机实跑输出**（`FakeRuntime` + 脚本化模型回复 `（模型回复）先看你的思路。`），非文档推测。
`引用数` 为回传策略层的可定位引用条数（不含教材原文，§6.4）。

| # | 输入样例 | 预期（action） | 实际输出 | 引用数 | Attempt 事件 |
| --- | --- | --- | --- | --- | --- |
| 1 | 「请讲讲什么是梯度下降」（有证据） | teach（贴合教材） | `teach` / `explaining` | 1 | 0 |
| 2 | 「我没学过前面的导数，这块还能听吗」 | teach（**讲前置概念**，不追问） | `teach` / `explaining` | 1 | 0 |
| 3 | 「我觉得可以先求偏导再更新参数」 | ask（苏格拉底追问，不泄露答案） | `ask` / `curious` | 0 | 0 |
| 4 | 「还是算不出来」+ 连续答错 1 次 + 当前提示级别 1 | hint（升级到第 2 级） | `hint` / `encouraging`，`hint_level=2` | 0 | 0 |
| 5 | 「我认为梯度下降是沿正梯度方向走」+ 连续答错 3 次 + 提示已用尽 | correct（指冲突、给对比例，并清零错误计数） | `correct` / `patient`，`wrong_streak` 归零 | 1 | 0（判分缺失） |
| 6 | 测验-评价作答（判分未接入） | test（判分缺失帧，不动错误计数） | `test` / `attentive` | 0 | 0（原因见 §5） |
| 7 | 测验-出题（本轮无作答） | test（出题帧 + 声明题库未接入） | `test` / `attentive` | 0 | 0 |
| 8 | 「请讲讲什么是梯度下降」（无证据） | teach（证据不足固定表述，**零引用**） | `teach` / `explaining` | 0 | 0 |
| 9 | 「我不想学了」（学生停止） | end（收束语，不进入教学动作） | `end` / `warm` | 0 | 0 |
| 10 | 有 `item_id` 的作答（测试注入） | test + 产出作答事实 | `test` / `attentive` | 0 | **1** |

场景 2 的“起讲点前移”通过**提示词**验证（不是只看 action）：

```
prompt_has_prior_gap_note = True
prompt_has_prior_ok_note   = False
prompt_has_evidence_text   = True
```

即送入模型的用户提示词含 `PRIOR_GAP_TEACH_NOTE`（“先补齐最基础的一步……不要用追问要求他自行推理”），
**不含** `PRIOR_OK_TEACH_NOTE`，且含教材片段原文（原文只在这一次生成里使用，回传策略层时只剩定位字段）。
先验判定只认**学生自述**（关键词意图），不从“学情记忆为空”反推没掌握——那等于给学生贴永久标签（§13.1）。

### 场景 8 实际输出（无证据不编造引用）

```
（证据不足）教材检索没有返回可定位的原文片段，因此本轮讲解不给出任何来源与引用。下面只是通用的思考路径，
不作为教材结论：先回到定义与前置概念，确认已知条件与目标，再检查可用定理的适用条件。……
```

同一场景下 `generate_calls = 0`：证据不足时**根本不调用模型**（不是调完再删引用）。

### 场景 9 实际输出（学生停止即退出）

```
好，本轮先到这里。我已经把本轮的学习情况写入可撤回的学情记忆，
你随时可以让我更正或删除它。下次我们接着这个知识点继续。
```

### 场景 6 / 7 实际输出（判分三态分开措辞）

```
# 场景 6（评价作答，判分缺失 → unknown 帧）
我们先对齐一下：先回到定义核对更新方向。
（判分未接入）我无法确认这一步的对错，因此本轮不更新你的错误计数，也不会把结论写成学情标签。……

# 场景 7（出题帧）
【自检】请说明梯度下降的更新方向。
（说明：课程题库与自动判分尚未接入——题目由模型即时生成，判分结论不写入学情；作答事实（Attempt）
只在题目可追踪且判分可靠时产出，当前题库未接入，故本轮不产出。）
```

### 绑定表快照（action → capability，实跑导出）

```
assess         -> retrieve_evidence      recall         -> read_memory
diagnose       -> invoke_skill           update_profile -> write_memory
end            -> render_template        reflect        -> render_template
hint           -> render_template        ask            -> invoke_skill
teach          -> generate_grounded      correct        -> generate_grounded
test           -> invoke_skill
```

---

## 五、作答事实（Attempt）交接：教育组 → 数据组（§16.3 / §18.2）

**责任分工**：Attempt 的**契约**由数据组定义（`models/learner/attempt.py`），
**产出与发送**由教育组在 Test / Correct 节点完成，随 `pedagogy.attempt` 事件落盘供数据组消费（BKT / IRT 输入）。

产出闸门由 `policies.attempt_gate` 唯一给出，三条必要条件缺一不可：

| 条件 | 不满足时的行为 | 依据 |
| --- | --- | --- |
| 归属明确（`learner_id` 非空） | 不产出，避免跨学习者污染画像 | §13.2 |
| 判分可靠（`correct` 为显式 bool） | 不产出，不得把“判不了”当成答错 | §13.1 |
| 题目可追踪（`item_id` 非空） | 不产出，不编造无法与题库对齐的 id | §18.2 |

跳过时**必须**在决策事件里写明 `attempt_skip_reason`，静默跳过会被误判为链路故障。

### 实测：当前生产真实状态（题库未接入）

```
[6 测验-评价作答（判分未接入）] action=test  attempt_events=0
    skip_reason=缺少可靠判分（自动判分未接入）：不产出 Attempt，不得把判不了当成答错（§13.1）
[7 测验-出题（本轮无作答）]    action=test  attempt_events=0
    skip_reason=缺少可靠判分（自动判分未接入）：不产出 Attempt，不得把判不了当成答错（§13.1）
```

### 实测：题目可追踪时产出成功路径

```
[10 有 item_id 的作答] action=test  attempt_events=1  skip_reason=
```

产出的 payload 能通过数据组契约校验（`Attempt.model_validate`）；失败路径逐条可验：
缺 `item_id` / 判分为 `None` / 缺 `learner_id` 三例均不产出并给出对应原因（`test_education_attempt.py`，11 例）。

---

## 六、失败案例与待补依赖（非阻塞）

以下四项**不影响本轮判定**，但必须在下一轮由对应责任人补齐。本轮一律以“显式不产出 / 显式说明”处理，不做隐式兜底。

| # | 现象 | 本轮处理（诚实状态） | 待补依赖 / 责任 |
| --- | --- | --- | --- |
| 1 | **教材索引未接入** | `search_textbook` 成功但返回空证据并写明 `missing=["course_corpus","vector_index"]`；RAG Skill 据此返回 `insufficient_evidence`；Teach/Correct 走证据不足表述，引用为 0 | 语料与向量索引 → 许阳毅（MVP-4，§20.5） |
| 2 | **课程题库未接入** | Quiz Skill 返回 `item_bank_connected=False` 与 `item.item_id=""`；出题帧带“题库未接入”声明；Attempt 走 skip 分支并写明原因 | 题库与来源映射 → 许阳毅（MVP-4） |
| 3 | **自动判分未接入** | 判分三态（对 / 错 / 判不了）；`last_answer_correct` 缺失即 `unknown`，不更新 `wrong_streak`、不写学情、不产出 Attempt | 判分链路 → 欧阳文凯（§18.1） |
| 4 | **学情模型未接入** | `diagnose` 返回 `not_implemented`（**不产出任何掌握度数字**），图侧退化为规则化观察（带 source / confidence / expires_at / revocable / metadata.model_version） | BKT / IRT / 知识追踪 → 欧阳文凯（§18.1） |

**第 2 项的技术根因（须在下一轮解决）**：`state["item_id"]` 目前**没有真实写入方**。
Quiz Skill 返回的是 `item.item_id`（嵌套），而 Runtime 的 `invoke_skill.passthrough` 只按**顶层键**白名单带回；
`SessionRequest` 字段已在 §18.2 冻结、不能新增 `item_id`。因此 **Attempt 成功路径目前只能由测试注入 `item_id` 触达**
（见 §5 场景 10），生产链路上稳定走 skip 分支。修复方向（三选一，需与数据组/接口归口确认）：

1. 题库接入后由 Quiz Skill 直接返回顶层 `item_id`（配合 `passthrough: ["item_id"]`）；
2. 让 `passthrough` 支持点路径（如 `"item.item_id"`）——需评估是否扩大到任意路径；
3. 由 Session 元数据在轮间传递题目标识（不改冻结字段）。

**另有一项观察（不影响本轮）**：`tests/api/test_gateway.py::test_message_turn_writes_session_and_events`
在满载运行下曾失败 1 次（`api/sessions.py::_run_turn` 用 `asyncio.create_task` 启动教学轮，测试随即轮询事件），
单独运行与连续重跑 3 次均通过。属既有实现的时序敏感问题，归 MVP-1（刘俊鹏）。

---

## 七、测试覆盖

`python -m pytest -o addopts="" tests` → **264 passed, 0 skipped**（exit 0）。

> 下列计数为 **MVP-2 交付时点的快照**。随后在 MVP-1 轮新增了 5 例
> （跨 Provider Profile 切换 3 例 + 「降级兜底不附引用」2 例），当前全量 **269 passed**。
> 其中「模型生成失败降级成兜底文案时**不附引用**」（§7.3）此前只在节点注释里声明、
> 未真正实现，是在那一轮被发现并修复的（`runtime/capabilities.py` 新增 `degraded_to_fallback`）；
> 现在是测试锁定的行为，详见 [MVP-1_验收记录.md](MVP-1_验收记录.md)。

本模块相关用例（实测计数）：

| 测试文件 | 用例数 | 覆盖要点 |
| --- | --- | --- |
| `tests/graph/test_education_routing.py` | 16 | 六类动作分支、先验不足样例、退出与防无限追问、事件 schema |
| `tests/graph/test_education_evidence.py` | 10 | 证据不足不编造引用、可定位校验、学情写入纪律 |
| `tests/graph/test_education_attempt.py` | 11 | Attempt 闸门三条件、节点产出、整轮通路 |
| `tests/graph/test_memory_note.py` | 10 | 学情记忆进入生成上下文（压缩 → 透传 → 拼装）全链路 |
| `tests/graph/test_education_contract.py` | 8 | **决策事件不含学生正文**、组合根装配、RAG 规范化、**节点瘦身守卫（含自检）** |
| `tests/runtime/test_capability_dispatch.py` | 29 | 分发器行为、装配失败必须显式、证据原文/引用边界、`content_field` 与 `passthrough` |
| `tests/runtime/test_skills.py` | 4 | Skill 宽面口径：`uses_host` 才拿到 host，缺 host 显式失败 |
| `tests/test_architecture_boundaries.py` | 21 | runtime 不反向依赖、runtime 无教学词汇、**graph 只碰窄面**、密钥边界 |
| `tests/test_contract_consistency.py` | 9 | `packages/contracts` 与代码同步（含新增 `pedagogy.attempt`） |

三条与本模块直接相关的架构守卫：

1. `runtime/` 字符串常量不得含教学动作词（`hint` / `teach` / `correct` / `reflect` / `socratic`）；
   `pedagogy.*` 事件类型不在禁止之列，故 `pedagogy.attempt` 合法；
2. `graph/` 只能通过窄面 `execute` / `emit` 访问 Runtime（禁止 `.invoke_skill` / `.generate` / `.read_memory` / `.write_memory` 属性访问）——
   本轮 graph/ 落地后该守卫由 `skip` 变为**真正执行**（`21 passed, 0 skipped`）；
3. `packages/contracts/events.json` 的事件集合与 `EventType` 必须完全一致（新增事件漏登记即失败）。

另有本轮新增的**节点瘦身守卫**（`tests/graph/test_education_contract.py`）：
扫描 `graph/education/nodes/*.py` 的字符串常量（排除 docstring），禁止出现能力名
（`socratic` / `quiz` / `rag` / `diagnosis` / `paper_reader` / `search_textbook`）与提示词占位符
（`{evidence_block}` / `{prior_gap_note}` / `{memory_note}` / `{conflicts}`），并自带两个自检用例
（能发现违规、docstring 豁免），避免守卫退化为恒真。

---

## 八、交接对象

| 对象 | 交接内容 | 通道 |
| --- | --- | --- |
| RAG 组（许阳毅） | 教材证据只经绑定表引用、不直接调用；索引就绪后只需让 `search_textbook` 返回带定位字段的命中 | `retrieve_evidence` 绑定 → `skills/rag` → `tools/retrieval` |
| 数据组（欧阳文凯） | 作答事实 Attempt（教育组产出、数据组消费）；`LearnerModel` 接口已就位待实现 | `pedagogy.attempt` 事件 + `models/learner` |
| 前端 / 桌宠（张钧翔） | 文本、教学动作、情感标签 | 图状态 `action` / `response_text` / `emotion`（§16.3、§19.8） |
| 指导教师（唐欢容） | **教学策略评审材料** | `graph/education/policies/` + `graph/education/bindings.py`（§16.8） |
| 接口归口（刘俊鹏） | 接口问题与字段冻结；本次补回的 5 项 Runtime 能力 | `RuntimePort` / `RuntimeHost` / `packages/contracts/` |

> 教育样例由唐欢容审阅，接口问题由刘俊鹏归口（§18.3）。任何实验结果均来自本机实际运行，不填造成功率或完成百分比。

---

## 附录：验收证据快照

| 指标 | 实测值 |
| --- | --- |
| 全量测试 | 264 passed, 0 skipped（exit 0） |
| 实跑教学场景 | 10 条（六类动作 + 退出 + 无证据 + Attempt 成功路径） |
| 先验不足提示词校验 | `prompt_has_prior_gap_note = True`，且不含先验正常提示 |
| 无证据讲解引用数 | 0，且 `generate_calls = 0`（未调用模型） |
| 有证据讲解 | 引用 1 条，仅含 document_id / chunk_id / page / source（无 `text`） |
| Attempt 生产路径 | skip（`item_id` 为空或判分缺失，原因显式写入决策事件） |
| Attempt 可追踪路径 | 1 条事件，payload 通过 `Attempt.model_validate` |
| 架构守卫 | graph 窄面扫描**已生效**（非 skip）；契约一致性全绿；节点瘦身守卫抓出 2 处真实泄漏并已修复 |
| 运行依赖 | provider=fake，零网络零密钥，可完全离线复现 |
| 提交状态 | 工作区改动**尚未提交**（本轮按负责人要求不提交；HEAD 仍保有旧实现可回查） |