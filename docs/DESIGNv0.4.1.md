# DeepProf 项目结构化设计文档

> **版本**：v0.4.1（分层式 Agent Runtime 与教学策略图协同架构；接口修订版）  
> **更新日期**：2026-09-19  
> **文档性质**：项目总纲 + 系统架构设计 + 工程实施基线  
> **上一版本**：`DESIGNv0.4.md` v0.4（2026-09-17）  
> **配套架构图**：`deepprof_framework_v0.4.html`

---

## 版本变更记录

| 版本 | 日期 | 变更摘要 | 作者 |
| --- | --- | --- | --- |
| v0.1 | 2026-09-14 | 初版：角色设定、三大核心侧、技术栈、模块划分 | 团队 |
| v0.2 | 2026-09-16 | 新增五层架构、端到端链路与框架图维护说明 | 团队 |
| v0.3 | 2026-09-17 | 重构为“自研 Agent Runtime + LangGraph Pedagogical Graph”；新增 Pi、DeepSeek Harness 参考；明确 Core、Plugin、Skills/Tools/Storage/Sandbox 边界；更新目录树、数据流与参考网址 | 团队 |
| v0.4 | 2026-09-17 | 确认 D-1—D-6；新增团队任务与学习路线、自有服务器门户部署方案、学情模型接入与联调契约 | 团队 |
| v0.4.1 | 2026-09-19 | **接口修订（D-7）**：教学图只产出声明式决策 `PedagogicalDecision`；`RuntimePort` 收窄为 `execute + emit`，能力面拆为 `RuntimeHost`；新增 `CapabilityResult` 与绑定表（action → capability 由组合根注入）；配套三条架构守卫测试 | 团队 |

## 目录

