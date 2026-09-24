# DeepProf 项目结构化设计文档

> **版本**：v0.6.1（两大前端模块 + Codex 式桌宠挂件 + API-Key 登录 + 资源库；交付路线改为 v1.0 演示版 + MVP-1～MVP-5）
> **更新日期**：2026-09-22
> **文档性质**：项目总纲 + 系统架构设计 + 工程实施基线
> **上一版本**：`DESIGNv0.6.md` v0.6（2026-09-21）
> **配套架构图**：`deepprof_framework_v0.6.html`（待与本文同步更新）

---

## 版本变更记录

| 版本 | 日期 | 变更摘要 | 作者 |
| --- | --- | --- | --- |
| v0.1 | 2026-09-14 | 初版：角色设定、三大核心侧、技术栈、模块划分 | 团队 |
| v0.2 | 2026-09-16 | 新增五层架构、端到端链路与框架图维护说明 | 团队 |
| v0.3 | 2026-09-17 | 重构为“自研 Agent Runtime + LangGraph Pedagogical Graph”；新增 Pi、DeepSeek Harness 参考；明确 Core、Plugin、Skills/Tools/Storage/Sandbox 边界；更新目录树、数据流与参考网址 | 团队 |
| v0.4 | 2026-09-17 | 确认 D-1—D-6；新增团队任务与学习路线、自有服务器门户部署方案、学情模型接入与联调契约 | 团队 |
| v0.4.1 | 2026-09-19 | **接口修订（D-7）**：教学图只产出声明式决策 `PedagogicalDecision`；`RuntimePort` 收窄为 `execute + emit`，能力面拆为 `RuntimeHost`；新增 `CapabilityResult` 与绑定表（action → capability 由组合根注入）；配套三条架构守卫测试 | 团队 |
| v0.5 | 2026-09-21 | **统一前端与多模型接入**：交互层升级为 DSH Desktop 式 Electron 工作台 + Pi 风格 CLI/TUI + DeepProf 娘桌宠；新增 `Client SDK`、Runtime Supervisor、Provider Hub 与多套 OpenAI-compatible API Profile；扩展目录树、MVP、部署、团队分工与守卫测试 | 团队 |
| v0.6 | 2026-09-21 | **前端收敛与工程重排**：① 前端明确为两个主模块——按 DSH Desktop 真实配置落地的工作台 App 与 Pi 风格 CLI；桌宠降级为 Codex 兼容挂件（`pet.json + spritesheet.webp`）。② 首次启动改为 Codex 式 API-Key 接入全流程，不做账号系统，适配 OpenAI-compatible 云端模型。③ 工作台内置资源库：真实教材选取、学生上传、分类归档、爬虫/导入并与 RAG 引用管线对接。④ 交付路线改为“负责人先做 v1.0 演示版 → MVP-1～MVP-5 模块维护”；更新团队职责（刘雨烟仅负责计划书商业部分；指导教师职责按选题→文档撰写全过程重述） | 团队 |
| v0.6.1 | 2026-09-22 | **MVP-1 / MVP-2 交付记录**：① MVP-2 —— `graph/education`（契约/状态/策略/路由/八节点/绑定表/builder）、`skills/`（socratic·rag·quiz·diagnosis·paper_reader）、`models/learner` 契约落地；Runtime 侧补回 `invoke_skill.content_field/passthrough`、`generate_grounded` 的提示词模板与证据原文拼装、`retrieve_evidence` 的 Skill 入口；补回 `pedagogy.attempt` 事件契约。② MVP-1 —— 出验收记录，新增跨 Provider Profile 集成测试（证明“换 Profile 不改教学策略”），修复降级兜底仍附引用的缺陷。③ §9 目录树、§9.1 依赖规则、§10.4 交付进度按实际实现校准。验收记录：`docs/MVP-1_验收记录.md`、`docs/MVP-2_验收记录.md` | 团队 |

v0.5 的架构结论在 v0.6 中**继续有效**：Runtime—Pedagogy 接缝（D-7）、`PedagogicalDecision / RuntimePort / RuntimeHost`、绑定表、Provider Hub、Session/Event 与 Client SDK 均不回退。v0.6 只重构**产品层（前端形态、登录方式、资源来源）与实施层（路线、分工）**。

v0.6.1 **不改任何架构主张**：只记录 MVP-1 / MVP-2 的交付事实、把 §9 目录树与 §9.1 依赖规则对齐到实际实现，并说明被精简掉的 Runtime 能力为何必须补回（见 §10.4 交付进度）。

---

## 目录