- [一、文档使用说明](#一文档使用说明)
- [二、助手角色定位](#二助手角色定位)
- [三、项目背景、目标与创新主张](#三项目背景目标与创新主张)
- [四、v0.4 总体架构](#四v03-总体架构)
- [五、DeepProf Runtime 设计](#五deepprof-runtime-设计)
- [六、Pedagogical Graph 设计](#六pedagogical-graph-设计)
- [七、关键接口与端到端数据流](#七关键接口与端到端数据流)
- [八、技术栈选型](#八技术栈选型)
- [九、模块划分与目录树](#九模块划分与目录树)
- [十、MVP 迭代路线](#十mvp-迭代路线)
- [十一、参考项目、借鉴边界与协议合规](#十一参考项目借鉴边界与协议合规)
- [十二、开发原则与质量要求](#十二开发原则与质量要求)
- [十三、安全、隐私与教育伦理](#十三安全隐私与教育伦理)
- [十四、当前项目配置快照](#十四当前项目配置快照)
- [十五、已确认决策](#十五已确认决策)
- [十六、团队任务与学习路线](#十六团队任务与学习路线)
- [十七、部署与协作](#十七部署与协作)
- [十八、学情模型与联调契约](#十八学情模型与联调契约)
- [附录 A：架构图使用说明](#附录-a架构图使用说明)
- [附录 B：核心术语](#附录-b核心术语)

---

## 一、文档使用说明

本文档是 DeepProf v0.4 的架构基线。新增设计时应遵循以下规则：

1. 正文章节使用中文序号；小节使用 `4.1` 形式，并同步更新目录。
2. 未定事项使用 `{{TODO: 说明}}`，已决策事项写明结论与日期。
3. 架构图与本文档必须同步更新；图中组件名称应与第九章目录树一致。
4. “参考项目”只代表设计借鉴，不代表直接复制实现；实际引入代码前必须核查版本与 License。

---

## 二、助手角色定位

### 2.1 角色身份

- **角色名**：DeepProf 项目首席开发助手
- **定位**：精通教育 AI、Agent Runtime、RAG、教学策略编排、桌宠交互和全栈开发的技术助手
- **使命**：协助大创团队从 0 到 1 构建可研究、可扩展、可验证的教育智能体 DeepProf
- **边界**：作为开发助手协助构建，不直接扮演 DeepProf 本身

### 2.2 服务对象

| 对象 | 主要需求 |
| --- | --- |
| 大创团队 | 清晰的任务拆解、可运行代码、可持续演进的架构 |
| 申报与答辩评审 | 技术路线、创新点、研究问题、可验证指标 |
| 高校学生 | 可信、个性化、以启发为主的长期伴学体验 |

---

## 三、项目背景、目标与创新主张

### 3.1 项目愿景

DeepProf 是面向高等教育的个性化伴学智能体。它把教育智能体能力与桌宠式情感交互结合起来，在长期会话中逐步理解学生的知识状态、学习习惯和情感偏好。

### 3.2 三大核心侧

| 教育侧 | Agent 侧 | 宠物侧 |
| --- | --- | --- |
| 苏格拉底式提问 | 自研 Runtime | Live2D / PNG 桌宠 |
| 教材 RAG | Session / Event / Tool | 语音与情感反馈 |
| 错题与测验 | Provider / Memory / Plugin | 好感度与主动陪伴 |
| 学情诊断 | Storage / Sandbox | 个性化表达 |

### 3.3 架构创新主张

> **面向高等教育的分层式 Agent Runtime 与教学策略图协同架构**

核心思想不是“直接选择一个 Agent 框架”，而是把系统拆成两个可以独立演进和独立评测的层次：

- **底层 DeepProf Runtime 负责 Agent 能力**：Session、Tool、Model、Memory、Event、Plugin、Storage、Sandbox。
- **上层 Pedagogical Graph 负责教育策略**：什么时候讲、什么时候问、什么时候提示、什么时候纠错、什么时候测试、什么时候更新学情。

这种拆分使“Agent 能做什么”与“教师此刻应该怎么教”彻底解耦。系统的研究价值由此从“组合 RAG + LangGraph + Live2D”提升为可被实验验证的 Runtime—Pedagogy 协同机制。

**解耦靠什么落地**（D-7，详见 §4.4）：上层只产出**声明式决策**
（`PedagogicalDecision`：动作 + 参数 + 依据），下层按**注入的绑定表**决定怎么执行。
这与常见的做法有实质差别：

| 常见做法 | DeepProf 的做法 | 得到的性质 |
| --- | --- | --- |
| 策略写在节点/Skill 内部，正文与调用一起产出 | 节点只产出决策数据，正文与调用由绑定表落地 | 策略层退化为**纯数据**，可反事实回放与离线比较 |
| 两者通过"约定不要互相调"来隔离 | 端口分面 + 架构守卫测试，违反即失败 | 边界可机械检查，不依赖自觉 |
| 换模型/换实现要改策略代码 | 改绑定表数据即可 | 换执行方式不动策略 |

因此"Agent 是策略的自动化执行"这句话在本项目里是可证的：
策略的产物里没有一行正文、没有任何能力名，唯一的下行通道是 `execute`。

### 3.4 可研究的核心问题

1. 教学策略图是否比单轮提示词更能稳定执行苏格拉底式教学？
2. Runtime 与 Pedagogical Graph 解耦后，是否能降低新增教学策略的工程成本？
3. 学情记忆反馈是否能提高后续提示、测验和纠错的个性化质量？
4. 事件轨迹是否能支持教学过程复盘、策略比较和可解释性分析？

D-7 使问题 1 与 4 有了更直接的实验手段：决策是纯数据，因此可以**反事实回放**——
固定同一段学情轨迹，只替换决策里的级别或证据条件，重跑执行层即可比较输出差异，
不必重跑整个模型链路。

---

## 四、v0.4 总体架构

### 4.1 分层总览

| 层级 | 核心职责 | 主要组件 | 设计来源 |
| --- | --- | --- | --- |
| 交互层 | 接收文本/语音，呈现回答、表情和动作 | Electron + React / Web、Live2D、ASR/TTS | DeepProf 自研 |
| Pedagogical Graph | 决定教学时机、顺序、分支和回退 | Teach、Ask、Hint、Correct、Test、Update Profile | LangGraph |
| Education Capability | 提供可复用教学能力 | Socratic、RAG、Diagnosis、Quiz、PaperReader | 教育项目与 DeepProf 自研 |
| DeepProf Runtime | 提供通用 Agent 执行能力与生命周期 | Agent、Session、Event、Message、Tool、Provider、Memory、Plugin | Core 参考 Pi；Plugin 思想参考 DeepSeek Harness |
| 基础设施层 | 持久化、隔离、模型与外部服务 | Storage、Sandbox、SQLite、Vector DB、LLM Providers | DeepProf 自研适配 |

层的**职责**如上表；层之间的**接缝**见 §4.4：Pedagogical Graph 与 Education Capability
之间不是直接调用，而是"图出决策 → 绑定表 → Runtime 执行"。因此这张表读作
"谁负责什么"，不读作"谁调用谁"。

### 4.2 核心结构图

```text
                         DeepProf
                             │
                    交互层 / 桌宠 / API
                             │
          ┌──────────────────┴──────────────────┐
          │       DeepProf Agent Runtime        │
          │            （自己设计）              │
          │                                     │
          │  Core：Agent · Session · Event      │
          │        Message · Tool · Provider    │
          │  Services：Model · Memory · Storage │
          │  Runtime：Plugin · Skills · Sandbox │
          │                                     │
          │  Core 参考 Pi                       │
          │  Plugin 思想参考 DeepSeek Harness   │
          └──────────────────┬──────────────────┘
                             │ Runtime API / Events
                         LangGraph
                             │
                    Pedagogical Graph
                             │
       ┌──────────┬──────────┼──────────┬──────────┐
       │          │          │          │          │
     Teach       Ask        Hint      Correct     Test
       │          │          │          │          │
       └──────────┴──────┬───┴──────────┴──────────┘
                         │
          Socratic · RAG · Quiz · Diagnosis
                         │
                  Update Learner State
                         │
                       Memory
```

> 上图为 v0.4 的总体分层；**教学图与 Runtime 之间的接缝在 v0.4.1 做了接口修订**，
> 见 §4.4：图不再直接调用 `Socratic / RAG / Quiz / Diagnosis` 这些能力，
> 而是产出声明式决策，由 Runtime 按注入的绑定表决定调哪个能力。

### 4.3 关键设计决策

| 决策 | v0.2 | v0.4 / v0.4.1 |
| --- | --- | --- |
| Agent 核心 | LangGraph 同时承担对话状态机和教学编排 | 自研 Runtime 承担通用 Agent 生命周期 |
| LangGraph 定位 | Harness 层核心 | 仅用于 Education Workflow / Pedagogical Graph |
| Plugin | 未形成统一运行时 | Plugin Runtime 作为一等能力 |
| Session/Event | 隐含在状态机中 | 显式模型、可持久化、可回放 |
| Tool/Model | 业务调用逻辑 | 统一注册、策略校验、Provider 适配 |
| 教学策略 | Skill 内部逻辑 | 可视、可测、可分支的策略图 |
| **策略与执行的接缝**（v0.4.1） | 图节点内部直接调 Skill / 模型 / 记忆 | **图只产出声明式决策**；Runtime 按注入的绑定表执行（见 §4.4） |

### 4.4 解耦边界

**分层与方向**

- Runtime 不判断"是否该给提示"，只执行图节点请求并返回结构化结果。
- Pedagogical Graph 不直接操作数据库、模型 SDK 或沙箱，只通过 Runtime Port 调用能力。
- Skill 不持有全局 Session；需要状态时通过 `RuntimeContext` 读写受控 Memory。
- Provider 不包含教学逻辑；模型更换不得改变图的业务语义。
- UI 不直连 Tool、Memory 或 Provider；所有动作由 Runtime 接入并产生事件。

**WHAT / HOW 的接缝（v0.4.1 修订）**

图回答"应该做什么"，Runtime 回答"怎么把它做出来"；两者之间只有两张契约，
且**教学词汇只存在于图侧的数据里**：

```text
        WHAT（策略层：graph/education）              HOW（执行层：runtime）
 ┌──────────────────────────────┐
 │ 节点：算策略                   │
 │  ① 触发条件、级别、冲突点     │
 │  ② 产出 PedagogicalDecision   │
 └──────────────┬───────────────┘
                │  RuntimePort.execute(decision, ctx)   ← 窄面，只有这一个出口
                ▼
 ┌──────────────────────────────┐
 │ ActionDispatcher（通用分发）   │  不认识 hint/teach/ask 任何词
 │  查绑定表 → 解析 capability    │
 │  按需先取证据 → 执行 → 归一化   │
 └──────────────┬───────────────┘
                │  RuntimeHost.invoke_skill / generate / call_tool / read_memory / write_memory
                ▼
 ┌──────────────────────────────┐
 │ 通用原语 + Skill / Tool / 模型 │
 │ / 记忆存储                     │
 └──────────────┬───────────────┘
                │  CapabilityResult{content, evidence, records, status}
                ▲
                └───────────────→ 图更新状态、交前端

 action → capability 的映射 = graph/education/bindings.py（**纯数据**）
 由组合根 api/app.py 注入 RuntimeService（runtime/ 不得反向依赖教学词汇）
```

下列约束使这条接缝可机械检查，而不是靠自觉：

| 约束 | 落地方式 | 守卫 |
| --- | --- | --- |
| Runtime 不认识教学动作名 | 绑定表放在图侧，Runtime 只查表 | `runtime/**` 字符串常量词汇扫描 |
| 图不编排执行细节 | 端口分面：图只有 `execute` / `emit` | `graph/**` 宽面属性访问 AST 扫描 |
| 端口窄面不被加宽 | `RuntimePort` 只有两个方法 | 协议面断言（`execute`/`emit`） |

**为什么这样切**

- **策略层退化为纯数据**：同一份决策可以反事实回放（换级别、换证据条件），
  支撑 §3.4 的研究问题 1 与 4，而不是靠重跑一遍模型。
- **换执行方式不动策略**：提示话术、Skill 选择、提示词都在绑定表里，
  教育组改表即改行为，Runtime 与节点代码不用动。
- **可评审性集中**：教师只需评审 `policies/`（阈值与文案）与 `bindings.py`（怎么落地）。

---

## 五、DeepProf Runtime 设计

### 5.1 Core：参考 Pi 的极简内核

Pi 的核心价值在于保持 Agent loop 与状态管理精简，并通过 Session、事件流、工具和 Provider 扩展能力。DeepProf 借鉴其“最小稳定内核”思想，但不直接复刻 Pi 的终端编码 Agent 产品形态。

| 组件 | 职责 | 最小接口示意 |
| --- | --- | --- |
| Agent | 驱动一次或多次模型—工具循环，维护运行状态 | `run(context) -> AsyncEventStream` |
| Session | 会话标识、分支、恢复、上下文窗口与持久化 | `load / append / fork / compact` |
| Event | 记录生命周期、模型、工具、教学与错误事件 | `publish / subscribe / replay` |
| Message | 统一 user / assistant / system / tool 消息结构 | `role + content + metadata` |
| Tool | 描述、参数校验、权限策略、执行与结果规范化 | `schema + execute()` |
| Provider | 屏蔽不同模型供应商和流式协议差异 | `generate / stream / capabilities` |

Core 必须保持少依赖、可单元测试、与 LangGraph 解耦。教育能力通过 Port/Adapter 接入，而不是写入 Agent loop。

### 5.2 Plugin Runtime：参考 DeepSeek Harness

DeepSeek Harness 的关键思想是“Everything is a Plugin”：模型、工具、技能、会话、沙箱、存储和 UI 都可由插件组合。DeepProf 采用较小的受控子集：

- 插件具有唯一 `id`、版本、能力声明和依赖列表。
- 插件通过生命周期挂载：`install → start → stop → uninstall`。
- 插件只能通过显式 Service Registry 获取服务，不读取 Runtime 私有状态。
- Tool、Skill、Provider、Storage 与 Sandbox 都可以由插件提供。
- 插件事件写入 Session 轨迹，支持回放、审计和失败定位。
- 第三方插件默认最小权限；文件、网络、进程与密钥权限分开授权。

### 5.3 Skills、Tools、Storage 与 Sandbox

| 子系统 | 定义 | 示例 | 约束 |
| --- | --- | --- | --- |
| Skills | 面向模型的可复用能力说明与流程，由绑定表按名引用 | Socratic、Quiz、PaperReader | 负责“怎么完成一类任务”，不拥有底层权限；**图节点不直接调用它**（§4.4） |
| Tools | 可执行且有结构化参数的原子操作 | 检索教材、保存错题、读取画像 | 必须经过 schema 校验、权限检查和审计 |
| Storage | Session、Memory、资源和轨迹的持久化接口 | SQLite、文件存储、向量库 | 上层只依赖接口，不绑定具体数据库 |
| Sandbox | 隔离高风险工具与第三方插件 | 文件边界、进程限制、网络策略 | 默认拒绝越界，危险动作需审批 |

### 5.4 Runtime 事件模型

建议最小事件集合：

```text
session.started / session.resumed / session.compacted / session.ended
agent.started / agent.turn.started / agent.turn.completed / agent.failed
model.requested / model.stream.delta / model.completed / model.failed
tool.requested / tool.approved / tool.started / tool.completed / tool.failed
memory.read / memory.write
plugin.started / plugin.failed / plugin.stopped
pedagogy.node.entered / pedagogy.decision / pedagogy.node.exited
```

事件采用追加写入（append-only）并携带 `event_id`、`session_id`、`trace_id`、`timestamp`、`type`、`payload` 与 `source`。敏感字段在落盘前脱敏。

`pedagogy.decision` 是实验复盘的主要依据（§3.4 研究问题 4）：它记录节点、动作、
`reason`、依据（`evidence_sufficient` / `evidence_count` / `attempt_count` /
`wrong_streak` / `hint_level` / `turn_count` / `max_turns`）以及本轮**实际执行了哪个能力**
（`capability` / `capability_status`）。因此"策略判了什么"与"执行做成了什么"
在同一条轨迹里可分别核对。

隐私纪律：决策事件的载荷里**不得包含学生正文**——学生文本只出现在决策的
`params` 里（供能力层使用），事件只记长度与依据字段（§6.4、§13.2）。
`hint` / `test` 还额外声明 `answer_leaked`，用于核查"是否过早泄露答案"。

### 5.5 Memory 边界

Memory 是 Runtime 服务，不等同于 Session 日志：

- **Session Log**：事实轨迹，用于恢复、审计与回放。
- **Working Memory**：当前任务与本周学习目标。
- **Long-term Learning Memory**：知识点掌握度、错误模式和学习偏好。
- **Episodic Memory**：关键学习事件与干预结果。
- **Affective Memory**：经用户授权保存的表达偏好与互动状态。

任何长期记忆写入都必须包含来源、置信度、过期策略和可撤回标记。

---

## 六、Pedagogical Graph 设计

### 6.1 LangGraph 的唯一职责

LangGraph 负责把教育策略表达为有状态图，不负责底层 Agent Runtime。它通过 DeepProf Runtime 提供的稳定端口调用模型、工具、记忆与技能。

**v0.4.1 收窄**：节点不产出教学正文、不自己取教材证据、不直接调 Skill 或模型。
每个节点只做两件事——算策略、发决策：

```python
decision = PedagogicalDecision(action=..., concept=..., level=..., params={...})
result   = await dispatch(port, state, decision)   # → RuntimePort.execute
# result.content / result.evidence / result.records / result.status
```

"这个动作该由什么能力做"写在 §6.2 的绑定表里，由组合根注入 Runtime；
节点代码里因此不再出现提示模板、提示词、证据获取与 Skill 名。

### 6.2 教学决策节点

所有节点只产出声明式决策，正文与执行细节由 Runtime 按绑定表落地。

| 节点 | 触发条件 | 行为 | 主要输出 |
| --- | --- | --- | --- |
| Assess | 新问题、阶段切换或信息不足 | 判断意图、先验知识与置信度 | 学习状态快照 |
| Teach | 概念缺失且适合直接解释 | 分层讲解并给出来源 | 讲解片段 |
| Ask | 学生具备推理基础 | 苏格拉底式追问 | 问题与预期认知目标 |
| Hint | 尝试受阻但不宜直接给答案 | 从轻到重给提示 | 提示级别与内容 |
| Correct | 出现稳定错误或概念混淆 | 指出冲突、解释原因、提供对比例 | 纠错记录 |
| Test | 需要验证理解或间隔复习 | 生成题目并评价作答 | 测验结果 |
| UpdateProfile | 节点完成或会话结束 | 更新掌握度、错误模式与偏好 | 学情增量 |
| Reflect | 多轮无进展、异常或策略失效 | 选择回退、换策略或求助教师 | 策略调整 |

**动作 → 能力绑定表**（`graph/education/bindings.py`，纯数据，可 JSON 序列化）

| 动作 | 绑定到 | 说明 |
| --- | --- | --- |
| teach / correct | `generate_grounded` + `retrieve_evidence` | `require_evidence=True`：先取可定位证据；取不到则不调模型，改用绑定里的"证据不足"表述（§7.3） |
| ask | `invoke_skill`（socratic） | 取不到问题则退回策略模板；证据不足时由绑定追加"不含引用"说明 |
| hint | `render_template` | **确定性渲染，绝不调用模型**：提示强度是实验自变量，不能因换模型而漂移（§3.4） |
| test | `invoke_skill`（quiz） | 按 mode 取 Skill 结果的不同位置；按判分三态（对/错/判分缺失）选框架文案 |
| reflect / end | `render_template` | 换策略建议与收束语都是可评审的固定文案 |
| assess | `retrieve_evidence` | 只取"有没有可定位证据"这个信号，供状态与输出方式使用（不参与动作选择） |
| recall | `read_memory` | 读该学习者的长期学情记忆；身份取自调用上下文，检索口径来自绑定 |
| diagnose | `invoke_skill`（diagnosis） | 只问状态：模型未接入（not_implemented）也记为调用成功，据此退化为规则化观察（§18.1） |
| update_profile | `write_memory` | 记录由节点构造（来源/置信度/过期策略/可撤回），绑定只声明"写进去" |

绑定表可用的字段（解析逻辑见 `runtime/capabilities.py`）：

| 字段 | 作用 |
| --- | --- |
| `capability` | 要执行的通用原语：`render_template` / `invoke_skill` / `retrieve_evidence` / `generate_grounded` / `read_memory` / `write_memory` |
| `params` | 原语参数；字符串中的 `${字段}` 从决策取值（支持 `a.b` 路径，整串占位保留原类型） |
| `evidence` | `require_evidence=True` 时先执行的能力；取不到可定位证据就不执行主能力 |
| `prefix` / `suffix` | 围绕正文的段落（开头框架、结尾说明），未声明即不加 |
| `fallback` | 主能力失败时的确定性兜底 |
| `insufficient_text` | 证据不足时的确定性表述（不调用模型） |
| `memo` | 声明"同一轮内可复用结果"（仅只读且同轮内稳定的能力，目前只有证据检索）。默认不备忘——写学情、调模型这类有副作用的能力绝不能缓存 |

`prefix` / `suffix` / `fallback` / `insufficient_text` 是同一套**绑定模板块**，两种形态可混用：

```jsonc
{"template": "...", "values": {...}}                        // 固定模板
{"templates": {...}, "select": "<键>", "values": {...}}      // 按取值选模板
```

声明了 `select` 却选不到模板时**必须显式失败**：静默少一段会让学生看到半截回复，
而绑定作者以为自己配好了。

### 6.3 教育能力节点

- **Socratic**：生成递进问题，避免过早泄露答案。
- **RAG**：检索教材与论文，返回可追踪来源。
- **Diagnosis**：从答题、提问和错误轨迹推断知识状态。
- **Quiz**：按知识点、难度与题型生成并评价测验。
- **PaperReader**：面向高校论文阅读的结构化理解与讨论。

能力由**绑定表按名引用**，图节点不知道它们的名字（这使换 Skill 实现不动策略代码）。
能力实现拿到的是 `RuntimeHost`（§7.1 宽面），因此可以调模型、工具与记忆；
图节点拿不到宽面，只能出决策。

### 6.4 策略图状态

建议将图状态限制为可序列化字段：

```text
session_id
learning_goal
current_concept
learner_state_ref
retrieved_evidence_refs[]
attempt_count
hint_level
misconceptions[]
last_assessment
next_action
```

大型文档、模型原始输出和完整历史不直接塞入图状态，只保存 Storage 引用，避免状态膨胀和隐私复制。

**v0.4.1 补充**：状态里的证据一律是**可定位引用**（`document_id / chunk_id / page / source`），
不含教材原文——原文由能力层当次取用、用完即弃。这条边界由 Runtime 的出站归一化保证：
能力内部可以带原文（生成需要），但跨回策略层的结果只保留引用。
学情记忆同理：读回来的记录只在当次解释（取 `record_id` 与误解标签），不写进状态正文。

**同一轮内证据只检索一次**：Assess 的探路与 Teach / Correct 的取原文参数完全相同，
应得同一结果，因此绑定声明 `memo`，由 Runtime 按 `(trace_id, 能力, 参数)` 在单轮内复用。
好处有两层：不多花一次向量查询；更重要的是**不会出现"Assess 判证据充分、
Teach 却说证据不足"的自相矛盾**。跨轮不复用——`trace_id` 每轮不同，天然隔离；
`ctx` 里没有 trace_id 时不备忘（无法判断是否同一轮）。

---

## 七、关键接口与端到端数据流

### 7.1 Runtime Port

端口按**谁能用**分成两个面，方向单向（v0.4.1 修订前，这些方法平铺在同一个协议上，
图节点可以随手调用，边界只能靠自觉）：

```python
class RuntimePort(Protocol):
    """窄面：教学图唯一可依赖的能力面。"""
    async def execute(self, request: dict, ctx: dict) -> dict: ...   # 执行一条声明式决策
    async def emit(self, event: dict) -> None: ...                   # 写入事件轨迹


class RuntimeHost(RuntimePort, Protocol):
    """宽面：Runtime 能力实现可用的完整面。"""
    async def invoke_skill(self, name: str, input: dict, ctx: dict) -> dict: ...
    async def call_tool(self, name: str, arguments: dict, ctx: dict) -> dict: ...
    def generate(self, request: dict, ctx: dict) -> AsyncIterator[dict]: ...
    async def read_memory(self, query: dict, ctx: dict) -> list[dict]: ...
    async def write_memory(self, records: list[dict], ctx: dict) -> None: ...
```

`emit` 留在窄面的理由：事件轨迹是**图自己的可观测行为**（§3.4 可解释性分析的数据源），
不是"调用某个教学能力"，因此不包装成决策。`RuntimeService` 一套方法同时实现两个面，
所以对图只暴露 `RuntimePort` 的类型承诺，对能力实现才移交 `RuntimeHost`。

**决策契约**（`graph/education/contracts.py`，图侧，Runtime 不认识它）

| 字段 | 归属 | 说明 |
| --- | --- | --- |
| `action` | 图 | 教学动作或信息请求名；决定绑定到哪个 capability |
| `concept` / `level` | 图 | 策略算出的产物（如 `next_hint_level` 的结果） |
| `reveal_answer` | 决策 | 是否允许给出最终结论（提示与追问恒为 False） |
| `require_evidence` | 分发器前置条件 | 取不到可定位证据则不执行主能力、不调模型 |
| `require_student_reply` | 图 | 循环控制，Runtime 不参与 |
| `evidence_sufficient` | 图 | 本轮评估结论；据此决定是否发起检索、是否追加"不含引用"说明 |
| `params` | 图 | 执行所需补充输入。**学生正文只在这里，且不得整体写进事件**（§6.4、§13.2） |
| `reason` | 图 | 决策依据，写入决策事件供复盘 |

**结果契约**（`CapabilityResult`，Runtime 回传）

| 字段 | 说明 |
| --- | --- |
| `content` | 面向学生的正文；可能为空（如只取状态的调用） |
| `evidence` | 教材证据的**可定位引用**，不含原文 |
| `records` | 结构化记录（学情记忆等），供图侧解释而不进状态正文 |
| `status` | `success` / `insufficient_evidence` / `no_binding` / `capability_not_found` / `invalid_request` / `error` |
| `capability` / `action` | 这次实际执行了哪个动作、哪个能力（审计用） |
| `error` | 结构化错误（code + message） |
| `metadata` | 能力名、Skill 原状态、降级来源（`degraded_from`）、透传字段等 |

### 7.2 主会话链路

```text
学生文本/语音
  → 交互层标准化输入
  → Runtime 创建或恢复 Session
  → Pedagogical Graph 进入 Assess
      · 决策 recall  → read_memory 能力（读长期学情记忆）
      · 决策 assess  → retrieve_evidence（取可定位教材证据）
      · decide_action 判定本轮动作
  → Graph 在 Teach / Ask / Hint / Correct / Test / Reflect 之间选择
      每个节点只产出决策；Runtime 用绑定表决定调 Skill / 模板 / 模型
  → Runtime 流式输出文本与情感标签
  → 交互层驱动桌宠表达
  → UpdateProfile
      · 决策 diagnose       → invoke_skill(diagnosis)（只问状态，未接入则降级）
      · 决策 update_profile → write_memory 能力（写入学情增量）
  → Runtime 校验并写入 Memory，同时追加事件轨迹
```

### 7.3 失败与回退

- Provider 失败：按能力与成本策略切换备用模型，Graph 无需改动。
- Tool 失败：Runtime 返回结构化错误，Graph 选择重试、替代工具或解释限制。
- RAG 证据不足：禁止伪造引用，转入澄清、保守回答或教师求助节点。
- Plugin 崩溃：隔离插件、记录事件并回退到核心能力。
- 图循环超限：触发 Reflect 或结束节点，避免无限提问。

**装配与绑定失败必须显式（v0.4.1）**：没有为动作注入绑定、绑定指向未注册能力、
绑定引用了请求里不存在的字段、按取值选模板却选不到——这四种情况都返回结构化状态，
**不允许静默兜底成一段自由发挥的文本**：

| 情况 | status | 处理 |
| --- | --- | --- |
| 组合根漏装配 | `no_binding` | 正文为空；决策事件的 `capability_status` 与 `/health` 的 `action_bindings` 可见 |
| 绑定写错能力名 | `capability_not_found` | 同上，并回传已注册能力清单 |
| 绑定引用了不存在的字段 | `invalid_request` | 回传 `missing_fields`，便于定位写错的占位符 |
| 选不到模板 / 兜底模板缺失 | `error(template_not_found)` | 主能力执行前即失败，不产生半截回复 |

节点侧不覆盖这些失败（节点再自带一份兜底文案，等于把 HOW 又搬回图里）；
失败的学生可见表现因此是"空回复"，这是有意的取舍：**装配错误是部署缺陷，
应当响亮地暴露，而不是用一句安慰话遮住**。

---

## 八、技术栈选型

| 层级 | 技术选型 | 说明 |
| --- | --- | --- |
| Runtime | Python 3.12+，自研异步 Core | MVP 优先实现，不把 LangGraph 当 Core |
| API | FastAPI + WebSocket / SSE | 会话接入与流式输出 |
| Pedagogical Graph | LangGraph | 只承载教育策略与状态图 |
| LLM Provider | DeepSeek / OpenAI / 本地 Qwen | 统一 Provider 接口 |
| RAG | PyMuPDF + BGE-M3 + Chroma/FAISS | 教材解析、检索与引用 |
| Storage | SQLite + 文件资源库 + 向量库 | Session 与 Memory 分离 |
| Sandbox | 进程/目录/网络策略适配器 | MVP 可从目录白名单与审批开始 |
| 桌宠前端 | Electron + React + Live2D | D-1 已确认 |
| 语音 | Whisper / 兼容 ASR + Edge-TTS | 通过 Adapter 接入 |
| 辅助语言 | Rust（可选） | 仅在性能测量证明有必要时使用 |

---

## 九、模块划分与目录树

```text
DeepProf/
├── runtime/                         # 自研 DeepProf Runtime
│   ├── core/                        # Core 参考 Pi 的极简设计
│   │   ├── agent.py                 # Agent loop
│   │   ├── ports.py                 # RuntimePort（窄面）/ RuntimeHost（宽面）
│   │   ├── session.py               # Session 生命周期与分支
│   │   ├── events.py                # Event bus / event stream
│   │   └── message.py               # 统一消息模型
│   ├── capabilities.py              # 通用能力原语 + ActionDispatcher（不认识教学词）
│   ├── testing.py                   # FakeRuntime（同时满足窄面与宽面）
│   ├── tools/                       # Tool 注册、校验、审批与执行
│   ├── providers/                   # Model / ASR / TTS Provider
│   ├── memory/                      # 记忆接口、策略与实现
│   ├── storage/                     # Session / Resource / Trace 存储
│   ├── sandbox/                     # 文件、进程、网络隔离
│   └── plugins/                     # Plugin Runtime，思想参考 DSH
│       ├── registry.py
│       ├── lifecycle.py
│       └── manifest.py
├── graph/
│   └── education/                   # LangGraph Pedagogical Graph
│       ├── state.py
│       ├── router.py
│       ├── contracts.py             # PedagogicalDecision / CapabilityResult
│       ├── bindings.py              # 动作 → 能力绑定表（纯数据，组合根注入）
│       ├── nodes/
│       │   ├── assess.py
│       │   ├── teach.py
│       │   ├── ask.py
│       │   ├── hint.py
│       │   ├── correct.py
│       │   ├── test.py
│       │   ├── reflect.py
│       │   └── update_profile.py
│       └── policies/                # 阈值与可评审文案（教师评审入口）
├── skills/
│   ├── socratic/
│   ├── quiz/
│   ├── paper_reader/
│   ├── rag/
│   └── diagnosis/
├── tools/                           # 项目级原子工具实现
├── models/                          # 领域模型与 DTO
├── pet/                             # Live2D / 情感 / 好感度 / 语音
├── api/                             # FastAPI routes / WebSocket
│   └── app.py                       # 组合根：装配工具、Skill 与绑定表
├── config/
├── migrations/                      # SQLite 迁移（§16.5）
├── data/                            # 运行期数据（SQLite / 向量库）；.gitignore 已排除
├── media/                           # 门户与文档配图
├── plugins/                         # 第三方插件【安装目录】+ trusted.json 信任清单
│                                    # ⚠️ 必须留在插件包之外（理由见 config/settings.py 注释）；
│                                    #    与 runtime/plugins/（Plugin Runtime 代码）职责不同
├── scripts/                         # 开发期独立脚本（不在装配链上，仅手工运行）
├── shared/
│   └── contracts/                   # 前后端事件 / IPC / 教学动作契约（§16.4）
├── desktop/                         # Electron + React 桌宠前端（§16.4）
├── tests/
│   ├── runtime/
│   ├── graph/
│   ├── skills/
│   ├── integration/
│   └── test_architecture_boundaries.py   # 三条架构守卫（依赖 / 词汇 / 端口）
├── evaluation/                      # 【未建 · 待建】§16.6 模型评测、§16.8 评分量规
└── docs/
    ├── DESIGNv0.4.1.md
    ├── deepprof_framework_v0.4.html
    └── review/                      # 【未建 · 待建】§16.8 评审记录
```

### 9.1 依赖规则

```text
UI / API → Pedagogical Graph → RuntimePort（窄面：execute / emit）→ ActionDispatcher
                                                                        ↓
                                                            RuntimeHost（宽面）
                                                    Skill / Tool / Provider / Memory
Skills ───────────────────────→ Runtime Host
Plugins ──────────────────────→ Service Registry
```

禁止反向依赖：`runtime/` 不得导入 `graph/education`、`skills`、`pet` 或任何具体数据库 SDK。

**三类可机械检查的约束**（`tests/test_architecture_boundaries.py`，违反即测试失败）：

| 约束 | 检查方式 |
| --- | --- |
| `runtime/` 不反向依赖上层包 | 扫描 `runtime/**` 的绝对 import（AST） |
| `runtime/` 不认识教学动作名 | 扫描 `runtime/**` 的字符串常量（排除 docstring） |
| `graph/` 只碰端口窄面，且窄面不被加宽 | 扫描 `graph/**` 的属性访问（AST），禁止 `.invoke_skill` / `.call_tool` / `.generate` / `.read_memory` / `.write_memory`；并断言 `RuntimePort` 的方法集合恰为 `{execute, emit}` |

**绑定表的装配方向**：`action → capability` 的映射是数据，放在图侧
（`graph/education/bindings.py`），由组合根（`api/app.py`）注入 `RuntimeService`。
Runtime 侧不写任何默认绑定——若写成 Runtime 内部的 `if/elif`，
只是把耦合从结构搬到了词汇，边界并未真正立起来。

---

## 十、MVP 迭代路线

| 阶段 | 目标 | 验收标准 |
| --- | --- | --- |
| MVP-0 | 最小 Runtime | Agent + Session + Event + Provider 跑通终端对话；事件可回放 |
| MVP-1 | Tool / Storage / Memory | 至少一个 Tool；Session 重启恢复；学情记忆可读写 |
| MVP-2 | Pedagogical Graph | Assess → Ask/Teach → Test → UpdateProfile 主链路可测试 |
| MVP-3 | 教育能力 | 接入 Socratic、RAG、Quiz、Diagnosis；引用可追踪 |
| MVP-4 | Plugin Runtime / Sandbox | 能安装一个示例插件；危险 Tool 有审批与边界测试 |
| MVP-5 | 桌宠与语音 | 流式文本、情感标签、语音和动作联动 |
| v1.0 | 申报演示与研究评测 | 完成对照实验、演示脚本、隐私说明和可复现实验记录 |

**D-7 的落地状态（v0.4.1）**：端口分面、两份契约、绑定表与三条守卫测试已实现
（对应 MVP-2 的接口部分）；六类教学动作与 `assess` / `recall` / `diagnose` /
`update_profile` / `end` 均已改为"只产出决策"，节点内不再有正文与执行细节。
后续新增教学动作时不需要动 Runtime：加一条决策 + 一条绑定即可。

---

## 十一、参考项目、借鉴边界与协议合规

### 11.1 核心架构参考

| 项目 | 官方网址 | DeepProf 借鉴点 | 明确不照搬的部分 |
| --- | --- | --- | --- |
| Pi Agent Harness | [GitHub](https://github.com/earendil-works/pi) · [文档](https://pi.dev/docs/latest) | 极简 Agent Core、Session、Event、Tool、Provider、Extension；核心与扩展分离 | 终端编码 Agent 产品形态；默认宿主权限模型 |
| DeepSeek Harness | [GitHub](https://github.com/deepseek-ai/deepseek-harness) · [官网](https://deepseek.com/harness/en/) · [文档](https://deepseek-harness.github.io/) | Plugin Runtime；Skills / Tools / Storage / Sandbox 可组合；追加式会话轨迹 | 完整 Cordis 生态和过度通用的插件面；Developer Preview API 直接绑定 |
| LangGraph | [GitHub](https://github.com/langchain-ai/langgraph) · [文档](https://docs.langchain.com/oss/python/langgraph/overview) | 有状态 Education Workflow、分支、回退、恢复 | 不作为 DeepProf 的通用 Agent Core |

### 11.2 其他借鉴项目

| 项目 | 网址 | 主要借鉴点 |
| --- | --- | --- |
| OpenAI Codex | [github.com/openai/codex](https://github.com/openai/codex) | 工具调用、审批、沙箱与流式交互 |
| dsh-edu | [github.com/1Vewton/dsh-edu](https://github.com/1Vewton/dsh-edu) | 教育版 Harness、先教学再测验 |
| OpenMAIC | [github.com/THU-MAIC/OpenMAIC](https://github.com/THU-MAIC/OpenMAIC) | 多智能体课堂、教学内容与 Skill |
| Socratic Education System | [github.com/HowieWang1121/Socratic-Education-System](https://github.com/HowieWang1121/Socratic-Education-System) | 苏格拉底提问与学情诊断 |
| AI Tutor Release | [github.com/Zenglian990/AI_Tutor_Release](https://github.com/Zenglian990/AI_Tutor_Release) | 教材 RAG、错题本与引用 |
| EduAgent Studio | [github.com/ZZhouWJ/EduAgent-Studio](https://github.com/ZZhouWJ/EduAgent-Studio) | LangGraph 教育 Agent 工程化 |
| mea-pet-public | [github.com/suan-11/mea-pet-public](https://github.com/suan-11/mea-pet-public) | PyQt 桌宠、TTS、记忆与好感度 |
| N.E.K.O | [github.com/Project-N-E-K-O/N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) | 主动交互、情感引擎与具身桌宠 |
| AgentPet | [github.com/cqzaaa/AgentPet](https://github.com/cqzaaa/AgentPet) | Electron + Live2D + 记忆 + MCP |
| PetGPT | [github.com/JulesLiu390/PetGPT](https://github.com/JulesLiu390/PetGPT) | 跨平台桌宠、多模型与本地记忆 |

### 11.3 合规要求

1. 任何代码引入前都要记录：仓库、commit/tag、License、修改文件、用途、核对日期。
2. 只借鉴概念时，在设计记录中写明来源；复制代码时保留版权和许可证要求。
3. GPL/AGPL 或未知协议项目不得直接进入核心代码，必须先完成法律与发布方式评估。
4. Pi 与 DeepSeek Harness 的现行接口可能继续变化，DeepProf 应绑定自有接口而不是绑定其内部 API。

---

## 十二、开发原则与质量要求

| 原则 | 要求 |
| --- | --- |
| 极简 Core | Core 只保留 Agent 生命周期必需能力，新增功能优先放到服务或插件 |
| 接口先行 | Runtime Port、事件 schema、Plugin manifest 先于具体实现 |
| 教育可验证 | 每个图节点有输入、输出、进入条件、退出条件和测试样例 |
| 轨迹可审计 | 模型、工具、记忆与策略决策均可关联到同一 trace |
| 本地优先 | 学情与教材默认本地保存，上传遵循最小必要原则 |
| 小步快跑 | 按第十章逐步实现，先做闭环再扩展 Live2D 和复杂插件 |
| 不硬编码密钥 | 密钥只由 Provider/Secret Adapter 读取，不进入日志和仓库 |
| 不编造引用 | RAG 证据不足时明确说明，不生成不可验证来源 |
| 策略与执行分离 | 节点只产出声明式决策；执行细节写在绑定表数据里，边界由守卫测试兜住（D-7） |
| 失败要响亮 | 装配与绑定错误显式返回结构化状态，不用兜底文案掩盖漏装配 |

最低测试范围：Core 单元测试、Graph 路由测试、Provider 契约测试、Tool 权限测试、Session 恢复测试、Memory 写入审计测试、端到端教学闭环测试、**架构边界守卫测试（依赖方向 / 教学词汇 / 端口分面）**。

---

## 十三、安全、隐私与教育伦理

### 13.1 教育伦理

- DeepProf 不替代教师，不把模型判断包装成权威结论。
- 默认采用启发、提问和分步提示，不直接帮助完成考试作弊。
- 学情诊断必须展示证据和不确定性，不给学生贴永久标签。

### 13.2 隐私

- 不主动索取真实姓名、学校、学号等非必要信息。
- 长期记忆可查看、可纠正、可删除、可关闭。
- Session 轨迹和学习画像分库存储，导出时默认脱敏。

### 13.3 Tool、Plugin 与 Sandbox 安全

- Tool 默认最小权限，高风险动作需显式审批。
- Plugin manifest 声明文件、网络、进程、密钥和数据权限。
- Sandbox 是风险降低措施，不是绝对安全保证；必须配合审计、边界测试与人工审批。
- 第三方内容和检索结果视为不可信数据，不得被当作系统指令执行。

---

## 十四、当前项目配置快照

| 配置项 | 值 |
| --- | --- |
| 项目名 | DeepProf |
| 项目类型 | 大学生创新创业训练计划 |
| 技术亮点 | 分层式 Agent Runtime 与教学策略图协同架构 |
| Runtime | Python 自研；Core 参考 Pi；Plugin 思想参考 DeepSeek Harness |
| 教学编排 | LangGraph Pedagogical Graph |
| LLM 偏好 | DeepSeek / OpenAI / 本地 Qwen |
| 数据策略 | 本地优先；Session 与学习 Memory 分离 |
| 桌宠形象 | 二次元老师形象的 DeepSeek 娘 |
| 当前阶段 | MVP-1 规划 / Runtime 重构 |
| 团队技术栈 | Python 核心 + Rust 辅助，React，FastAPI |
| 文档版本 | v0.4.1（2026-09-19，接口修订） |

---

## 十五、已确认决策

用户于 2026-09-17 确认以下六项决策，具体课程名仍需按 D-2 原则选定；
D-7 于 2026-09-19 确认（v0.4.1 接口修订）。

| 编号 | 决策项 | 结论 | 实施说明 | 状态 |
| --- | --- | --- | --- | --- |
| D-1 | 桌宠前端 | Electron + React | 桌面端使用 Electron + React，Live2D/PNG 和语音通过受控接口接入。 | 已确认 |
| D-2 | 试点课程 | 高质量教材与题库优先 | 先选择资料充足、来源可用且教师能评审的课程；具体课程名由教育组与教师落实。 | 已确认 |
| D-3 | 事件与数据 | 抽象接口 + SQLite | 采用 EventStore / MemoryStore / ProfileStore 抽象；MVP 使用服务器本地 SQLite。 | 已确认 |
| D-4 | 插件范围 | 仅 Python 插件 | MVP 只加载受信、审核后的 Python 插件；manifest 管理能力与生命周期。 | 已确认 |
| D-5 | 隔离方案 | 目录白名单 + 审批 | 演示版使用目录白名单与审批；启用任意代码执行前升级进程/容器隔离。 | 已确认 |
| D-6 | 学情建模 | BKT / IRT / 知识追踪 | BKT 跟踪掌握度，IRT 标定题目与能力，统一接口支持后续知识追踪模型比较。 | 已确认 |
| D-7 | 策略与执行的接缝 | 图只产出声明式决策 | 节点只产出 `PedagogicalDecision`；Runtime 按注入的绑定表执行（`Capability/Skill`）；`RuntimePort` 收窄为 `execute + emit`，能力面拆为 `RuntimeHost`；边界由三条守卫测试兜住。策略层因此退化为纯数据，可反事实回放（§3.4 研究问题 1 / 4）。 | 已确认 |

---


## 十六、团队任务与学习路线

成员与组级职责依据用户提供的分工表：7 名学生、1 名指导教师。刘雨烟于 2026-09-19 加入，创业学院人工智能专业，负责计划书商业部分编写与人工智能框架设计。教育组成员与两名数据组成员的个人主责是本版建议拆分，组内共同负责原分工；姓名按图片转录，正式发布前可在网站数据区修正。

### 16.1 刘俊鹏｜项目负责人

总体架构设计、Agent Harness 与后端、进度管理、申报与结题材料。

- **模块**：`runtime/core/ · runtime/plugins/ · runtime/capabilities.py · api/ · docs/`
- **第一轮任务**：
  1. 冻结端口与契约（D-7）：`RuntimePort`（窄面 `execute` / `emit`）、`RuntimeHost`（宽面）、
     `PedagogicalDecision` / `CapabilityResult`、消息与事件 schema；提供可运行的 FakeProvider / FakeRuntime。
  2. 实现 Session 创建/恢复、工具调用与追加式事件日志；与数据组对接 EventStore。
  3. 作为**组合根**装配工具、Skill 与绑定表（`api/app.py`），并在 `/health` 暴露 `action_bindings` 供装配自检。
  4. 实现仅受信 Python 插件的 manifest、注册、启停和审批；统筹集成与申报材料。
- **验收**：一条带 trace_id 的请求完成模型→工具→事件闭环；重启可恢复；失败与取消可观测；
  三条架构守卫测试全绿（依赖方向 / 教学词汇 / 端口分面）。
- **交接**：给全员：接口契约、Mock 服务与集成样例。接收：学情仓储、教育策略与桌宠事件消费结果。

学习顺序：

1. [FastAPI 教程](https://fastapi.tiangolo.com/tutorial/)：完成请求模型、依赖注入与流式响应示例。
2. [Pi Core 参考](https://github.com/earendil-works/pi)：阅读 Agent、Session、Event 的职责边界，写一页映射笔记。
3. [DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness)：学习插件服务与生命周期，先实现最小 Python 插件注册器。

### 16.2 许阳毅｜教育层开发

教育组共同负责教材 RAG、苏格拉底 Skill、错题本与学情诊断；建议主责 RAG 与课程语料。

- **模块**：`skills/rag/ · tools/retrieval/ · data/course_manifest/`
- **第一轮任务**：
  1. 按教材授权、题库质量、知识点标注和教师可评审性选择试点课程。
  2. 完成 PDF→章节/页码→切块→向量→检索，保留 document_id、page、chunk_id。
  3. 整理至少 20 条人工标注的检索问题，验证引用定位与无证据返回。
- **验收**：检索返回可定位的原文片段；无命中明确返回 insufficient_evidence；记录 Recall@k 与失败样例。
- **交接**：给孙一新：检索工具和引用结构。给数据组：course_id、concept_id、题目及来源映射。

学习顺序：

1. [PyMuPDF 文档](https://pymupdf.readthedocs.io/en/latest/)：实践抽取一章教材并保存页码。
2. [BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3)：了解输入、编码与检索示例；先跑小语料。
3. [LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview)：理解检索如何作为**能力**被绑定表引用（节点不直接调用，见 §4.4）。

### 16.3 孙一新｜教育层开发

教育组共同负责教材 RAG、苏格拉底 Skill、错题本与学情诊断；建议主责教学策略与测验闭环。

- **模块**：`graph/education/`（含 `policies/`、`bindings.py`、`contracts.py`、`nodes/`）· `skills/socratic/ · skills/quiz/`
- **第一轮任务**：
  1. 用 FakeRuntime 实现 Assess→条件路由→Teach/Ask/Hint/Correct/Test→UpdateProfile；
     节点只产出 `PedagogicalDecision`，正文与执行细节写进 `bindings.py`（§6.2 绑定表）。
  2. 编写包含先验不足、连续答错、无证据、主动求讲解的教学样例。
  3. 与数据组定义答题事件与学情更新；加入最大轮次、退出与 Reflect 回退。
- **验收**：分支测试覆盖六类教学动作；学生停止时退出；无证据不编造引用；图不能形成无限追问；
  节点源码里不再出现提示模板、提示词、证据获取与 Skill 名（守卫测试会拦）。
- **交接**：向 RAG 组消费证据（经绑定表引用，不直接调用）；向数据组发送 Attempt；
  向前端输出文本、教学动作和情感标签；**向教师提交 `policies/` + `bindings.py` 作为教学策略评审材料**（§16.7）。

学习顺序：

1. [LangGraph 官方文档](https://docs.langchain.com/oss/python/langgraph/overview)：先做带条件边的小图，再接真实 Runtime。
2. [Socratic Education System](https://github.com/HowieWang1121/Socratic-Education-System)：比较递进提问和学情诊断的实现。
3. [OpenMAIC](https://github.com/THU-MAIC/OpenMAIC)：学习教学能力组织；整理可复用的课程与测验模式。

### 16.4 张钧翔｜桌宠前端开发

桌宠形象集成、情感反馈、好感度交互与语音链路。

- **模块**：`desktop/ · pet/ · shared/contracts/`
- **第一轮任务**：
  1. 建立 Electron + React 桌面壳，使用 Mock 事件流展示回复、加载、错误、取消与重连。
  2. 先用 PNG 跑通表情状态，再接 Live2D；把教学动作映射为可解释的表现。
  3. 接入 ASR/TTS 与可中断播放；通过 preload 暴露最小 IPC 接口。
- **验收**：桌面应用可启动；流式文本与语音可停止；断线重连不重复展示；渲染进程不持有模型密钥。
- **交接**：消费刘俊鹏提供的会话事件；与孙一新确认教学动作；向数据组提交经用户授权的偏好更新。

学习顺序：

1. [React 入门](https://react.dev/learn)：实现消息流、组件状态与可取消请求。
2. [Electron 教程](https://www.electronjs.org/docs/latest/tutorial/tutorial-prerequisites)：理解 main、preload、renderer 与打包。
3. [Electron 安全指南](https://www.electronjs.org/docs/latest/tutorial/security)：落地 contextIsolation 与受控 IPC。
4. [Live2D SDK 教程](https://docs.live2d.com/en/cubism-sdk-tutorials/top/)：从官方最小示例加载模型并切换表情。

### 16.5 谢浪｜记忆与数据

数据组共同负责五维记忆系统、数据存储、学情画像与测试；建议主责仓储和数据生命周期。

- **模块**：`runtime/storage/ · runtime/memory/ · migrations/`
- **第一轮任务**：
  1. 定义 EventStore、MemoryStore、ProfileStore 抽象，MVP 使用服务器本地 SQLite。
  2. 区分短期、工作、长期、情感、事件记忆；实现来源、版本、过期与删除字段。
  3. 实现迁移、事务、幂等写入、备份恢复与用户级隔离；对外只暴露 API。
- **验收**：重复 event_id 不重复写入；重启恢复一致；备份可还原；一个学生不能读取另一学生记录。
- **交接**：给刘俊鹏：EventStore 和 Session 持久化。给欧阳文凯：答题历史和版本化画像仓储。

学习顺序：

1. [Python sqlite3](https://docs.python.org/3/library/sqlite3.html)：练习参数化 SQL、事务与连接管理。
2. [SQLite WAL](https://www.sqlite.org/wal.html)：了解读写并发限制；数据库放本机磁盘，不使用网络共享文件。
3. [pytest 入门](https://docs.pytest.org/en/stable/getting-started.html)：编写隔离数据库 fixture 与恢复测试。

### 16.6 欧阳文凯｜记忆与数据

数据组共同负责五维记忆系统、数据存储、学情画像与测试；建议主责学情模型与评测。

- **模块**：`models/learner/ · evaluation/ · tests/integration/`
- **第一轮任务**：
  1. 与教育组定义 Attempt：学生、题目、知识点、正误、时间、提示次数和来源。
  2. 先实现 BKT 掌握度基线；用 IRT 标定题目与能力，输出模型版本和不确定性。
  3. 通过 LearnerModel 接口比较后续知识追踪模型；按学生划分数据并保持时间顺序。
- **验收**：固定样例更新可复现；训练/测试无学生与未来信息泄漏；输出 AUC、LogLoss、校准和样本量，单类数据不计算 AUC。
- **交接**：消费谢浪的答题记录；向孙一新返回知识点掌握估计和测验建议；由教师审核解释是否合理。

学习顺序：

1. [pyBKT](https://github.com/CAHLR/pyBKT)：运行示例，区分初始掌握、学习、猜测与失误参数。
2. [py-irt](https://github.com/nd-ball/py-irt)：从 1PL/Rasch 开始，理解能力与题目难度。
3. [pyKT](https://github.com/pykt-team/pykt-toolkit)：学习知识追踪数据格式与基准；数据充足后比较扩展模型。

### 16.7 刘雨烟｜商业与框架设计

创业学院人工智能专业，2026-09-19 加入，负责计划书商业部分编写与人工智能框架设计。

- **模块**：`DESIGNv0.4.1.md · docs/（计划书第 4、7 章）`
- **第一轮任务**：
  1. 完成计划书商业部分：商业模式与运营规划、目标客户与获客路径、成本与预算口径，与第 2 章市场分析使用同一数据口径。
  2. 参与人工智能框架设计评审：分层架构、`RuntimePort`（窄面）与 `RuntimeHost`（宽面）、
     动作—能力绑定表与解耦边界，确认守卫条件可机械检查。
  3. 对齐 `DESIGNv0.4.1.md` 与计划书：成员分工、里程碑与预算条目在两份文档中保持一致。
- **验收**：商业章节的每个数字都有口径与来源标注；框架设计与设计文档一致，接口改动同步落到
  文档与门户；两份文档不出现互相矛盾的分工与预算。
- **交接**：给刘俊鹏：商业章节初稿与框架评审意见。给唐欢容：待审核的商业与经费口径。
  给教育组与数据组：成本与资源假设的核对清单。

学习顺序：

1. [FastAPI 教程](https://fastapi.tiangolo.com/tutorial/)：理解后端接口形态，便于与 Runtime 端口对齐。
2. [LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview)：对照教学策略图与执行层的接缝，理解框架设计的边界。
3. [Pi Core 参考](https://github.com/earendil-works/pi)：理解 Agent、Session、Event 的职责边界，便于评审框架分层。

### 16.8 唐欢容｜指导教师

教学法与学术指导、试点课程审核、项目评审把关。

- **模块**：`docs/review/ · evaluation/rubrics/`
- **第一轮任务**：
  1. 审核试点课程、教材与题库来源，检查知识点和难度映射。
  2. 用统一量表评审提问、提示、纠错与引用质量，反馈给教育组。
  3. 审查学情指标解释、实验设计与申报材料，确认每阶段通过条件。
- **评审入口（D-7 之后）**：教学策略现在集中在两个文件——
  `graph/education/policies/`（阈值与全部文案）与 `graph/education/bindings.py`
  （每个动作"怎么做"的数据）。审这两处即可覆盖策略，不必读节点代码。
- **验收**：形成可追溯的评审记录、修订建议和阶段结论；明确真实实验与演示数据的边界。
- **交接**：向刘俊鹏给出阶段评审意见；向教育组与数据组给出教学样例和评价标准。

学习顺序：

1. [OpenMAIC 教学示例](https://github.com/THU-MAIC/OpenMAIC)：观察课程组织与测验形式，形成评审维度。
2. [pyBKT 模型说明](https://github.com/CAHLR/pyBKT)：理解掌握度估计的假设，审查输出解释。
3. [LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview)：对照教学流程检查节点条件与回退是否合理。


### 16.9 四阶段联合交付

阶段是相对执行顺序，不代表已完成或承诺日期。

| 阶段 | 团队交付 | 集成门槛 |
| --- | --- | --- |
| 01 接口与学习 | 负责人冻结 schema；教育组整理课程样本；前端跑 Mock；数据组定义仓储和 Attempt | 全员用同一份请求、事件、引用和答题样例 |
| 02 独立最小模块 | Runtime、检索、策略图、桌面端、SQLite、BKT 分别跑通 | 各模块附启动说明、输入输出和失败样例 |
| 03 联调闭环 | 提问→检索/提示→作答→学情更新→桌宠反馈 | trace_id 可追溯，重复事件不重复记分，退出可取消 |
| 04 教师评审与演示 | 检索与教学评审、模型指标、备份恢复、演示脚本 | 教师完成评审记录，负责人归档可复现材料 |

## 十七、部署与协作

### 17.1 两种部署对象

本次交付的**团队门户**是可部署的共享资料与架构站点。DeepProf 教育产品的 Electron、FastAPI Runtime、SQLite 与模型服务属于本文设计的后续实现，不能把门户发布误称为教育产品已经上线。

```text
成员浏览器 → 团队服务器 Nginx → 门户 HTML + DESIGNv0.4.1.md
                               （本次交付）

学生 Electron + React → HTTPS/WebSocket → FastAPI Session 接入
                                         ├─ 教学图 → RuntimePort
                                         └─ Runtime 服务 → SQLite / 模型适配
                               （后续产品部署）
```

### 17.2 门户交付与运行

- `deepprof_framework_v0.4.html`：单文件门户与架构图，无在线字体或 CDN 依赖。
- `team-site/build.mjs`：同步 HTML 与 Markdown 到部署目录。
- `team-site/Dockerfile`、`compose.yaml`、`nginx.conf`：Nginx 静态服务。
- `team-site/README.md`：本地预览、服务器部署、更新和访问检查。

服务器执行 `docker compose up -d --build` 后，门户映射至宿主机 8080 端口；团队可通过内网 IP 访问。已有反向代理可转发到该端口。涉及公网域名时由服务器管理员配置 HTTPS 与访问策略。未提供服务器地址或凭据，本版完成可部署包，不声称已远程部署。

共享资料由负责人合并发布。门户不把浏览器本地勾选当作团队共享进度；如需进度协作，另行增加用户身份、权限与服务端数据存储。

### 17.3 产品端数据部署约束

MVP 数据库只保存在 FastAPI 所在服务器本机磁盘；Electron 通过 API 访问。采用事务、唯一键和适度写入队列管理并发，不把 SQLite 文件放在网络共享目录。Session 与学习画像逻辑分表/仓储，按 learner_id 隔离。

产品保留本地学习模式；服务端协作模式需明确告知数据存储位置。本门户不保存真实学生作答或学情记录。

## 十八、学情模型与联调契约

### 18.1 D-6 的实施顺序

BKT、IRT 与其他知识追踪模型采用统一 `LearnerModel` 接口，但负责不同估计任务：

| 模型 | 输入 | 输出 | 首轮工作 |
| --- | --- | --- | --- |
| BKT | 按时间排序的知识点作答 | 知识点掌握概率 | 首个可解释、可复现基线 |
| IRT | 学生—题目作答矩阵 | 学生能力、题目难度及相应模型参数 | 从 1PL 开始，验证数据能否支持稳定估计 |
| 后续知识追踪 | 长期交互序列与题目/知识点特征 | 后续作答预测或掌握估计 | 在样本和标注充分时比较，不预设优于基线 |

两类概率和能力分数不可直接混加。Graph 读取结构化估计及其模型版本、证据数量和不确定性；冷启动时返回信息不足，并采用经教师审核的保守教学策略。

### 18.2 交接字段

| 对象 | 必需字段 | 责任接口 |
| --- | --- | --- |
| SessionRequest | session_id, learner_id, request_id, content | 前端 → 后端 |
| RuntimeEvent | event_id, session_id, trace_id, sequence, type, payload | 后端 → 前端/Storage |
| Evidence | document_id, chunk_id, page, text, source | RAG → 教学图 |
| **PedagogicalDecision** | action, concept, level, require_evidence, evidence_sufficient, params | 教学图 → Runtime（§7.1） |
| **CapabilityResult** | content, evidence, records, status, capability, metadata | Runtime → 教学图（§7.1） |
| Attempt | attempt_id, learner_id, item_id, concept_ids, correct, timestamp, hint_count | 教育组 → 数据组 |
| LearnerEstimate | learner_id, concept_id, model_type, model_version, estimate, evidence_count, uncertainty | 数据组 → 教学图 |

一个学习者的数据不能因会话切换被覆盖。事件使用唯一标识实现幂等；流式重连按 sequence 恢复，避免重复渲染、重复记分。图 checkpoint 保存教学状态和 Memory 引用，Session 负责交互事实，两者通过 session_id/trace_id 关联。教学图上交决策、Runtime 回传结果时，**教材原文与学情记录都不进图状态**，只保留可定位引用（§6.4）。

### 18.3 首轮验收记录模板

每个模块提交：负责成员、版本、启动方式、输入样例、预期输出、实际输出、失败案例和交接对象。教育样例由唐欢容审阅，接口问题由刘俊鹏归口。任何实验结果必须来自实际运行；当前文档与门户不填造成功率或完成百分比。

---

## 附录 A：门户与架构图使用说明

新版 `deepprof_framework_v0.4.html` 同时是架构图与团队门户，可直接打开。首页包含七位成员任务入口，架构页显示教学层与 Runtime 服务的依赖关系，资料页可按成员查看学习路径，决策页列出 D-1—D-7。

可部署副本位于 `docs/team-site/dist/index.html`，设计文档下载副本位于 `docs/team-site/dist/DESIGNv0.4.1.md`。统一修改根目录的 HTML 与 Markdown 后，**从项目根目录**执行构建，把源同步进 dist：

```sh
node docs/team-site/build.mjs
```

`docs/team-site/` 里还有 `nginx.conf`、`Dockerfile`、`compose.yaml` 与 `README.md`（部署方法、验证清单、功能边界）。**只改 dist 会在下次构建时被覆盖**，源始终是根目录那两个文件。

部署到已有 Nginx（例如阿里云 Linux 云服务器）时，把 `dist/` 内容放进静态目录即可（页面用 hash 导航，不需要后端路由重写），
具体步骤与示例 server 块见 `docs/team-site/README.md`。此交付未连接真实服务器，也未执行远程部署。

---
 
## 附录 B：核心术语

| 术语 | 含义 |
| --- | --- |
| Agent Runtime | 管理模型、工具、状态、事件、权限与扩展生命周期的执行底座 |
| Harness | 让模型能够理解环境、调用能力并持续工作的运行支撑系统 |
| Pedagogical Graph | 把教学决策表示为节点、状态与转移条件的策略图 |
| PedagogicalDecision | 图产出的**声明式决策**：动作 + 参数 + 依据，不含任何正文（WHAT） |
| CapabilityResult | Runtime 回传的结构化结果：正文 / 可定位引用 / 结构化记录 / 状态（HOW 的产出） |
| Capability | 与教学无关的通用执行原语：`render_template`、`invoke_skill`、`retrieve_evidence`、`generate_grounded`、`read_memory`、`write_memory` |
| Binding（绑定表） | `action → capability` 的映射数据，放在图侧，由组合根注入 Runtime |
| RuntimePort（窄面） | 图唯一可依赖的端口：`execute` + `emit` |
| RuntimeHost（宽面） | 能力实现可用的完整端口：Skill / Tool / Provider / 记忆读写 |
| Skill | 可按需加载的任务方法与流程知识 |
| Tool | 具有结构化参数与执行副作用的原子能力 |
| Provider | 对外部模型或服务的统一适配接口 |
| Session | 可恢复、可分支、可审计的一次连续交互上下文 |
| Event Stream | 追加式记录 Runtime 与教学决策轨迹的事件序列 |
| Plugin Runtime | 负责扩展发现、依赖、挂载、生命周期与权限治理的运行环境 |

---