- [一、文档使用说明](#一文档使用说明)
- [二、助手角色定位](#二助手角色定位)
- [三、项目背景、目标与创新主张](#三项目背景目标与创新主张)
- [四、v0.6 总体架构](#四v06-总体架构)
- [五、DeepProf Runtime 设计](#五deepprof-runtime-设计)
- [六、Pedagogical Graph 设计](#六pedagogical-graph-设计)
- [七、关键接口与端到端数据流](#七关键接口与端到端数据流)
- [八、技术栈选型](#八技术栈选型)
- [九、模块划分与目录树](#九模块划分与目录树)
- [十、交付路线：v1.0 演示版 + MVP-1～MVP-5](#十交付路线v10-演示版--mvp-1mvp-5)
- [十一、参考项目、借鉴边界与协议合规](#十一参考项目借鉴边界与协议合规)
- [十二、开发原则与质量要求](#十二开发原则与质量要求)
- [十三、安全、隐私与教育伦理](#十三安全隐私与教育伦理)
- [十四、当前项目配置快照](#十四当前项目配置快照)
- [十五、已确认决策](#十五已确认决策)
- [十六、团队任务与学习路线](#十六团队任务与学习路线)
- [十七、部署与协作](#十七部署与协作)
- [十八、学情模型与联调契约](#十八学情模型与联调契约)
- [十九、前端：两大模块、桌宠挂件与 Provider Hub](#十九前端两大模块桌宠挂件与-provider-hub)
- [二十、资源库：教材选取、上传、分类与爬虫](#二十资源库教材选取上传分类与爬虫)
- [附录 A：门户与架构图使用说明](#附录-a门户与架构图使用说明)
- [附录 B：核心术语](#附录-b核心术语)

---

## 一、文档使用说明

本文档是 DeepProf v0.6 的架构与实施基线；v0.4.1 的 Runtime—Pedagogy 接缝（D-7）继续作为不可破坏的基础约束，v0.5 的 Provider Hub 与 Client 契约继续有效。新增设计时应遵循以下规则：

1. 正文章节使用中文序号；小节使用 `4.1` 形式，并同步更新目录。
2. 未定事项使用 `{{TODO: 说明}}`，已决策事项写明结论与日期。
3. 架构图与本文档必须同步更新；图中组件名称应与第九章目录树一致。
4. “参考项目”只代表设计借鉴，不代表直接复制实现；实际引入代码前必须核查版本与 License（§11.3、§11.4）。
5. 涉及前端形态、登录方式、资源来源与交付路线的表述，以本文档为准，不再沿用 v0.5 的“三端并列”旧写法。

---

## 二、助手角色定位

### 2.1 角色身份

- **角色名**：DeepProf 项目首席开发助手
- **定位**：精通教育 AI、Agent Runtime、RAG、教学策略编排、桌面工作台、CLI、桌宠交互和全栈开发的技术助手
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

DeepProf 是面向高等教育的个性化伴学智能体。它把教育智能体能力、长期学习记忆与持续存在的桌面交互结合起来，在长期会话中逐步理解学生的知识状态、学习习惯和情感偏好。

v0.6 明确产品形态：学生端由**两个主模块**和**一个挂件**组成——

- **DeepProf Desktop（工作台）**：主 App，按 DSH Desktop 的真实工程配置落地（§19.2）；
- **DeepProf CLI**：第二主入口，Pi 风格的清晰终端全流程（§19.7）；
- **DeepProf Pet（桌宠）**：Codex 式桌面挂件，不再作为独立前端（§19.8）。

三者共享同一学生、同一课程、同一 Session、同一 Memory 和同一 Runtime。

### 3.2 三大核心侧与前端载体

| 教育侧 | Agent 侧 | 宠物侧 |
| --- | --- | --- |
| 苏格拉底式提问 | 自研 Runtime | Codex 兼容宠物挂件 |
| 教材 RAG | Session / Event / Tool | 表情与语音反馈 |
| 错题与测验 | Provider / Memory / Plugin | 好感度与主动陪伴 |
| 学情诊断 | Storage / Sandbox | 个性化表达 |

**前端载体（v0.6）**：`DeepProf Desktop`（主工作台）+ `DeepProf CLI`（键盘优先第二入口）+ `DeepProf Pet`（桌面挂件）。三者只依赖统一 `Client SDK` 与 Session/Event 协议，不各自持有 Agent、Memory 或 Provider；桌宠挂件只做事件投影与轻交互（§19.8）。

**资源来源（v0.6）**：课程教材、讲义、论文与题库统一从工作台的**资源库**中选取与管理；资源库支持学生上传、分类归档、爬虫/导入（§20）。

### 3.3 架构创新主张

> **面向高等教育的分层式 Agent Runtime 与教学策略图协同架构**

核心思想不是“直接选择一个 Agent 框架”，而是把系统拆成两个可以独立演进和独立评测的层次：

- **底层 DeepProf Runtime 负责 Agent 能力**：Session、Tool、Model、Memory、Event、Plugin、Storage、Sandbox。
- **上层 Pedagogical Graph 负责教育策略**：什么时候讲、什么时候问、什么时候提示、什么时候纠错、什么时候测试、什么时候更新学情。

这种拆分使“Agent 能做什么”与“教师此刻应该怎么教”彻底解耦。系统的研究价值由此从“组合 RAG + LangGraph + 桌面端”提升为可被实验验证的 Runtime—Pedagogy 协同机制。

**解耦靠什么落地**（D-7，详见 §4.4）：上层只产出**声明式决策**
（`PedagogicalDecision`：动作 + 参数 + 依据），下层按**注入的绑定表**决定怎么执行。
这与常见的做法有实质差别：

| 常见做法 | DeepProf 的做法 | 得到的性质 |
| --- | --- | --- |
| 策略写在节点/Skill 内部，正文与调用一起产出 | 节点只产出决策数据，正文与调用由绑定表落地 | 策略层退化为**纯数据**，可反事实回放与离线比较 |
| 两者通过“约定不要互相调”来隔离 | 端口分面 + 架构守卫测试，违反即失败 | 边界可机械检查，不依赖自觉 |
| 换模型/换实现要改策略代码 | 改绑定表数据即可 | 换执行方式不动策略 |

因此“Agent 是策略的自动化执行”这句话在本项目里是可证的：
策略的产物里没有一行正文、没有任何能力名，唯一的下行通道是 `execute`。

**v0.5 保留、v0.6 沿用“前端—Runtime”第二条解耦边界**：Desktop、CLI 与 Pet 只是 Presentation Surface。它们共享 `ClientCommand / RuntimeEvent / Session` 协议，通过 Gateway 进入同一 DeepProf Runtime；任何 Surface 都不能直接调用模型 SDK、Tool、MemoryStore 或数据库。因此更换桌面壳、CLI 实现或桌宠形象，不改变教育语义和学习数据事实源。

### 3.4 可研究的核心问题

1. 教学策略图是否比单轮提示词更能稳定执行苏格拉底式教学？
2. Runtime 与 Pedagogical Graph 解耦后，是否能降低新增教学策略的工程成本？
3. 学情记忆反馈是否能提高后续提示、测验和纠错的个性化质量？
4. 事件轨迹是否能支持教学过程复盘、策略比较和可解释性分析？
5. 同一学习 Session 在工作台与 CLI 之间连续迁移时，是否能降低学习任务切换成本并保持教学行为一致？

D-7 使问题 1 与 4 有了更直接的实验手段：决策是纯数据，因此可以**反事实回放**——
固定同一段学情轨迹，只替换决策里的级别或证据条件，重跑执行层即可比较输出差异，
不必重跑整个模型链路。

---

## 四、v0.6 总体架构

### 4.1 分层总览

| 层级 | 核心职责 | 主要组件 | 设计来源 |
| --- | --- | --- | --- |
| 交互层 | 统一接收命令并投影 Runtime 事件；提供工作台、CLI 与桌宠挂件 | Electron 工作台（DSH Desktop 真实配置）、Pi 风格 CLI/TUI、Codex 兼容桌宠挂件、Client SDK | DSH Desktop 真实实现参考、Pi UX 参考、Codex 宠物格式参考 |
| Pedagogical Graph | 决定教学时机、顺序、分支和回退 | Teach、Ask、Hint、Correct、Test、Update Profile | LangGraph |
| Education Capability | 提供可复用教学能力 | Socratic、RAG、Diagnosis、Quiz、PaperReader | 教育项目与 DeepProf 自研 |
| DeepProf Runtime | 提供通用 Agent 执行能力与生命周期 | Agent、Session、Event、Message、Tool、Provider、Memory、Plugin | Core 参考 Pi；Plugin 思想参考 DeepSeek Harness |
| 基础设施层 | 持久化、隔离、模型与外部服务 | Storage、Sandbox、SQLite、Vector DB、Provider Hub、OpenAI-compatible APIs / 本地模型、资源库爬虫与索引 | DeepProf 自研适配 |

层的**职责**如上表；层之间的**接缝**见 §4.4：Pedagogical Graph 与 Education Capability
之间不是直接调用，而是“图出决策 → 绑定表 → Runtime 执行”。因此这张表读作
“谁负责什么”，不读作“谁调用谁”。

### 4.2 核心结构图

```text
                                  DeepProf v0.6
                                        │
        ┌───────────────────────────────┼───────────────────────────────┐
        │                               │                               │
 DeepProf Desktop                 DeepProf CLI                  DeepProf Pet
 DSH Desktop 真实配置式            Pi 风格 TUI                    （桌宠挂件）
 Electron 工作台                  第二主入口                      Codex 兼容宠物包
        │                               │                               │
        └───────────────────────────────┼───────────────────────────────┘
                                        │
                              DeepProf Client SDK
                      Command / Session / Event / Approval
                                        │
                           HTTPS / WebSocket / Local IPC
                                        │
                              DeepProf Gateway/API
                                        │
                              Pedagogical Graph
                       Assess / Teach / Ask / Hint / Test
                                        │
                         PedagogicalDecision (WHAT)
                                        │ RuntimePort.execute
                                        ▼
                         DeepProf Agent Runtime (HOW)
       Agent · Session · Event · Message · Tool · Provider · Memory · Plugin
                     │                          │
               Education Capability       Infrastructure
             RAG · Socratic · Quiz      Storage · Sandbox · Vector DB
             Diagnosis · PaperReader    资源库（教材/论文/上传/爬虫索引）
                     │                          │
                     └──────────┬───────────────┘
                                ▼
                          LLM Provider Hub
                 OpenAI-compatible Profiles / Local Adapters
```

> v0.6 不改变 v0.4.1 的 D-7 接缝：教学图仍只产出 `PedagogicalDecision`，Runtime 按注入绑定表执行。v0.5 的“前端只通过 Client SDK / Gateway 进入系统”边界继续有效；v0.6 补充一条：**登录与密钥只存在于工作台 Main 进程与 CLI 凭据层，Renderer、事件流与日志中永远没有 API Key。**

### 4.3 关键设计决策

| 决策 | v0.2 | v0.4 / v0.4.1 / v0.5 | v0.6 |
| --- | --- | --- | --- |
| Agent 核心 | LangGraph 同时承担对话状态机和教学编排 | 自研 Runtime 承担通用 Agent 生命周期 | 不变 |
| 教学策略 | Skill 内部逻辑 | 可视、可测、可分支的策略图 | 不变 |
| 策略与执行的接缝 | 图节点内部直接调 Skill / 模型 / 记忆 | 图只产出声明式决策；Runtime 按绑定表执行（D-7） | 不变 |
| 前端形态 | 单一 Electron/桌宠入口 | 三形态并列（工作台 + CLI + 桌宠） | **收敛为两个主模块（工作台 + CLI）+ 一个挂件（桌宠）** |
| 桌面壳 | 未定 | “DSH Desktop 式”薄壳 | **按 DSH Desktop 真实配置落地**（隔离运行进程、环回地址、沙箱渲染器、安全模式、更新机制） |
| 登录方式 | 未定义 | 未定义 | **不做账号系统；Codex 式 API-Key 接入全流程**，适配 OpenAI-compatible 模型 |
| 资源来源 | 学生自备资料 | 自备资料 | **工作台内置资源库**：真实教材选取、上传、分类、爬虫/导入 |
| 模型接入 | Provider 由后端代码配置 | Provider Hub 管理多套 OpenAI-compatible Profile | Provider Hub 管理多套 OpenAI-compatible Profile |
| 交付路线 | 按 MVP-0～MVP-9 渐进 | 同 v0.4 | **负责人先交付 v1.0 演示版；随后 MVP-1～MVP-5 模块维护** |

### 4.4 解耦边界

**分层与方向**

- Runtime 不判断“是否该给提示”，只执行图节点请求并返回结构化结果。
- Pedagogical Graph 不直接操作数据库、模型 SDK 或沙箱，只通过 Runtime Port 调用能力。
- Skill 不持有全局 Session；需要状态时通过 `RuntimeContext` 读写受控 Memory。
- Provider 不包含教学逻辑；模型更换不得改变图的业务语义。
- UI 不直连 Tool、Memory 或 Provider；所有动作由 Runtime 接入并产生事件。
- Desktop Renderer、CLI 与 Pet 不持有模型 API Key；密钥仅进入 SecretStore / Provider Registry。
- 桌宠挂件不是独立前端：它由 Desktop 托管（或独立浮窗运行），只消费事件投影、只发送 `pet.interact` 一类命令。
- 同一 Session 的事实源是 Runtime EventStore；不同 Surface 只做视图投影，不维护互相独立的“前端记忆”。
- 资源库只负责“材料从哪来、怎么归档、怎么定位”；教学决策仍在图中，资源库内容不得绕过绑定表直接进入教学流程（§20.7）。

**WHAT / HOW 的接缝（v0.4.1 修订，v0.6 不变）**

图回答“应该做什么”，Runtime 回答“怎么把它做出来”；两者之间只有两张契约，
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
| 前端不碰密钥与内部实现 | Renderer 无 Node 权限；CLI 不依赖 Pi Agent Core | 前端依赖扫描 + Secret 访问扫描（§9.1） |

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
| Provider | 屏蔽不同模型供应商、OpenAI-compatible Base URL 与流式协议差异 | `generate / stream / list_models / capabilities / healthcheck` |

Core 必须保持少依赖、可单元测试、与 LangGraph 解耦。教育能力通过 Port/Adapter 接入，而不是写入 Agent loop。

### 5.2 Plugin Runtime：参考 DeepSeek Harness

DeepSeek Harness 的关键思想是“Everything is a Plugin”：模型、工具、技能、会话、沙箱、存储和 UI 都可由插件组合。DeepProf 采用较小的受控子集：

- 插件具有唯一 `id`、版本、能力声明和依赖列表。
- 插件通过生命周期挂载：`install → start → stop → uninstall`。
- 插件只能通过显式 Service Registry 获取服务，不读取 Runtime 私有状态。
- Tool、Skill、Provider、Storage 与 Sandbox 都可以由插件提供。
- 插件事件写入 Session 轨迹，支持回放、审计和失败定位。
- 第三方插件默认最小权限；文件、网络、进程与密钥权限分开授权。
- 桌面端提供与 DSH Desktop 一致的**安全模式**入口：第三方插件导致启动或渲染异常时，可仅加载官方核心组件启动（§17.2）。

### 5.3 Skills、Tools、Storage 与 Sandbox

| 子系统 | 定义 | 示例 | 约束 |
| --- | --- | --- | --- |
| Skills | 面向模型的可复用能力说明与流程，由绑定表按名引用 | Socratic、Quiz、PaperReader | 负责“怎么完成一类任务”，不拥有底层权限；**图节点不直接调用它**（§4.4） |
| Tools | 可执行且有结构化参数的原子操作 | 检索教材、保存错题、读取画像、资源库导入 | 必须经过 schema 校验、权限检查和审计 |
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
library.imported / library.crawled / library.indexed
pedagogy.node.entered / pedagogy.decision / pedagogy.node.exited
pedagogy.attempt                  # 作答事实：教育组 → 数据组的交接（§18.2）
```

事件采用追加写入（append-only）并携带 `event_id`、`session_id`、`trace_id`、`timestamp`、`type`、`payload` 与 `source`。敏感字段在落盘前脱敏。

`pedagogy.decision` 是实验复盘的主要依据（§3.4 研究问题 4）：它记录节点、动作、
`reason`、依据（`evidence_sufficient` / `evidence_count` / `attempt_count` /
`wrong_streak` / `hint_level` / `turn_count` / `max_turns`）以及本轮**实际执行了哪个能力**
（`capability` / `capability_status`）。因此“策略判了什么”与“执行做成了什么”
在同一条轨迹里可分别核对。

隐私纪律：决策事件的载荷里**不得包含学生正文**——学生文本只出现在决策的
`params` 里（供能力层使用），事件只记长度与依据字段（§6.4、§13.2）。
`hint` / `test` 还额外声明 `answer_leaked`，用于核查“是否过早泄露答案”。

`pedagogy.attempt` 承载作答事实：字段与 `models/learner/attempt.py` 的 `Attempt` 契约一致，
只在“归属明确 + 判分可靠 + 题目可追踪”三者齐备时产出，否则在决策事件里写明跳过原因（§18.2、§13.1）。
这两条纪律都由测试锁住（`tests/graph/test_education_contract.py`、`test_education_attempt.py`），
而不是靠约定。

`library.*` 事件用于资源库审计：每个导入/爬取/入库动作记录来源、哈希与操作者，
支撑“资源可溯源、可撤回”（§20.5）。

### 5.5 Memory 边界

Memory 是 Runtime 服务，不等同于 Session 日志：

- **Session Log**：事实轨迹，用于恢复、审计与回放。
- **Working Memory**：当前任务与本周学习目标。
- **Long-term Learning Memory**：知识点掌握度、错误模式和学习偏好。
- **Episodic Memory**：关键学习事件与干预结果。
- **Affective Memory**：经用户授权保存的表达偏好与互动状态。

任何长期记忆写入都必须包含来源、置信度、过期策略和可撤回标记。

### 5.6 LLM Provider Hub：多 OpenAI-compatible API

v0.5 将“支持 DeepSeek / OpenAI / 本地 Qwen”升级为统一 **Provider Hub**；v0.6 在该机制上只改接入路径：首次启动时由**登录/接入向导**（§19.3）创建第一份 Profile，之后可在 Provider Settings 中维护多套 Profile。目标不是为每家模型厂商写一套业务代码，而是优先支持可配置的 OpenAI-compatible API，并为协议差异保留专用 Adapter。

**Provider Profile**（配置对象，不写入教学图）：

```text
profile_id
display_name
protocol = openai_compatible | native | local
base_url
api_key_ref                 # 只存 SecretStore 引用，不存明文
default_model
models[]                    # 手工配置或探测缓存
api_mode = chat_completions | responses | auto
extra_headers{}
timeout_ms
max_retries
capabilities{}              # stream / tools / json / vision / reasoning 等
enabled
```

**OpenAI-compatible Adapter** 负责：

1. 组合 `base_url` 与兼容端点；支持用户自定义代理、学校网关和第三方模型平台。
2. 将 DeepProf 的统一 `ModelRequest` 映射到兼容请求；流式增量统一转换为 `model.stream.delta`。
3. 不假定所有“OpenAI 格式”实现都完整支持 tools、JSON Schema、vision、reasoning 或 Responses API；由 `capabilities` 与启动探测决定是否启用。
4. 模型列表优先使用兼容的 models 接口；不支持时允许用户手工录入模型 ID。
5. Provider 失败按结构化错误返回，禁止静默换到另一家模型导致实验条件漂移；只有显式配置的 fallback chain 才允许切换。

**工作台中的 Provider Settings** 只编辑 Profile；真正的 API 调用仍发生在 Runtime。Renderer 通过受控 IPC 把 Secret 写入系统凭据库或后端 Secret Adapter，任何 Session/Event/日志都不得记录 API Key。

推荐路由方式：

```text
logical role        → provider profile → model
------------------------------------------------
tutor.default       → personal-deepseek → model-a
quiz.generator      → campus-gateway    → model-b
paper.reader        → openai-compatible → model-c
local.offline       → ollama-local      → qwen...
```

绑定表只引用“逻辑模型角色”或能力，不写具体厂商密钥；由组合根把角色映射注入 Provider Registry。这样更换模型供应商不改教学策略。

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

“这个动作该由什么能力做”写在 §6.2 的绑定表里，由组合根注入 Runtime；
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
| teach / correct | `generate_grounded` + `retrieve_evidence` | `require_evidence=True`：先取可定位证据；取不到则不调模型，改用绑定里的“证据不足”表述（§7.3） |
| ask | `invoke_skill`（socratic） | 取不到问题则退回策略模板；证据不足时由绑定追加“不含引用”说明 |
| hint | `render_template` | **确定性渲染，绝不调用模型**：提示强度是实验自变量，不能因换模型而漂移（§3.4） |
| test | `invoke_skill`（quiz） | 按 mode 取 Skill 结果的不同位置；按判分三态（对/错/判分缺失）选框架文案 |
| reflect / end | `render_template` | 换策略建议与收束语都是可评审的固定文案 |
| assess | `retrieve_evidence` | 只取“有没有可定位证据”这个信号，供状态与输出方式使用（不参与动作选择） |
| recall | `read_memory` | 读该学习者的长期学情记忆；身份取自调用上下文，检索口径来自绑定 |
| diagnose | `invoke_skill`（diagnosis） | 只问状态：模型未接入（not_implemented）也记为调用成功，据此退化为规则化观察（§18.1） |
| update_profile | `write_memory` | 记录由节点构造（来源/置信度/过期策略/可撤回），绑定只声明“写进去” |

绑定表可用的字段（解析逻辑见 `runtime/capabilities.py`）：

| 字段 | 作用 |
| --- | --- |
| `capability` | 要执行的通用原语：`render_template` / `invoke_skill` / `retrieve_evidence` / `generate_grounded` / `read_memory` / `write_memory` |
| `params` | 原语参数；字符串中的 `${字段}` 从决策取值（支持 `a.b` 路径，整串占位保留原类型） |
| `evidence` | `require_evidence=True` 时先执行的能力；取不到可定位证据就不执行主能力 |
| `prefix` / `suffix` | 围绕正文的段落（开头框架、结尾说明），未声明即不加 |
| `fallback` | 主能力失败时的确定性兜底 |
| `insufficient_text` | 证据不足时的确定性表述（不调用模型） |
| `memo` | 声明“同一轮内可复用结果”（仅只读且同轮内稳定的能力，目前只有证据检索）。默认不备忘——写学情、调模型这类有副作用的能力绝不能缓存 |

`prefix` / `suffix` / `fallback` / `insufficient_text` 是同一套**绑定模板块**，两种形态可混用：

```jsonc
{"template": "...", "values": {...}}                        // 固定模板
{"templates": {...}, "select": "${键}", "values": {...}}     // 按取值选模板
```

模板块里两种记法各管一段：`${字段}` 从决策取值，`{字段}` 由该块的 `values` 填充；
`select` 用 `${字段}` 指名“按哪个取值选模板”。声明了 `select` 却选不到模板、
或引用了请求里不存在的字段时**必须显式失败**（`template_not_found` / `invalid_request`）：
静默少一段会让学生看到半截回复，而绑定作者以为自己配好了。

**原语级参数**（v0.6.1 补回，`params` 里除占位符外的执行细节）：

| 参数 | 所属原语 | 作用 |
| --- | --- | --- |
| `skill` | `invoke_skill` / `retrieve_evidence` | 调哪个 Skill（名字只出现在绑定数据里） |
| `content_field` | `invoke_skill` | 取 Skill 结果的哪一段作正文（`a.b` 点路径，或按取值选字段的模板块）；**不声明即“只问状态”** |
| `passthrough` | `invoke_skill` | Skill 结果里要带回策略层的字段白名单（目前只支持**顶层键**，见 §10.4 与验收记录 §6 的 item_id 待补项） |
| `tool` / `arguments` | `retrieve_evidence` | 不经 Skill、直接调检索工具时的入口 |
| `system_prompt` / `prompt_template` / `values` / `max_chars` / `temperature` / `role` | `generate_grounded` | 提示词模板与生成参数；`{evidence_block}` 由 Runtime 用**证据原文**填入（原文用完即弃，回传策略层只有可定位引用，§6.4） |
| `query` / `records` | `read_memory` / `write_memory` | 记忆检索口径与待写入记录（记录由策略层构造，§5.5） |

### 6.3 教育能力节点

- **Socratic**：生成递进问题，避免过早泄露答案。
- **RAG**：检索教材与论文，返回可追踪来源（检索源＝工作台资源库，§20.5）。
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

大型文档、模型原始输出和完整历史不直接塞入图状态，只保存 Storage 引用，避免状态膨胀和隐私复制。资源库中的教材原文同样不进图状态，只保留可定位引用（`document_id / chunk_id / page / source`）。

**v0.4.1 补充**：状态里的证据一律是**可定位引用**（`document_id / chunk_id / page / source`），
不含教材原文——原文由能力层当次取用、用完即弃。这条边界由 Runtime 的出站归一化保证：
能力内部可以带原文（生成需要），但跨回策略层的结果只保留引用。
学情记忆同理：读回来的记录只在当次解释（取 `record_id` 与误解标签），不写进状态正文。

**同一轮内证据只检索一次**：Assess 的探路与 Teach / Correct 的取原文参数完全相同，
应得同一结果，因此绑定声明 `memo`，由 Runtime 按 `(trace_id, 能力, 参数)` 在单轮内复用。
好处有两层：不多花一次向量查询；更重要的是**不会出现“Assess 判证据充分、
Teach 却说证据不足”的自相矛盾**。跨轮不复用——`trace_id` 每轮不同，天然隔离；
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
不是“调用某个教学能力”，因此不包装成决策。`RuntimeService` 一套方法同时实现两个面，
所以对图只暴露 `RuntimePort` 的类型承诺，对能力实现才移交 `RuntimeHost`。

**决策契约**（`graph/education/contracts.py`，图侧，Runtime 不认识它）

| 字段 | 归属 | 说明 |
| --- | --- | --- |
| `action` | 图 | 教学动作或信息请求名；决定绑定到哪个 capability |
| `concept` / `level` | 图 | 策略算出的产物（如 `next_hint_level` 的结果） |
| `reveal_answer` | 决策 | 是否允许给出最终结论（提示与追问恒为 False） |
| `require_evidence` | 分发器前置条件 | 取不到可定位证据则不执行主能力、不调模型 |
| `require_student_reply` | 图 | 循环控制，Runtime 不参与 |
| `evidence_sufficient` | 图 | 本轮评估结论；据此决定是否发起检索、是否追加“不含引用”说明 |
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
  → 交互层标准化输入（工作台 / CLI / 桌宠挂件）
  → Runtime 创建或恢复 Session
  → Pedagogical Graph 进入 Assess
      · 决策 recall  → read_memory 能力（读长期学情记忆）
      · 决策 assess  → retrieve_evidence（取可定位教材证据）
      · decide_action 判定本轮动作
  → Graph 在 Teach / Ask / Hint / Correct / Test / Reflect 之间选择
      每个节点只产出决策；Runtime 用绑定表决定调 Skill / 模板 / 模型
  → Runtime 流式输出文本与情感标签
  → 交互层驱动工作台展示与桌宠挂件表达
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
失败的学生可见表现因此是“空回复”，这是有意的取舍：**装配错误是部署缺陷，
应当响亮地暴露，而不是用一句安慰话遮住**。

---

### 7.4 Unified Client 契约（v0.6）

Desktop、CLI 与 Pet 挂件统一发送 `ClientCommand`，统一消费 `RuntimeEvent`。前端不直接把 HTTP 返回值当作最终事实，而是用事件流投影当前视图状态。

```text
ClientCommand
├── command_id
├── client_id
├── surface              # desktop | cli | pet
├── session_id
├── learner_id
├── type
├── payload
└── timestamp
```

首轮命令类型：`message.send`、`turn.cancel`、`session.new`、`session.resume`、`session.fork`、`session.compact`、`tool.approve`、`tool.reject`、`quiz.answer`、`voice.transcript`、`pet.interact`、`library.import`、`library.crawl`。

事件继续使用 v0.4.1 的 `event_id / session_id / trace_id / sequence / type / payload`，增加可选 `client_id / surface / audience`。WebSocket 重连按 `sequence` 补发，避免多个 Surface 重复渲染或重复记分。

形态说明（v0.6）：

- **Desktop / CLI** 是完整会话入口，可 new/resume/fork/compact 同一 Session。
- **Pet 挂件**只做轻交互：消费事件投影（§19.8），发送 `pet.interact` / `voice.transcript`；不提供完整会话管理界面，也不持有独立的“前端记忆”。
- CLI 的 Pi 风格交互只复用 UX / TUI 思路；正式学生链路仍发送 `ClientCommand` 到 DeepProf，而不是让 Pi Agent Core 再建立第二套 Agent/Session/Memory。真实 Pi RPC 仅作为 `integrations/pi/` 的开发者模式。

## 八、技术栈选型

| 层级 | 技术选型 | 说明 |
| --- | --- | --- |
| Runtime | Python 3.12+，自研异步 Core | MVP 优先实现，不把 LangGraph / Pi / DSH 当 Core |
| API / Gateway | FastAPI + WebSocket / SSE | Session 接入、事件流、Provider/Settings 管理 |
| Pedagogical Graph | LangGraph | 只承载教育策略与状态图 |
| LLM Provider Hub | 自研 Provider Registry + OpenAI-compatible Adapter | 多套 Base URL / API Key / Model Profile；能力探测与显式 fallback；首次启动由接入向导写入（§19.3） |
| 本地模型 | Ollama / 本地 OpenAI-compatible Server Adapter | 作为 Provider Profile 接入，不改变上层调用 |
| 登录与凭据 | API Key + 系统凭据库（Windows DPAPI/凭据管理器、macOS Keychain、Linux Secret Service） | **不做账号系统**；登录＝接入 Provider；密钥不落 Renderer、不落日志、不进仓库 |
| Desktop 工作台 | Electron + React + TypeScript，按 DSH Desktop 真实配置落地 | Electron Main 管窗口/托盘/更新/安全模式；独立隔离进程承载 Runtime；工作台 UI 仅暴露在 127.0.0.1 动态端口；Renderer 无 Node 权限（contextIsolation、sandbox、webSecurity）；preload 仅最小 IPC 白名单；userData 与源码分离 |
| CLI/TUI | TypeScript；Pi 风格 UX，优先评估 `@earendil-works/pi-tui` | 覆盖交互式/非交互/JSON/RPC 四类形态的体验参考；共享 Client SDK，不建立第二套学生 Agent Runtime |
| 桌宠挂件 | Codex 兼容宠物包 + 透明置顶浮窗 + Pet Director | 包格式：`pet.json` + `spritesheet.webp`；9 个标准动作行，可选 v2 的 16 向朝向；由事件驱动状态（§19.8） |
| 资源库 | Python：httpx + selectolax/parsel（抓取）、PyMuPDF/EPUB 解析（导入）、SHA-256（去重）、SQLite + 向量库（索引） | 爬虫限白名单域与 robots/ToS 合规；入库产出可定位引用（§20） |
| Client SDK | TypeScript | SessionClient / EventClient / CommandBus / ApprovalClient |
| 语音 | Whisper-compatible ASR + TTS Adapter | 通过 Provider/Adapter 接入；桌宠挂件使用，工作台可选 |
| 辅助语言 | Rust（可选） | 仅在性能测量证明有必要时使用 |

---

## 九、模块划分与目录树

```text
DeepProf/
├── apps/
│   ├── desktop/                     # 主工作台（DSH Desktop 真实配置式 Electron 外壳）
│   │   ├── main/
│   │   │   ├── app.ts               # 单实例锁、启动序列、userData 初始化
│   │   │   ├── runtime_supervisor.ts# 启动/健康检查/重启 DeepProf Runtime 进程
│   │   │   ├── backend_broker.ts    # 工作台入口：仅 127.0.0.1 动态端口
│   │   │   ├── ipc_guard.ts         # 校验发送窗口与主帧后才执行特权动作
│   │   │   ├── windows.ts
│   │   │   ├── tray.ts
│   │   │   ├── safe_mode.ts         # 官方核心组件启动，屏蔽第三方插件
│   │   │   ├── secrets.ts           # 系统凭据库封装（api_key_ref 落地处）
│   │   │   └── updater.ts
│   │   ├── preload/
│   │   │   └── index.ts             # 最小 IPC 白名单
│   │   └── renderer/
│   │       ├── onboarding/          # Codex 式首次启动与 API Key 接入（§19.3）
│   │       ├── workspace/           # 主学习工作区
│   │       ├── chat/
│   │       ├── courses/
│   │       ├── library/             # 资源库界面（§20.6）
│   │       ├── sources/             # RAG 引用定位
│   │       ├── session_tree/
│   │       ├── provider_settings/   # Profile 管理，不直接发模型请求
│   │       ├── traces/
│   │       └── settings/
│   ├── cli/                         # Pi 风格 DeepProf CLI/TUI（第二主入口）
│   │   ├── onboarding.ts            # 终端版 API Key 接入全流程
│   │   ├── commands/
│   │   ├── components/
│   │   └── session_tree/
│   └── pet/                         # Codex 兼容桌宠挂件
│       ├── package/                 # pet.json + spritesheet.webp（兼容格式与校验）
│       ├── overlay/                 # 透明置顶浮窗、点击穿透与拖动
│       ├── director/                # 事件 → 动作行状态映射
│       └── voice/
├── packages/
│   ├── client_sdk/                  # 三端共享客户端
│   │   ├── session_client.ts
│   │   ├── event_client.ts
│   │   ├── command_bus.ts
│   │   ├── approval_client.ts
│   │   └── event_projector.ts
│   ├── contracts/                   # ClientCommand / RuntimeEvent 等共享 schema
│   └── design_system/
├── runtime/                         # 自研 DeepProf Runtime
│   ├── core/ (agent.py, ports.py, session.py, events.py, message.py)
│   ├── capabilities.py              # 六个通用原语与 ActionDispatcher（D-7 的 HOW 侧）
│   ├── skills.py                    # SkillRegistry 与 Skill 协议（uses_host 声明宽面需求）
│   ├── assembly.py                  # 装配辅助（组合根在 api/app.py）
│   ├── testing.py                   # 内存装配、RecordingHost、FakeRuntime（测试替身）
│   ├── tools/
│   ├── providers/ (registry.py, profiles.py, openai_compatible.py, local.py, capabilities.py, secrets.py)
│   ├── memory/
│   ├── storage/
│   ├── sandbox/
│   └── plugins/
├── graph/
│   └── education/
│       ├── state.py / router.py / contracts.py / bindings.py / builder.py
│       ├── nodes/                   # assess / teach / ask / hint / correct / test / reflect / update_profile
│       └── policies/                # 阈值与全部可评审文案（教师评审入口，§16.8）
├── skills/
│   ├── socratic/ / quiz/ / paper_reader/ / rag/ / diagnosis/
├── models/
│   └── learner/                     # Attempt / LearnerEstimate 契约与 LearnerModel 接口（§18.1）
├── library/                         # 资源库服务（v0.6 新增）
│   ├── crawler/                     # 白名单抓取、robots/ToS 检查、来源记录
│   ├── importers/                   # pdf / epub / docx / pptx / md 导入
│   ├── classify/                    # 课程、类型、标签分类
│   ├── index/                       # SHA-256 去重、切块、向量入库
│   └── store/
├── tools/
│   └── retrieval/                   # 教材检索工具（索引未接入时诚实返回“无证据”）
├── integrations/
│   └── pi/                          # 真实 Pi RPC/SDK，仅开发者模式
│       ├── rpc_client.ts
│       ├── process_manager.ts
│       └── event_adapter.ts
├── api/ (app.py, sessions.py, events.py, providers.py, health.py)
├── config/
├── tests/
│   ├── runtime/ / graph/ / client/ / desktop/ / cli/ / pet/ / providers/ / library/ / integration/
│   └── test_architecture_boundaries.py
└── docs/
    ├── DESIGNv0.6.md
    └── deepprof_framework_v0.6.html
```

### 9.1 依赖规则

```text
Desktop ─┐
CLI ─────┼──→ Client SDK → Gateway/API → Pedagogical Graph → RuntimePort → ActionDispatcher
Pet ─────┘                                                             ↓
                                                                  RuntimeHost
                                                         Skill / Tool / Provider / Memory
                                                                    ↑
                                                    Library（经工具/能力绑定进入）

Provider Settings（Renderer 只改 Profile）→ Main IPC → Provider Registry → Secret Adapter / OpenAI-compatible Adapter
Plugins ───────────────────────────────────────────────→ Service Registry
```

禁止反向依赖：

- `runtime/` 不得导入 `graph/education`、`apps/*`、具体 UI 或任何具体数据库 SDK。
- `apps/*` 不得导入 Runtime 内部实现、Provider SDK、MemoryStore 或 Tool 实现。
- `graph/*` 仍只能依赖 `RuntimePort.execute / emit`；不得直接调用 `library/`。
- `apps/desktop/renderer` 不得读取模型 Secret；Secret 只存在 Main/系统凭据库。
- `apps/desktop/main` 不得把 Runtime 工作台暴露到非环回地址，也不得向 Renderer 授予 Node 能力。
- 正式学生版 `apps/cli` 不得直接依赖 Pi Agent Core；Pi RPC/SDK 仅在 `integrations/pi/`。
- `library/` 不得写入 `graph/` 或 `runtime/` 内部状态；其产物只以可定位引用形式被检索能力消费。

**宽面只交给能力实现（v0.6.1 明确实现口径）**：`skills/` 里的 Skill 若是纯函数式，
只拿到 `(input, ctx)`；需要用模型 / 工具 / 记忆时，由 Skill 自己声明
`uses_host = True`，Runtime 才会把 `RuntimeHost` 宽面移交过去（`SkillRegistry.invoke(..., host=...)`）。
声明了却没人给 host 时**显式失败**（`capability_missing`），不静默降级。
图节点在任何情况下都拿不到宽面——这条由守卫 4 机械检查。

**架构守卫清单（v0.6 累积）**：v0.4.1 三条（Runtime 词汇扫描、图宽面 AST 扫描、端口面断言）+ v0.5 三条（前端依赖扫描、Renderer Secret 访问扫描、Product CLI→Pi Core 依赖禁止、Provider Profile 明文密钥扫描）+ v0.6 两条（工作台仅环回地址检查、爬虫来源字段与白名单检查）+ v0.6.1 一条（**图节点瘦身扫描**：节点字符串常量不得含能力名与提示词占位符，自带检测器自检与 docstring 豁免自检）。

---
## 十、交付路线：v1.0 演示版 + MVP-1～MVP-5

### 10.1 交付原则

1. **负责人先交付 v1.0 演示版**：一个可运行、可演示、端到端的最小闭环（单机运行、单门课程、演示数据）。
2. v1.0 之后，所有开发统一收敛为 **MVP-1～MVP-5**；每个 MVP 指定主责人与维护范围，其他成员负责维护、数据、测试与补强，不再各自铺开新线。
3. 演示数据与真实数据严格区分：任何“完成度”表述都必须指向实际运行证据（§16.9、§18.3）。

### 10.2 v1.0 演示版（负责人先做）

| 范围 | 内容 | 验收标准 |
| --- | --- | --- |
| Runtime | Core + Session/Event + Provider Hub | 一条带 trace_id 的请求完成 图→Runtime→Provider→流式事件闭环 |
| 教学图 | Assess → Ask/Teach → Test → UpdateProfile 主链路 | 六类教学动作有样例；D-7 守卫全绿 |
| Provider | ≥2 套不同 Base URL 的 OpenAI-compatible Profile | 切换 Profile 不改 Graph / Skill 代码 |
| Desktop 工作台 | 最小可用：首次接入向导、Chat、Sessions、Provider Settings、Trace | 首次启动可完成 API Key 接入；重启可恢复 |
| CLI | 最小全流程：login / new / ask / resume / tree / compact | 与工作台共享同一 session_id 继续对话 |
| 桌宠挂件 | 首个 DeepProf 宠物包 + 透明浮窗 | 可随事件呈现 idle / running / waiting / failed / review 等状态 |
| 资源库 | 内置 1 门课程教材 + 上传入口 + 引用定位 | 检索可定位到 page / chunk；无证据显式返回 |

> 负责人只在 v1.0 阶段保持“一人可跑通全链路”的最小实现；接口与 schema 必须按 §7 的契约写好，
> 为后续成员接管模块留下干净的接缝。

### 10.3 MVP-1～MVP-5 模块迭代与维护

| 编号 | 名称 | 主责 | 维护范围 | 验收标准 |
| --- | --- | --- | --- | --- |
| MVP-1 | Runtime、契约与 Provider Hub | 刘俊鹏 | `runtime/`、`packages/contracts/`、`api/`、`integrations/pi/` | 契约测试全绿；Provider 能力探测与显式 fallback 可复现 |
| MVP-2 | 教学策略图与教育能力 | 孙一新（协同 许阳毅） | `graph/`、`skills/`、`models/learner/` | 分支测试覆盖六类动作；学情增量可复现、可审计 |
| MVP-3 | 桌面工作台与桌宠挂件 | 张钧翔 | `apps/desktop/`、`apps/pet/`、`packages/design_system/` | 工作台可启动且仅环回地址；Renderer 无密钥；挂件状态与事件一致 |
| MVP-4 | 资源库与教材 RAG 管线 | 许阳毅（协同 谢浪） | `library/`、`skills/rag/`、`runtime/storage/` | 导入/上传/爬虫来源可溯源；去重与引用定位通过样例 |
| MVP-5 | CLI 全流程与联调、评测 | 张钧翔 ＋ 刘俊鹏（协同 欧阳文凯） | `apps/cli/`、`tests/integration/`、`evaluation/` | CLI 全流程与工作台互相恢复同一 Session；联调记录可复现 |

> v0.5 的 MVP-0～MVP-9 编号自 v0.6 起**废止**，其内容已并入 v1.0 演示版与 MVP-1～MVP-5。

### 10.4 交付进度（截至 2026-09-22，按实际运行证据记）

只记录**已实际跑通并有验收证据**的部分；未列出的模块即尚未交付。

| 模块 | 状态 | 证据 |
| --- | --- | --- |
| MVP-2 `graph/` · `skills/` · `models/learner` 契约 | **已交付（验收记录已出）** | [MVP-2_验收记录.md](MVP-2_验收记录.md)；六类教学动作分支测试各至少一条（交付时点全量 `264 passed`） |
| MVP-2 剩余：BKT / IRT 估计与知识追踪 | 未交付 | `models/learner` 只出 `LearnerModel` 接口与 DTO；`diagnose` 仍返回 `not_implemented`，图侧退化为规则化观察（§18.1） |
| MVP-1 `runtime/` · `packages/contracts/` · `api/` · `integrations/pi/` | **已交付（验收记录已出）** | [MVP-1_验收记录.md](MVP-1_验收记录.md)；当前全量 `269 passed, 0 skipped`；契约 9 组一致性全绿；跨 Provider Profile 切换集成测试 |
| MVP-3 / MVP-4 | 已交付（各有验收记录） | `docs/MVP-3_验收记录.md`、`docs/MVP-4_验收记录.md` |
| MVP-5 | 工程实现完成，验收记录已出 | `docs/MVP-5_验收记录.md`；真实模型与完整跨进程桌面手测仍需按环境复测 |

MVP-1 另有 **8 项未完成/待补**（清单见验收记录 §7），其中三项直接影响后续模块：
① 显式 fallback 未接入流式主链路（`RuntimeService.generate` 仍只 `resolve`）；
② 插件库已就绪但未装配进组合根（`service.plugins is None`）；
③ 能力缺失未做请求前校验（`missing_capabilities` 无调用方）。
**v1.0 演示闭环（跨工作台/CLI 迁移）在 `apps/` 交付前无法验证**，因此 §10.2 的演示版仍待补。

**本轮为何要改 Runtime（MVP-1 范围）**：v0.6 的 Runtime 精简版丢了 `invoke_skill.content_field/passthrough`、
`generate_grounded` 的提示词模板与证据原文拼装、`retrieve_evidence` 的 Skill 入口。
没有它们，绑定表里“提示词怎么写、取 Skill 结果的哪一段、教材原文怎么进模型”就无处安放，
D-7 的“节点不含提示词”与“有依据的讲解”会同时失效。因此按最小侵入补回（接口不变、纯增量），
并由 `tests/runtime/test_capability_dispatch.py` 的用例锁住行为。

---

## 十一、参考项目、借鉴边界与协议合规

### 11.1 核心架构参考

| 项目 | 官方网址 | DeepProf 借鉴点 | 明确不照搬的部分 |
| --- | --- | --- | --- |
| Pi Agent Harness | [GitHub](https://github.com/earendil-works/pi) · [文档](https://pi.dev/docs/latest) | 极简 Agent Core；四种运行形态（交互式 / print-JSON / RPC / SDK）；Session 树与 fork/compact 体验；TUI 差分渲染（pi-tui）；自定义 Provider（`models.json`）思路 | 正式学生链路不使用 Pi Agent Core、Pi SessionStore 或默认宿主权限；真实 Pi 仅作开发者 Adapter，且 Pi 本身不带权限系统（§11.4） |
| DeepSeek Harness / DSH Desktop | [Harness](https://github.com/deepseek-ai/deepseek-harness) · [DSH Desktop](https://github.com/dataelement/dsh-desktop) · [架构文档](https://github.com/dataelement/dsh-desktop/blob/main/docs/architecture.md) | Plugin Runtime 思想；Electron 外壳 + 隔离运行进程；Web 工作台仅环回地址；contextIsolation/sandbox/无 Node 权限的 Renderer；IPC 发送方校验；userData 分离；安全模式；更新与恢复流程 | 不绑定 Cordis / DSH Session / Developer Preview 内部 API；不引入其 PPT 模式、手机配对桥与 preset 包格式；不把 DSH Host 作为 DeepProf Runtime |
| LangGraph | [GitHub](https://github.com/langchain-ai/langgraph) · [文档](https://docs.langchain.com/oss/python/langgraph/overview) | 有状态 Education Workflow、分支、回退、恢复 | 不作为 DeepProf 的通用 Agent Core |

### 11.2 其他借鉴项目

| 项目 | 网址 | 主要借鉴点 |
| --- | --- | --- |
| OpenAI Codex | [github.com/openai/codex](https://github.com/openai/codex) | 工具调用、审批、沙箱与流式交互 |
| Codex 宠物生态 | [awesome-codex-pet](https://github.com/legeling/awesome-codex-pet) · Codex Settings → Pets | 宠物包格式（`pet.json` + `spritesheet.webp`）与 9 个标准动作行约定；只借鉴格式与状态语义，不搬运美术资源 |
| dsh-edu | [github.com/1Vewton/dsh-edu](https://github.com/1Vewton/dsh-edu) | 教育版 Harness、先教学再测验 |
| OpenMAIC | [github.com/THU-MAIC/OpenMAIC](https://github.com/THU-MAIC/OpenMAIC) | 多智能体课堂、教学内容与 Skill |
| Socratic Education System | [github.com/HowieWang1121/Socratic-Education-System](https://github.com/HowieWang1121/Socratic-Education-System) | 苏格拉底提问与学情诊断 |
| AI Tutor Release | [github.com/Zenglian990/AI_Tutor_Release](https://github.com/Zenglian990/AI_Tutor_Release) | 教材 RAG、错题本与引用 |
| EduAgent Studio | [github.com/ZZhouWJ/EduAgent-Studio](https://github.com/ZZhouWJ/EduAgent-Studio) | LangGraph 教育 Agent 工程化 |
| mea-pet-public | [github.com/suan-11/mea-pet-public](https://github.com/suan-11/mea-pet-public) | 桌宠、TTS、记忆与好感度参考 |
| N.E.K.O | [github.com/Project-N-E-K-O/N.E.K.O](https://github.com/Project-N-E-K-O/N.E.K.O) | 主动交互、情感引擎与具身桌宠 |
| AgentPet | [github.com/cqzaaa/AgentPet](https://github.com/cqzaaa/AgentPet) | Electron + Live2D + 记忆 + MCP |
| PetGPT | [github.com/JulesLiu390/PetGPT](https://github.com/JulesLiu390/PetGPT) | 跨平台桌宠、多模型与本地记忆 |

### 11.3 合规要求

1. 任何代码引入前都要记录：仓库、commit/tag、License、修改文件、用途、核对日期。
2. 只借鉴概念时，在设计记录中写明来源；复制代码时保留版权和许可证要求。
3. GPL/AGPL 或未知协议项目不得直接进入核心代码，必须先完成法律与发布方式评估。
4. Pi 与 DeepSeek Harness 的现行接口可能继续变化（DSH 目前为 developer preview，官方明示会有破坏性变更），DeepProf 应绑定自有接口而不是绑定其内部 API。
5. `OpenAI-compatible` 表示请求/响应形态兼容，不等价于功能完全一致；tools、JSON Schema、vision、reasoning、Responses API 等必须按 Profile 能力测试，不做未经验证的兼容承诺。
6. Codex 宠物美术资源为 CC BY-NC 4.0：DeepProf 只采用格式与动作行约定，第三方美术资源不得用于商业用途；自制素材需自行确认授权。
7. 资源库爬虫只抓取公开或已授权内容，遵守 robots 与站点条款，不绕过付费墙与访问控制（§20.4、§13.5）。

### 11.4 版本核对记录（2026-09-21）

以下事实在编写 v0.6 时从官方/现行来源核对，用于支撑 §8、§17、§19 的具体表述：

- **DSH Desktop**（dataelement/dsh-desktop，MIT，官网 dshdesktop.com；README 与 `docs/architecture.md` 核对）：Electron 外壳托管 Harness；macOS 用 Electron UtilityProcess、Windows 用随附原生 Node 可执行文件承载隔离运行进程；Harness Web UI 仅监听随机 `127.0.0.1` 端口；主窗口 `contextIsolation: true`、`nodeIntegration: false`、Renderer sandbox 与 webSecurity，webview/导航/新窗口受限、权限白名单，外链走系统浏览器；IPC 执行特权动作前校验发送窗口与主帧；数据在 Electron userData 下分离（`launch-root/`、`harness/`（含 profiles/sessions/settings.yaml）、`bin/`、`update-skip.json`），升级不覆盖用户数据；安全模式（`--safe-mode`）为非破坏性恢复；更新用 electron-updater，启动后、每 6 小时与长休眠恢复后检查，下载与安装均需用户确认；手机配对桥走 LAN + 短时令牌 + Cloudflare Quick Tunnel（Pinggy 兜底）；当前平台为 macOS 与 Windows x64。
- **DeepSeek Harness**（deepseek-ai/deepseek-harness，MIT，developer preview；README 核对）：everything-is-a-plugin（基于 Cordis）；`npx @deepseek-ai/dsh web` 默认在本机 `127.0.0.1:3080` 启动 Web UI；仓库 `apps/` 内含 cli、desktop、desktop-host、web。
- **Pi**（earendil-works/pi，MIT；根 README 与 `packages/coding-agent/README.md` 核对）：四种运行形态——交互式、print/JSON、RPC（进程集成）、SDK（嵌入）；RPC 为 `pi --mode rpc`，使用严格 LF 分隔的 JSONL；Session 以 JSONL 树存储（`id`/`parentId`），支持 `/tree`、`/fork`、`/clone`、`/compact`；自定义 Provider 写在 `~/.pi/agent/models.json`（支持 OpenAI 等兼容接口）；`pi-tui` 为差分渲染终端 UI 库；Pi 不内置权限系统，官方建议容器化运行。
- **Codex 宠物**（Codex Settings → Pets；格式由社区目录 awesome-codex-pet 与 hatch-pet-v2 规范核对，代码 MIT / 素材 CC BY-NC 4.0）：宠物包＝`pet.json`（`id`、`displayName`、`description`、`spriteVersionNumber`、`spritesheetPath`）+ `spritesheet.webp`，安装在 `~/.codex/pets/<pet-id>/`（可用 `CODEX_HOME` 覆盖）；v1 图集 1536×1872（8 列 × 9 行），v2 图集 1536×2288（8 列 × 11 行，含 16 个顺时针朝向）；9 个标准动作行依次为 idle、running-right、running-left、waving、jumping、failed、waiting、running、review，其中 waiting 表示等待审批/输入，running 表示执行中，review 表示专注查看。
- 结论：上述项目均在快速迭代；DeepProf 一律绑定自有接口与自有状态机，外部项目只作参考实现（合规第 4 条）。

---

## 十二、开发原则与质量要求

| 原则 | 要求 |
| --- | --- |
| 极简 Core | Core 只保留 Agent 生命周期必需能力，新增功能优先放到服务或插件 |
| 接口先行 | Runtime Port、事件 schema、Plugin manifest 先于具体实现 |
| 教育可验证 | 每个图节点有输入、输出、进入条件、退出条件和测试样例 |
| 轨迹可审计 | 模型、工具、记忆与策略决策均可关联到同一 trace |
| 本地优先 | 学情与教材默认本地保存，上传遵循最小必要原则 |
| 两模块一挂件 | 前端只做两个主模块（工作台、CLI）与一个桌宠挂件；不再新增并列前端 |
| 登录从简 | 不做账号系统；接入＝API Key；密钥只进系统凭据库 |
| 资源可溯源 | 资源库每条记录必须有来源、许可与哈希；可定位、可撤回 |
| 小步快跑 | 先交付 v1.0 演示版，再按 MVP-1～MVP-5 收敛迭代 |
| 不硬编码密钥 | 密钥只由 Provider/Secret Adapter / 系统凭据库读取，不进入 Renderer、Session、日志和仓库 |
| 多 Provider 可替换 | Graph / Skill 依赖逻辑模型角色，不依赖具体厂商 Base URL；更换 Profile 不修改教学策略 |
| 同一事实源 | 工作台、CLI 与挂件共享 Runtime Session/Event，不维护独立学习记忆 |
| 不编造引用 | RAG 证据不足时明确说明，不生成不可验证来源 |
| 策略与执行分离 | 节点只产出声明式决策；执行细节写在绑定表数据里，边界由守卫测试兜住（D-7） |
| 失败要响亮 | 装配与绑定错误显式返回结构化状态，不用兜底文案掩盖漏装配 |

最低测试范围：Core 单元测试、Graph 路由测试、Provider 契约与 OpenAI-compatible 兼容测试、Tool 权限测试、Session 恢复测试、Memory 写入审计测试、Client SDK sequence 重连测试、Electron IPC/Secret 隔离测试、工作台仅环回地址测试、宠物包校验测试、资源库导入/去重/来源字段测试、端到端教学闭环测试、**架构边界守卫测试（Runtime—Graph + Frontend—Runtime + Secret 边界 + 资源库边界）**。

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

### 13.4 Provider、密钥与外部 API 安全（v0.5 起延续）

- API Key 不进入 React Renderer、CLI 历史、Session Event、Graph State 或错误堆栈；配置对象只保存 `api_key_ref`。
- Desktop 使用系统凭据库或 Main Process Secret Adapter；Campus 模式由服务端密钥管理组件持有。
- 用户自定义 Base URL 属于外部网络边界：默认仅允许 HTTPS（localhost/明确开发模式例外），并记录目标 Provider Profile，但日志不记录 Authorization。
- Provider 健康检查不得把用户对话内容发出；只使用固定最小探测请求。
- 多 Provider fallback 必须显式开启，并在事件中记录 `provider_profile/model/degraded_from`，防止研究实验在不知情情况下更换模型。
- 工作台服务只监听环回地址；任何对外暴露都视为破坏性变更，必须走评审（§9.1 守卫）。

### 13.5 资源库与爬虫合规（v0.6 新增）

- 只采集公开或已授权的内容；遵守目标站点 robots 与使用条款，不绕过付费墙、登录墙或 DRM。
- 抓取限白名单域、限速、保留来源 URL 与采集时间；抓取失败不重试到违规程度。
- 学生上传资源默认仅本地可见、可删除；分享与他人可见必须显式开启。
- 教师与课程教材以“可用、可教学、可评审”为先，避免使用来源不明的盗版材料。
- 资源库记录许可与用途字段；无法确认来源的资源不得进入课程教材集。

---

## 十四、当前项目配置快照

| 配置项 | 值 |
| --- | --- |
| 项目名 | DeepProf |
| 项目类型 | 大学生创新创业训练计划 |
| 技术亮点 | 分层式 Agent Runtime 与教学策略图协同架构 + 两模块一挂件前端 + 资源库 |
| Runtime | Python 自研；Core 参考 Pi；Plugin 思想参考 DeepSeek Harness |
| 教学编排 | LangGraph Pedagogical Graph |
| 主前端 | DeepProf Desktop（DSH Desktop 真实配置式 Electron 工作台） |
| 第二前端 | DeepProf CLI（Pi 风格 TUI，完整全流程） |
| 桌宠 | Codex 兼容宠物包（`pet.json + spritesheet.webp`）桌面挂件 |
| 登录 | 仅 API Key（OpenAI-compatible）；不做账号系统；密钥进系统凭据库 |
| Client | 共享 Client SDK / ClientCommand / RuntimeEvent |
| 模型接入 | Provider Hub；多套 OpenAI-compatible API Profile + 本地模型 Adapter |
| 资源库 | 内置：真实教材选取、学生上传、分类归档、爬虫/导入、RAG 引用对接 |
| 数据策略 | 本地优先；Session 与学习 Memory 分离；共享同一 EventStore |
| 桌宠形象 | DeepProf 娘（二次元老师形象，自制或授权素材） |
| 当前阶段 | v0.6.1 架构冻结 / MVP-1～MVP-5 均已有工程实现与验收记录（§10.4）；BKT·IRT 学情模型仍待交付 |
| 团队技术栈 | Python + TypeScript/React/Electron + FastAPI；Rust 可选 |
| 文档版本 | v0.6.1（2026-09-22） |

---

## 十五、已确认决策

用户于 2026-09-17 确认 D-1—D-6；D-7 于 2026-09-19 确认（v0.4.1 接口修订）；D-8—D-12 为 v0.5 新增；D-13—D-17 为 v0.6 新增/修订。

| 编号 | 决策项 | 结论 | 实施说明 | 状态 |
| --- | --- | --- | --- | --- |
| D-1 | 桌宠前端 | Electron + React | 桌面端使用 Electron + React。**v0.6 修订**：桌宠不再作为独立前端，改为工作台挂件承载（见 D-13、D-16）。 | 已确认（v0.6 修订） |
| D-2 | 试点课程 | 高质量教材与题库优先 | 先选择资料充足、来源可用且教师能评审的课程；具体课程名由教育组与教师落实。 | 已确认 |
| D-3 | 事件与数据 | 抽象接口 + SQLite | 采用 EventStore / MemoryStore / ProfileStore 抽象；MVP 使用本机 SQLite。 | 已确认 |
| D-4 | 插件范围 | 仅 Python 插件 | MVP 只加载受信、审核后的 Python 插件；manifest 管理能力与生命周期。 | 已确认 |
| D-5 | 隔离方案 | 目录白名单 + 审批 | 演示版使用目录白名单与审批；启用任意代码执行前升级进程/容器隔离。 | 已确认 |
| D-6 | 学情建模 | BKT / IRT / 知识追踪 | BKT 跟踪掌握度，IRT 标定题目与能力，统一接口支持后续知识追踪模型比较。 | 已确认 |
| D-7 | 策略与执行的接缝 | 图只产出声明式决策 | 节点只产出 `PedagogicalDecision`；Runtime 按注入的绑定表执行；`RuntimePort` 收窄为 `execute + emit`，能力面拆为 `RuntimeHost`；边界由守卫测试兜住。 | 已确认 |
| D-8 | 统一前端 | Desktop + CLI + Pet，共享 Client SDK | DSH Desktop 式工作台为主入口；三端共享 Session/Event。**v0.6 修订见 D-13。** | v0.5 新增（v0.6 修订） |
| D-9 | Pi 集成边界 | Pi 进入 TUI/UX 与开发者 Adapter，不替代 Runtime | 正式学生 CLI 调 DeepProf Client SDK；真实 Pi RPC/SDK 放 `integrations/pi/`，与 Learner Session 隔离。 | v0.5 新增 |
| D-10 | 多模型接入 | Provider Hub + OpenAI-compatible Profiles | 支持多 Base URL/API Key/Model/Headers；能力探测；Graph 只依赖逻辑模型角色。 | v0.5 新增 |
| D-11 | 桌面运行方式 | Electron 薄壳 + DeepProf Runtime Supervisor | 借鉴 DSH Desktop 的 Shell 与运行进程分工。**v0.6 升级**：按 DSH Desktop 真实配置落地（§17.2）。 | v0.5 新增（v0.6 升级） |
| D-12 | 部署形态 | Local + Campus 双模式 | Local 可启动本机 Runtime/SQLite/本地模型；Campus 连接学校 HTTPS/WSS Runtime；协议一致。 | v0.5 新增 |
| D-13 | 前端收敛 | 两大模块 + 一个挂件 | 前端只保留：工作台（主 App，按 DSH Desktop 真实配置）与 CLI（第二主入口）；桌宠降为挂件。 | v0.6 新增 |
| D-14 | 登录与账号 | 不做账号系统；仅 API Key | 首次启动走 Codex 式接入全流程；适配 OpenAI-compatible 云端模型；密钥进系统凭据库。 | v0.6 新增 |
| D-15 | 资源来源 | 内置资源库 | 真实教材在工作台资源库中选取；支持学生上传、分类归档、爬虫/导入；入库产出可定位引用。 | v0.6 新增 |
| D-16 | 桌宠底层 | Codex 兼容宠物包 | 采用 `pet.json + spritesheet.webp`、9 个标准动作行（v2 可选 16 向朝向）；事件驱动状态映射。 | v0.6 新增 |
| D-17 | 交付与职责 | 先演示、后维护 | 负责人先交付 v1.0 演示版；其余成员按 MVP-1～MVP-5 维护模块；刘雨烟仅负责计划书商业部分（不含 DESIGN）；指导教师职责按选题→文档撰写全过程履行（§16.7、§16.8）。 | v0.6 新增 |

---

## 十六、团队任务与学习路线

成员与组级职责依据用户提供的分工表：7 名学生、1 名指导教师。v0.6 起实施节奏为：**负责人先交付 v1.0 演示版，其余成员按 MVP-1～MVP-5 维护各自模块**（§10.3）。教育组与数据组成员的个人主责是建议拆分，组内共同负责原分工；姓名转录以正式发布为准。

### 16.1 刘俊鹏｜项目负责人

总体架构设计、Agent Runtime 与后端、Provider Hub、跨端协议、v1.0 演示版集成、申报与结题材料。

- **模块**：`runtime/core/ · runtime/providers/ · runtime/plugins/ · packages/contracts/ · packages/client_sdk/ · api/ · integrations/pi/ · docs/`
- **第一轮任务**：
  1. 先交付 **v1.0 演示版**：按 §10.2 跑通 图→Runtime→Provider→前端 的最小端到端闭环，并冻结 `ClientCommand / RuntimeEvent / ProviderProfile / ModelRequest` schema。
  2. 实现 Provider Registry、OpenAI-compatible Adapter、Secret Ref 与 capability/healthcheck；至少用两套不同 Base URL 的兼容 API 做契约测试。
  3. 实现 Session/Event sequence 重连与 Client SDK 最小版，保证工作台、CLI 与挂件消费同一会话。
  4. 作为组合根装配工具、Skill、绑定表和逻辑模型角色→Provider Profile 映射；`/health` 暴露非敏感装配状态。
  5. 实现仅受信 Python 插件的 manifest、注册、启停和审批；维护真实 Pi RPC Developer Adapter，但不得写 Learner Memory。
- **验收**：v1.0 演示闭环当场可演示；切换 Provider Profile 不改 Graph；重启可恢复；守卫测试全绿。
- **交接**：给全员统一契约、Mock Runtime、FakeProvider 与 Client SDK；接收教育策略、仓储与前端事件消费结果。

学习顺序：

1. [FastAPI 教程](https://fastapi.tiangolo.com/tutorial/)：请求模型、依赖注入、WebSocket 与流式响应。
2. [Pi 文档](https://pi.dev/docs/latest)：重点看四种运行形态与 RPC 嵌入边界（`packages/coding-agent/README.md`），不把 Pi Agent Core 放进学生主链路。
3. [DSH Desktop 架构文档](https://github.com/dataelement/dsh-desktop/blob/main/docs/architecture.md)：理解 Electron 外壳、隔离运行进程、环回地址与用户数据分离。
4. OpenAI-compatible 协议契约：实现最小 Chat/Stream/Tools capability matrix，并用实际供应商或测试 Server 验证。

### 16.2 许阳毅｜教育层开发（资源库与教材 RAG）

教育组共同负责教材 RAG、苏格拉底 Skill、错题本与学情诊断；建议主责**资源库与教材语料**（MVP-4 主责）。

- **模块**：`library/ · skills/rag/ · tools/retrieval/ · data/course_manifest/`
- **第一轮任务**：
  1. 按教材授权、题库质量、知识点标注和教师可评审性选择试点课程，并整理课程清单。
  2. 完成 PDF→章节/页码→切块→向量→检索，保留 document_id、page、chunk_id。
  3. 实现导入器与爬虫最小版：白名单域、限速、robots/ToS 检查、来源与许可字段、SHA-256 去重（§20.3、§20.4）。
  4. 整理至少 20 条人工标注的检索问题，验证引用定位与无证据返回。
- **验收**：检索返回可定位的原文片段；无命中明确返回 insufficient_evidence；资源库每条记录来源/许可/哈希齐全；记录 Recall@k 与失败样例。
- **交接**：给孙一新：检索工具和引用结构。给张钧翔：资源库界面所需的数据契约。给数据组：course_id、concept_id、题目及来源映射。

学习顺序：

1. [PyMuPDF 文档](https://pymupdf.readthedocs.io/en/latest/)：实践抽取一章教材并保存页码。
2. [BGE-M3 模型卡](https://huggingface.co/BAAI/bge-m3)：了解输入、编码与检索示例；先跑小语料。
3. [LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview)：理解检索如何作为**能力**被绑定表引用（节点不直接调用，见 §4.4）。

### 16.3 孙一新｜教育层开发（教学策略图与教育能力）

教育组共同负责教材 RAG、苏格拉底 Skill、错题本与学情诊断；建议主责**教学策略与测验闭环**（MVP-2 主责）。

- **模块**：`graph/education/`（含 `policies/`、`bindings.py`、`contracts.py`、`nodes/`）· `skills/socratic/ · skills/quiz/`
- **第一轮任务**：
  1. 用 FakeRuntime 实现 Assess→条件路由→Teach/Ask/Hint/Correct/Test→UpdateProfile；
     节点只产出 `PedagogicalDecision`，正文与执行细节写进 `bindings.py`（§6.2 绑定表）。
  2. 编写包含先验不足、连续答错、无证据、主动求讲解的教学样例。
  3. 与数据组定义答题事件与学情更新；加入最大轮次、退出与 Reflect 回退。
- **验收**：分支测试覆盖六类教学动作；学生停止时退出；无证据不编造引用；图不能形成无限追问；
  节点源码里不再出现提示模板、提示词、证据获取与 Skill 名（守卫测试会拦）。
- **交接**：向 RAG 组消费证据（经绑定表引用，不直接调用）；向数据组发送 Attempt；
  向前端输出文本、教学动作和情感标签；**向教师提交 `policies/` + `bindings.py` 作为教学策略评审材料**。

学习顺序：

1. [LangGraph 官方文档](https://docs.langchain.com/oss/python/langgraph/overview)：先做带条件边的小图，再接真实 Runtime。
2. [Socratic Education System](https://github.com/HowieWang1121/Socratic-Education-System)：比较递进提问和学情诊断的实现。
3. [OpenMAIC](https://github.com/THU-MAIC/OpenMAIC)：学习教学能力组织；整理可复用的课程与测验模式。

### 16.4 张钧翔｜统一前端开发（工作台 + 桌宠挂件 + CLI）

负责 DSH Desktop 真实配置式工作台、Codex 兼容桌宠挂件、语音链路与 CLI 展示；与负责人共同维护 CLI（MVP-3 主责、MVP-5 协同）。

- **模块**：`apps/desktop/ · apps/pet/ · packages/design_system/`（协同 `apps/cli/`）
- **第一轮任务**：
  1. 建立 Electron Main / Preload / Renderer 三层，按 DSH Desktop 真实配置实现：隔离运行进程、环回地址工作台、RuntimeSupervisor、IPC 守卫、Tray 与 Secret IPC；Renderer 不持有模型密钥（§17.2）。
  2. 实现首次启动 onboarding：Codex 式接入全流程，仅 API Key（§19.3）。
  3. 实现主工作台信息架构：左 Workspace/Sessions，中 Chat/Task，右 Context/Learner，下 Files/Sources/Trace；Provider Settings 可新增/测试/选择 Profile；资源库界面（§20.6）。
  4. 实现桌宠挂件：宠物包校验（`pet.json` + `spritesheet.webp`）、透明置顶浮窗、Director 事件映射、可选 ASR/TTS（§19.8）。
  5. 所有网络与会话交互通过 `packages/client_sdk`；使用 Mock Event Stream 验证加载、流式回复、工具审批、错误、取消和 sequence 重连。
- **验收**：工作台可启动且仅监听环回地址；Provider Settings 不泄露 Secret；CLI 与工作台能恢复同一 Session；挂件状态与事件一致；断线重连不重复展示。
- **交接**：消费负责人提供的 Client SDK 和事件；与孙一新确认教学动作标签；向数据组提交经用户授权的偏好更新；向许阳毅反馈资源库界面对接结果。

学习顺序：

1. [React 入门](https://react.dev/learn)：工作台状态、消息流、可取消请求。
2. [Electron 教程](https://www.electronjs.org/docs/latest/tutorial/tutorial-prerequisites) 与[安全指南](https://www.electronjs.org/docs/latest/tutorial/security)：Main / Preload / Renderer、contextIsolation、sandbox 与受控 IPC。
3. [DSH Desktop 架构文档](https://github.com/dataelement/dsh-desktop/blob/main/docs/architecture.md)：按真实配置实现外壳与运行进程分工，而不是复制 DSH Runtime。
4. [Pi coding-agent README](https://github.com/earendil-works/pi/blob/main/packages/coding-agent/README.md)：学习命令、Session 树、fork/compact 的交互设计。
5. [Awesome Codex Pet](https://github.com/legeling/awesome-codex-pet)：宠物包格式、9 个标准动作行与 v2 朝向约定。

### 16.5 谢浪｜记忆与数据

数据组共同负责记忆系统、数据存储、学情画像与测试；建议主责**仓储与数据生命周期**（MVP-4 协同维护）。

- **模块**：`runtime/storage/ · runtime/memory/ · migrations/`
- **第一轮任务**：
  1. 定义 EventStore、MemoryStore、ProfileStore 抽象，MVP 使用本机 SQLite。
  2. 区分短期、工作、长期、情感、事件记忆；实现来源、版本、过期与删除字段。
  3. 实现迁移、事务、幂等写入、备份恢复与用户级隔离；对外只暴露 API。
  4. 为资源库提供元数据与审计存储：资源记录、上传记录、导入/爬取日志（§20.5）。
- **验收**：重复 event_id 不重复写入；重启恢复一致；备份可还原；一个学生不能读取另一学生记录；资源记录可溯源、可撤回。
- **交接**：给刘俊鹏：EventStore 和 Session 持久化。给欧阳文凯：答题历史和版本化画像仓储。给许阳毅：资源库存储接口。

学习顺序：

1. [Python sqlite3](https://docs.python.org/3/library/sqlite3.html)：练习参数化 SQL、事务与连接管理。
2. [SQLite WAL](https://www.sqlite.org/wal.html)：了解读写并发限制；数据库放本机磁盘，不使用网络共享文件。
3. [pytest 入门](https://docs.pytest.org/en/stable/getting-started.html)：编写隔离数据库 fixture 与恢复测试。

### 16.6 欧阳文凯｜记忆与数据（学情模型与评测）

数据组共同负责记忆系统、数据存储、学情画像与测试；建议主责**学情模型与评测**（MVP-2 协同、MVP-5 评测协同）。

- **模块**：`models/learner/ · evaluation/ · tests/integration/`
- **第一轮任务**：
  1. 与教育组定义 Attempt：学生、题目、知识点、正误、时间、提示次数和来源。
  2. 先实现 BKT 掌握度基线；用 IRT 标定题目与能力，输出模型版本和不确定性。
  3. 通过 LearnerModel 接口比较后续知识追踪模型；按学生划分数据并保持时间顺序。
  4. 维护评测脚本：CLI 与工作台互恢复同一 Session 的联调检查、Provider 切换审计与指标汇总（MVP-5 协同）。
- **验收**：固定样例更新可复现；训练/测试无学生与未来信息泄漏；输出 AUC、LogLoss、校准和样本量，单类数据不计算 AUC。
- **交接**：消费谢浪的答题记录；向孙一新返回知识点掌握估计和测验建议；由教师审核解释是否合理。

学习顺序：

1. [pyBKT](https://github.com/CAHLR/pyBKT)：运行示例，区分初始掌握、学习、猜测与失误参数。
2. [py-irt](https://github.com/nd-ball/py-irt)：从 1PL/Rasch 开始，理解能力与题目难度。
3. [pyKT](https://github.com/pykt-team/pykt-toolkit)：学习知识追踪数据格式与基准；数据充足后比较扩展模型。

### 16.7 刘雨烟｜计划书商业部分（不是 DESIGN）

创业学院人工智能专业，2026-09-19 加入。**只负责计划书的商业部分**，不负责 DESIGN 设计文档，也不承担架构与框架设计职责（v0.6 起明确）。

- **模块**：`docs/（计划书商业章节：商业模式与运营规划、目标客户与获客路径、成本与预算口径）`
- **第一轮任务**：
  1. 完成计划书商业部分：商业模式与运营规划、目标客户与获客路径、成本与预算口径，与计划书第 2 章市场分析使用同一数据口径。
  2. 与负责人对齐分工与里程碑在两份文档中的表述（计划书 ↔ 设计文档的团队与进度信息保持一致）。
- **验收**：商业章节的每个数字都有口径与来源标注；与计划书其他章节口径一致；不与设计文档出现互相矛盾的分工与预算。
- **交接**：给刘俊鹏：商业章节初稿。给唐欢容：待审核的商业与经费口径。给教育组与数据组：成本与资源假设的核对清单。

学习顺序：

1. 大学生创新创业训练计划申报与评审要点：熟悉商业计划书结构与评分维度。
2. [FastAPI 教程](https://fastapi.tiangolo.com/tutorial/)（选读）：了解后端形态，便于与团队沟通技术口径。

### 16.8 唐欢容｜指导教师

**指导项目的选题、资料收集、总体设计、算法设计、代码实现、原型系统实现和文档撰写**（v0.6 起按此职责范围执行）；并在试点课程、教材与题库来源、教学样例与评价标准上提供指导意见。

- **指导抓手**：`graph/education/policies/`（阈值与全部文案）与 `graph/education/bindings.py`（每个动作“怎么做”的数据）——评审教学策略只需审这两处，不必读节点代码。
- **第一轮指导**：
  1. 对选题与总体设计把关：确认分层架构、`RuntimePort`（窄面）与 `RuntimeHost`（宽面）、动作—能力绑定表与资源库边界合理。
  2. 指导资料收集与课程设计：审核试点课程、教材与题库来源，检查知识点和难度映射。
  3. 指导算法设计与代码实现：对照教学流程检查节点条件与回退是否合理，指导诊断/测验算法的选用与解释。
  4. 指导原型系统实现与演示：确认 v1.0 演示闭环保留真实运行证据，明确演示数据与真实实验的边界。
  5. 指导文档撰写：对设计文档、计划书与实验记录的表述与证据口径给出修改意见。
- **验收**：形成可追溯的评审记录、修订建议和阶段结论；明确真实实验与演示数据的边界。

指导参考资料：

1. [OpenMAIC 教学示例](https://github.com/THU-MAIC/OpenMAIC)：观察课程组织与测验形式，形成评审维度。
2. [pyBKT 模型说明](https://github.com/CAHLR/pyBKT)：理解掌握度估计的假设，审查输出解释。
3. [LangGraph 概览](https://docs.langchain.com/oss/python/langgraph/overview)：对照教学流程检查节点条件与回退是否合理。

### 16.9 交付节奏

阶段是相对执行顺序，不代表已完成或承诺日期。

| 阶段 | 团队交付 | 集成门槛 |
| --- | --- | --- |
| 01 v1.0 演示版 | 负责人独立跑通端到端最小闭环（§10.2） | 两项主张可当场演示：同一 Session 跨工作台/CLI 迁移；换 Provider 不改教学策略 |
| 02 MVP-1～MVP-5 迭代 | 各主责按 §10.3 维护模块并补齐样例 | 每轮集成前守卫测试全绿；模块附启动说明、输入输出、失败样例 |
| 03 教师评审与演示 | 检索与教学评审、模型指标、Provider 切换审计、备份恢复、演示脚本 | 教师完成评审记录；负责人归档可复现材料与 Provider Profile（不含密钥） |
| 04 答辩与结题归档 | 设计文档、计划书、演示视频、实验记录 | 两份文档分工/里程碑/预算一致；所有数据均可溯源 |

## 十七、部署与协作

### 17.1 三种交付/运行对象

团队门户、Local Product 与 Campus Product 必须区分，不把“门户上线”误称为教育产品上线。

```text
A. 团队门户
成员浏览器 → Nginx → 门户 HTML + DESIGNv0.6.md

B. Local Mode
DeepProf Desktop / CLI / Pet 挂件
        → Local Gateway / RuntimeSupervisor（环回地址）
        → DeepProf Runtime
        → 本机 SQLite / Vector DB / 资源库
        → Provider Hub → 本地模型或用户自定义 OpenAI-compatible API

C. Campus Mode
Desktop / CLI / Pet 挂件
        → HTTPS / WSS
        → 学校 DeepProf Gateway / Runtime
        → 校内课程资源 / Learner Store
        → Provider Hub → 校级模型网关 / 合规外部 API
```

Local 与 Campus 对上层使用同一 `ClientCommand / RuntimeEvent` 契约，避免维护两套前端。

### 17.2 Desktop 运行时拓扑（按 DSH Desktop 真实配置，v0.6）

参考 DSH Desktop 的真实实现（§11.4），DeepProf Desktop 采用同样的“外壳—运行进程—浏览器窗口”三层：

```text
Electron Main（单实例锁、userData、启动序列）
  → 隔离运行进程：DeepProf Runtime（Python，仅本机）
  → 工作台 UI 仅监听 127.0.0.1 动态端口
  → 沙箱 BrowserWindow 加载工作台
      · contextIsolation: true / nodeIntegration: false / sandbox / webSecurity
      · 禁止 webview、限制导航与新窗口；外链走系统浏览器
  → Preload 仅暴露最小 IPC 白名单；ipc_guard 校验发送窗口与主帧
  → 特权动作：选择目录、重启 Runtime、安全模式、更新安装
```

数据布局与用户数据分离：

```text
Electron userData/
├── launch-root/            # 运行时进程的中性工作目录
├── runtime/                # DeepProf 数据根（会话、设置、资源库索引）
├── bin/                    # 随附辅助二进制
└── update-skip.json        # 记忆的更新选择（如启用）
logs/
└── runtime.log             # 启动与运行诊断
```

状态机至少包括 `STARTING / READY / UNHEALTHY / RECONNECTING / STOPPED`；Runtime 崩溃时 Renderer 只进入 reconnect 状态，Session 事实从 EventStore 恢复。第三方插件导致启动或渲染异常时，提供**非破坏性安全模式**（仅官方核心组件启动，用户数据仍可用），并保留引导恢复入口。更新机制与 DSH Desktop 对齐：启动后、每 6 小时与长休眠恢复后检查，下载与安装均需用户确认。应用升级不覆盖 profile、插件、工作区、会话与模型配置数据。

**明确不照搬**：DSH 的 PPT 模式、手机配对桥、`.dshpreset` 包格式与 Cordis 内部机制不在本版范围内。

### 17.3 Provider Profile 与 Secret 部署

- Local：Profile 元数据存在本机配置库；API Key 进入系统凭据库（Windows DPAPI/凭据管理器、macOS Keychain、Linux Secret Service）。
- Campus：学生端不获得学校 Provider API Key；由服务端 Provider Hub 持有，按账号/课程权限调用。
- Profile 导出默认移除 secret，只导出 `base_url/model/capabilities/headers(non-secret)` 等可迁移字段。
- 自定义第三方 OpenAI-compatible API 需要明确告知数据将发送至该服务；课程敏感资料是否允许外发由管理员策略决定。

### 17.4 门户交付与运行

- `deepprof_framework_v0.6.html`：架构图与团队门户（待同步生成）。
- `team-site/build.mjs`：同步 HTML 与 Markdown。
- `team-site/Dockerfile`、`compose.yaml`、`nginx.conf`：Nginx 静态服务。
- `team-site/README.md`：本地预览、服务器部署、更新和访问检查。

共享资料由负责人合并发布。门户不保存真实学生作答、学情记录或 Provider Secret。

### 17.5 产品端数据部署约束

MVP 数据库只放 Runtime 所在机器的本机磁盘；SQLite 不放网络共享目录。Session 与学习画像逻辑分仓储，按 learner_id 隔离；资源库文件与索引同样保存在本机数据根（§17.2）。产品保留本地学习模式；Campus 模式明确告知数据存储位置和模型 Provider 去向。

---
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
| ClientCommand | command_id, client_id, surface, session_id, learner_id, type, payload, timestamp | Desktop / CLI / Pet → Gateway |
| SessionRequest | session_id, learner_id, request_id, content | Gateway → Runtime 会话接入 |
| RuntimeEvent | event_id, session_id, trace_id, sequence, type, payload, client_id?, surface? | Runtime → Client SDK / Storage |
| ProviderProfile | profile_id, protocol, base_url, api_key_ref, default_model, capabilities, enabled | 接入向导 / Settings → Provider Registry；secret 不进入事件 |
| Evidence | document_id, chunk_id, page, text, source | RAG → 教学图 |
| ResourceRecord | resource_id, course_id, type, tags, source_type, source_url, license, hash, status | 资源库 → RAG / 审计（§20.2） |
| **PedagogicalDecision** | action, concept, level, require_evidence, evidence_sufficient, params | 教学图 → Runtime（§7.1） |
| **CapabilityResult** | content, evidence, records, status, capability, metadata | Runtime → 教学图（§7.1） |
| Attempt | attempt_id, learner_id, item_id, concept_ids, correct, timestamp, hint_count | 教育组 → 数据组 |
| LearnerEstimate | learner_id, concept_id, model_type, model_version, estimate, evidence_count, uncertainty | 数据组 → 教学图 |

一个学习者的数据不能因会话切换或 Surface 切换被覆盖。事件使用唯一标识实现幂等；工作台、CLI 与挂件的流式重连统一按 sequence 恢复，避免重复渲染、重复记分。图 checkpoint 保存教学状态和 Memory 引用，Session 负责交互事实，两者通过 session_id/trace_id 关联。教学图上交决策、Runtime 回传结果时，**教材原文与学情记录都不进图状态**，只保留可定位引用（§6.4）。

### 18.3 首轮验收记录模板

每个模块提交：负责成员、版本、启动方式、输入样例、预期输出、实际输出、失败案例和交接对象。教育样例由指导教师审阅，接口问题由刘俊鹏归口。任何实验结果必须来自实际运行；当前文档与门户不填造成功率或完成百分比。

已有验收记录：

- [MVP-1_验收记录.md](MVP-1_验收记录.md)（`runtime/` · `packages/contracts/` · `api/` · `integrations/pi/`，2026-09-22）
- [MVP-2_验收记录.md](MVP-2_验收记录.md)（`graph/` · `skills/` · `models/learner`，2026-09-22）

---

## 十九、前端：两大模块、桌宠挂件与 Provider Hub

### 19.1 产品形态

DeepProf v0.6 的学生端由**两个主模块**与**一个挂件**组成：

| 模块 | 定位 | 主要场景 |
| --- | --- | --- |
| DeepProf Desktop | 主学习工作台（主 App） | 课程学习、长对话、文档/引用、测验、资源库、Session/Trace/Provider 管理 |
| DeepProf CLI | 键盘优先第二主入口 | 快速问答、恢复 Session、开发/技术课程学习、低干扰使用 |
| DeepProf Pet | 桌面挂件（不是第三前端） | 常驻桌面、随事件表达状态、轻提问/提醒、恢复工作台 |

三者统一通过 `packages/client_sdk` 连接同一 Gateway/Runtime；挂件只消费事件投影与发送轻命令（§19.8）。

### 19.2 工作台信息架构（DSH Desktop 真实配置 + 教育面板）

工作台按 DSH Desktop 的真实工程配置落地（§11.4、§17.2）：Electron 外壳承载完整 Web 工作台，工作台服务仅监听环回地址，Renderer 无 Node 权限与密钥。内容改为 DeepProf 教育场景：

```text
┌──────────────────────────────────────────────────────────────────────┐
│ DeepProf   Workspace/Course     Provider: profile/model        ●     │
├──────────────┬──────────────────────────────────────┬────────────────┤
│ Workspaces   │                                      │ Context        │
│ Sessions     │          Chat / Learning             │ Learner State  │
│ Courses      │          Main Workspace              │ Concept        │
│ Library      │                                      │ Goal           │
│ Review       │  message / tool / quiz / paper       │ Evidence       │
│ Plugins      │                                      │ Pet State      │
├──────────────┴──────────────────────────────────────┴────────────────┤
│ Files | Sources | Session Tree | Trace | Terminal/CLI | Problems     │
└──────────────────────────────────────────────────────────────────────┘
                                                       ◉ DeepProf 娘（挂件）
```

第一版不追求所有面板同时可用，优先完成：

1. **首启接入向导**：API Key onboarding（§19.3），未完成不进入主界面。
2. **左栏**：Workspace/Course + Session 历史 + 资源库入口。
3. **主区**：流式 Chat、工具调用/审批、Quiz、RAG 引用卡片。
4. **右栏**：Learner State、当前教学动作、知识点与本轮证据。
5. **底栏**：Sources、Session Tree、Trace；Terminal/CLI 作为后续可折叠面板。
6. **Settings**：Providers、Models、Voice、Pet、Storage、Privacy。

### 19.3 首次启动与登录（Codex 式，仅 API Key）

**前提（D-14）：本项目暂不做账号系统。**“登录”＝完成一次模型接入；Codex 式体验保留为：首次启动必须完成接入才能进入主界面。

Desktop 首启向导（不进入主界面即可随时退出，重进继续）：

```text
① 欢迎页：界面语言、数据目录、隐私说明
② 接入方式：预置 OpenAI-compatible 模板 或 自定义
     （预置示例：OpenAI / DeepSeek / Kimi / 智谱 / 通义 / OpenRouter /
       硅基流动 / 校园网关 / 本地 Ollama；模板可编辑，不是硬编码兼容承诺）
③ 凭据：API Key（掩码输入） + Base URL（模板自带，可改）
④ 模型：点击「获取模型列表」或手工填写模型 ID
⑤ 测试连接：最小探测请求；失败给出结构化原因（鉴权/网络/模型不存在）
⑥ 完成：选择默认模型并映射逻辑角色（tutor.default / quiz.generator 等，可稍后改）
     → 写入 Provider Profile；API Key 存入系统凭据库，仅保存 api_key_ref
```

CLI 等价流程：`deepprof login`（交互式完成 ③—⑥）、`deepprof models`（列出）、`deepprof doctor`（复测）；与工作台共用同一 Profile 配置库。

共同约束：

- 密钥不进入 Renderer、CLI 历史、日志、Session Event 与仓库；
- 允许保存多套 Profile（个人 API、校园网关、本地模型）；允许跳过后以离线模式进入，随时补齐；
- “退出”是切换/移除 Profile，而不是注销账号；Profile 导出默认剔除 secret（§17.3）。

### 19.4 Provider Settings UI

Provider 页面必须支持：

```text
+ Add Provider
  Display Name     [My Gateway]
  Protocol         [OpenAI Compatible v]
  Base URL          [https://...]
  API Key           [••••••••]   ← 写 SecretStore
  Default Model     [...]
  Extra Headers     [...]
  API Mode          [Auto / Chat Completions / Responses]
  Timeout / Retry

  [Test Connection] [Fetch Models] [Save]
```

Provider 列表显示：名称、Base URL 域、默认模型、健康状态、能力标签、最后测试时间；**永不回显完整 Key**。

每个 Session 记录实际使用的 `provider_profile_id + model_id` 以便审计和复现实验，但不记录 secret。

### 19.5 模型选择与路由

前端可以选择默认 Profile/Model，但教学代码不写厂商名。运行时通过逻辑角色解析：

```text
request.role = tutor.default
      ↓
ModelRouter
      ↓
profile = campus-openai-compatible
model   = configured-model-id
      ↓
OpenAICompatibleProvider
```

课程或管理员可锁定 Provider，避免学生在评测中随意切换导致实验条件变化。

### 19.6 OpenAI-compatible 的兼容等级

为了避免“接口长得像 OpenAI 就当作完全兼容”，Profile 将能力分成三层：

- **L0 Basic**：非流式文本生成。
- **L1 Stream**：SSE/流式增量。
- **L2 Agent**：tools/tool_choice、结构化输出等。
- **扩展能力**：vision、reasoning、Responses API、embedding 另行声明。

启动或用户点击 Test 时运行最小能力探测；探测失败只关闭对应能力，不伪装成功。对教育主链路，如果绑定要求 tools/structured output 而当前 Profile 不支持，返回 `provider_capability_missing`，由上层明确降级或要求换 Profile。

### 19.7 Pi 风格 CLI 全流程

产品 CLI 的定位是**第二主入口**：键盘优先、清晰、无隐藏状态；外观与操作借鉴 Pi 的交互设计（§11.4），但数据源始终是 DeepProf Runtime。

- **启动页**：品牌块字符 Logo（详见项目根目录 `DEEPPROF_CLI_SYSTEM_PROMPT.md`）、右侧显示版本/接入 Profile/默认模型/工作目录。
- **交互区**：输入编辑器（多行、图片粘贴可选）、流式回复、工具调用展示与审批、错误提示。
- **状态行**：当前模型、上下文用量、token/费用、Session 名；关键状态始终可见。
- **命令集（首批）**：`/login`、`/new`、`/ask`（默认输入）、`/resume`、`/tree`、`/fork`、`/compact`；随后补齐 `/model`、`/settings`、`/export`；教育命令如 `/quiz`、`/book` 按需加入。
- **Session 体验**：new / resume / fork / compact 与工作台共享同一 `session_id` 事实源（经 Client SDK，而不是共享本地文件）。
- **开发者模式**：`integrations/pi` 保留真实 Pi RPC（`pi --mode rpc`，严格 LF JSONL）用于嵌入实验；与学生 Learner Session 严格隔离，不得写入学习记忆。

```text
Product CLI:
Pi-style TUI → DeepProf Client SDK → Gateway → DeepProf Runtime

Developer Mode:
Desktop/Dev Tool → integrations/pi → pi --mode rpc
```

### 19.8 桌宠挂件（Codex 兼容底层）

**底层选择（D-16）**：采用与 Codex 宠物一致的包格式与动作约定（§11.4），使 DeepProf 娘可以直接受益于成熟的宠物制作、校验与展示流程。

- **宠物包**：`pet.json`（`id`、`displayName`、`description`、`spriteVersionNumber`、`spritesheetPath`）+ `spritesheet.webp`；安装目录 `~/.deepprof/pets/<pet-id>/`，支持环境变量覆盖数据根。
- **图集与动作行**：v1 图集 1536×1872（8 列 × 9 行）；v2 图集 1536×2288（8 列 × 11 行，含 16 向朝向）。9 个标准动作行：`idle`、`running-right`、`running-left`、`waving`、`jumping`、`failed`、`waiting`、`running`、`review`。
- **状态映射（Pet Director）**：

| 动作行 | DeepProf 触发（事件投影） |
| --- | --- |
| idle | 空闲，等待学生输入 |
| running | 模型生成、检索、工具执行中（`model.stream.delta`、`tool.started`） |
| waiting | 等待审批（`tool.requested` 未决）或等待学生作答（出题后） |
| review | 学生查看引用/复习（Sources 打开、复习模式） |
| failed | 出错或证据不足（`model.failed`、`insufficient_evidence`） |
| jumping | 答对或达成小目标（`quiz.completed(correct=true)`） |
| waving | 打招呼、提醒、恢复会话（`session.resumed`） |
| running-left / running-right | 挂件沿屏幕边缘游走（装饰性状态） |

- **浮窗行为**：透明、置顶、不抢焦点、可拖动、区分点击区与穿透区；托盘可开关；点击挂件展开轻面板（快速提问、恢复工作台）。
- **边界**：挂件只消费事件投影、只发送 `pet.interact` / `voice.transcript`；不持有 API Key，不直接调用模型或 Memory。

### 19.9 v1.0 演示闭环

首个真正可验收的演示不是“UI 都画出来”，而是：

```text
1. 首次启动完成 API Key 接入向导，生成 Provider Profile A
2. 打开课程，从资源库选中内置教材并提问
3. Graph 产生 PedagogicalDecision，Runtime 通过 Provider Hub 生成流式回答
4. RAG 引用在 Sources 面板可定位；Trace 显示 provider_profile/model（无 secret）
5. 桌宠挂件同步表现教学动作（running → review → jumping 等）
6. 关闭工作台后，CLI resume 同一 session_id 并继续对话
7. 新增 Profile B（不同 Base URL）开新 Session，Graph/Skill 代码零修改
8. Profile B 缺少所需 capability 时，系统显式报告并按配置降级，不静默伪装
```

这条链跑通即证明 v0.6 的架构主张成立：**两模块一挂件共享同一 Session**、**模型供应商可替换而教学策略不变**、**资源来源可管理且引用可定位**。

---
## 二十、资源库：教材选取、上传、分类与爬虫

### 20.1 定位与边界

资源库（Resource Library）是工作台内的一等模块，回答一个问题：**“学生要学的真实材料从哪里来、放在哪里、怎么被引用。”**

它负责：资源来源、导入/上传、分类归档、索引与可定位引用。

它不负责：教学决策、讲解文案、提示强度——这些仍属于 Pedagogical Graph 与绑定表。资源库内容不得绕过绑定表直接进入教学流程（§20.7），也不得写入图状态正文（只提供引用）。

数据默认保存在本机数据根（§17.2 `userData/runtime/`）；Campus 模式可改为校内存储位置，边界不变。

### 20.2 资源类型与分类模型

| 资源类型 | 典型来源 | 用途 |
| --- | --- | --- |
| 教材（textbook） | 授权电子版、教师提供、学校图书馆资源 | 主教学语料，RAG 引用第一来源 |
| 讲义/课件（lecture） | 教师上传、课程群共享、公开课程页 | 与教材互补的讲解材料 |
| 论文（paper） | OA 仓库、授权数据库导出、教师提供 | PaperReader 与进阶阅读 |
| 题库（quiz_bank） | 教师整理、公开题库（确认许可）、自建 | 测验与错题本 |
| 学生上传（student_upload） | 学生自己导入的复习资料、扫描笔记 | 个人化学习；默认私有 |

统一元数据字段（ResourceRecord，§18.2）：

```text
resource_id        # 主键
course_id          # 归属课程；未归档时为 null
type               # textbook / lecture / paper / quiz_bank / student_upload
tags[]             # 知识点、章节、难度等
source_type        # import（本地导入）/ upload（学生上传）/ crawl（爬虫）
source_url         # 来源地址（import/upload 记录原始文件路径或来源说明）
license            # 许可与使用范围
hash               # SHA-256，用于去重与版本识别
status             # draft / active / archived
created_by / updated_at
```

分类与归档规则：一门课程一个资源集；类型与标签双维度筛选；学生上传默认仅自己可见，分享需显式开启；教师审核通过后资源才进入课程教材集。

### 20.3 导入、上传与去重

- **导入器**：PDF、EPUB、DOCX、PPTX、Markdown、TXT 为第一批；解析保留章节结构与页码。
- **学生上传**：工作台与 CLI 都提供入口（CLI 为 `library.import` 命令）；上传后进入待归档队列，可补充课程/标签/说明。
- **去重**：SHA-256 命中即提示“已存在”，可选择引用既有资源或作为新版本保存。
- **大文件与扫描件**：超过阈值给出处理提示；扫描件 OCR 作为可选步骤，不阻塞主流程。
- 导入失败必须显式（文件损坏、加密 PDF、格式不支持），不允许静默跳过。

### 20.4 爬虫与来源合规

爬虫只做一件事：把**公开或已授权**的教学资料取回本地并登记来源。

- **白名单域**：默认仅允许配置在 `config/library_allowlist` 中的域名；新增域名需人工确认。
- **合规检查**：抓取前检查 robots 与站点条款；不绕过付费墙、登录墙或 DRM（§13.5）。
- **限速与礼貌抓取**：按域限速、控制并发、支持中断续传。
- **来源登记**：每次抓取记录 `source_url`、抓取时间、内容哈希与抓取参数，写入 `library.crawled` 事件（§5.4）。
- **失败显式**：4xx/403/范围外域名一律显式失败，不尝试规避。

### 20.5 入库与检索管线

```text
文件 → 解析（章节/页码） → 切块 → 向量化 → 资源库索引
                                    │
                                    ▼
                     skills/rag 检索 → 可定位引用（document_id/page/chunk_id）
                                    │
                                    ▼
                        PedagogicalDecision（§6.2：require_evidence）
```

- 索引与向量库按 `resource_id` 组织，支持按课程、类型、标签过滤检索。
- 引用永远是**可定位引用**（`document_id / chunk_id / page / source`），原文不进图状态（§6.4）。
- 资源更新生成新版本，旧版本保留可回滚；删除资源时同步清理索引，并记录审计事件（§18.2、§16.5）。
- RAG 检索走绑定表（§6.2 的 `assess` / `teach` / `correct`），不得由节点直连资源库。

### 20.6 工作台中的资源库

资源库界面（左栏 `Library` 入口）包含四块：

1. **课程资源集**：按课程分组展示教材、讲义、论文、题库；支持“选为当前课程教材”。
2. **添加来源**（三入口）：
   - `Upload`：本地上传文件；
   - `Import file`：指定路径批量导入（适合已下载的教材/论文目录）；
   - `Add from web`：填写 URL 走白名单抓取。
3. **待归档队列**：学生上传与新导入资源在此补充课程/类型/标签后归档。
4. **检索预览**：按关键词/标签查看切块与页码定位，验证引用质量（与 Sources 面板联动）。

### 20.7 与教学链路的边界

- 资源库只提供“材料与引用”，不参与教学决策；教学动作仍是 §6.2 的绑定表落地。
- 资源库内容不得进入事件正文或图状态正文；只以引用与元数据形式存在。
- 学生上传默认私有；未经审核不得作为课程教材被其他学生检索。
- 爬虫与导入操作需要审计（`library.*` 事件），可追溯到人或配置来源。

---

## 附录 A：门户与架构图使用说明

新版 `deepprof_framework_v0.6.html` 计划同时作为架构图与团队门户，可直接打开。首页包含七位成员任务入口，架构页显示教学层与 Runtime 服务的依赖关系，资料页可按成员查看学习路径，决策页列出 D-1—D-17。

可部署副本位于 `docs/team-site/dist/index.html`，设计文档下载副本位于 `docs/team-site/dist/DESIGNv0.6.md`。统一修改根目录的 HTML 与 Markdown 后，**从项目根目录**执行构建，把源同步进 dist：

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
| Provider Hub | 管理 Provider Profile、模型能力、路由、健康状态与 Secret 引用的 Runtime 服务 |
| OpenAI-compatible Profile | 以可配置 Base URL / Model / Secret Ref 接入兼容接口的 Provider 配置，不承诺所有扩展能力完全一致 |
| 接入向导（Onboarding） | 首次启动的 Codex 式接入流程：模板/自定义 → API Key → 模型 → 测试连接 → 完成 |
| Client SDK | Desktop / CLI / Pet 共享的 Session、Event、Command、Approval 客户端层 |
| Presentation Surface | 只负责输入/展示的前端形态，不拥有 Agent / Memory / Provider |
| Session | 可恢复、可分支、可审计的一次连续交互上下文 |
| Event Stream | 追加式记录 Runtime 与教学决策轨迹的事件序列 |
| Plugin Runtime | 负责扩展发现、依赖、挂载、生命周期与权限治理的运行环境 |
| 资源库（Resource Library） | 工作台内的材料来源与归档模块：教材选取、上传、导入、爬虫、分类与索引 |
| ResourceRecord | 资源统一元数据：类型、课程、标签、来源、许可、哈希与状态（§20.2） |
| 宠物包（Pet Package） | `pet.json` + `spritesheet.webp` 的桌宠资源包，兼容 Codex 宠物的包格式与动作行约定 |
| Sprite Atlas（图集） | 8 列网格图集：v1 为 8×9（1536×1872），v2 为 8×11（1536×2288，含 16 向朝向） |
| Pet Director | 把 Runtime/教学事件投影映射为宠物动作行状态的组件 |
| 安全模式（Safe Mode） | 仅加载官方核心组件的非破坏性启动方式，用于排查第三方插件问题 |

---
