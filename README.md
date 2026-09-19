<div align="center">
  <img src="media/DeepProf.jpg" alt="DeepProf" width="160" />
</div>

# DeepProf：面向高等教育的分层式 Agent Runtime 与教学策略图协同架构

*把"Agent 能做什么"与"教师此刻应该怎么教"彻底拆开——上层策略图只产出声明式决策，下层运行时按注入的绑定表执行。*

[![Milestone](https://img.shields.io/badge/Milestone-MVP--0%20passed-4c6ef5)](docs/MVP-0_验收报告.md) [![Tests](https://img.shields.io/badge/tests-221%20passed-2ea44f)](tests/) [![Python 3.11+](https://img.shields.io/badge/Python-3.11%2B-3776ab?logo=python&logoColor=white)](https://www.python.org/) [![Runtime](https://img.shields.io/badge/Runtime-self--built%2C%20no%20LangChain%20in%20core-8b5cf6)](runtime/) [![Design](https://img.shields.io/badge/Design-DESIGNv0.4.1-0ea5e9)](docs/DESIGNv0.4.1.md) [![Compliance](https://img.shields.io/badge/License%20audit-11%20projects-f59e0b)](docs/license_audit.md)

> 📖 **架构决策、接口契约、依赖规则与验收口径都在版本化设计文档里，请从 [DESIGNv0.4.1](docs/DESIGNv0.4.1.md) 开始。** MVP-0 的验收判据、实测证据与复现命令见 [MVP-0 验收报告](docs/MVP-0_验收报告.md)；参考开源项目的协议核查结果见 [License 核查台账](docs/license_audit.md)。

---

## News 🔥🔥🔥

- **[2026-09-19]** 🎉 **仓库建立，MVP-0 全部源码入库。** Runtime、Pedagogical Graph、API 与 221 项测试一并推送。
- **[2026-09-19]** 🔌 **D-7 接口修订落地（v0.4.1）。** `RuntimePort` 收窄为 `execute + emit` 两个方法，能力面拆为 `RuntimeHost`；图节点不再持有任何正文与执行细节，六类教学动作全部改为"只产出 `PedagogicalDecision`"。新增三条架构守卫测试（依赖方向 / 教学词汇 / 端口分面），违反即失败。
- **[2026-09-17]** ✅ **MVP-0 验收通过。** 6 项判据（终端对话闭环、事件可回放、重启可恢复、Provider 可替换、模型→工具→事件闭环、失败与取消可观测）全部达成，验收以本机实际运行输出为准。
- **[2026-09-14]** 📋 **技术选型与合规基线确立。** 六项架构决策 D-1—D-6 确认，11 个参考项目完成 License 核查。

---

## Overview

**DeepProf 是面向高等教育的个性化伴学智能体**，把教育智能体能力与桌宠式情感交互结合，在长期会话中逐步理解学生的知识状态、学习习惯与情感偏好。项目面向三大核心侧：

| 教育侧 | Agent 侧 | 宠物侧 |
| --- | --- | --- |
| 苏格拉底式提问 | 自研 Runtime | Live2D / PNG 桌宠 |
| 教材 RAG | Session / Event / Tool | 语音与情感反馈 |
| 错题与测验 | Provider / Memory / Plugin | 好感度与主动陪伴 |
| 学情诊断 | Storage / Sandbox | 个性化表达 |

核心思想不是"直接选一个 Agent 框架"，而是把系统拆成**两个可以独立演进、独立评测的层次**：

- **底层 DeepProf Runtime 负责 Agent 能力**：Session、Tool、Model、Memory、Event、Plugin、Storage、Sandbox，Core 参考 Pi 的极简内核，Plugin 思想参考 DeepSeek Harness。
- **上层 Pedagogical Graph 负责教育策略**：什么时候讲、什么时候问、什么时候提示、什么时候纠错、什么时候测试、什么时候更新学情。

系统的研究价值由此从"组合 RAG + LangGraph + Live2D"提升为**可被实验验证的 Runtime—Pedagogy 协同机制**。

### 解耦怎么落地：图只出决策，Runtime 才执行

WHAT（策略）与 HOW（执行）之间只有两张契约，且**教学词汇只存在于图侧的数据里**：

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

 action → capability 的映射 = graph/education/bindings.py（纯数据）
 由组合根 api/app.py 注入 RuntimeService（runtime/ 不得反向依赖教学词汇）
```

这与常见做法的实质差别：

| 常见做法 | DeepProf 的做法 | 得到的性质 |
| --- | --- | --- |
| 策略写在节点 / Skill 内部，正文与调用一起产出 | 节点只产出决策数据，正文与调用由绑定表落地 | 策略层退化为**纯数据**，可反事实回放与离线比较 |
| 两者靠"约定不要互相调"来隔离 | 端口分面 + 架构守卫测试，违反即失败 | 边界可机械检查，不依赖自觉 |
| 换模型 / 换实现要改策略代码 | 改绑定表数据即可 | 换执行方式不动策略 |

因此"Agent 是策略的自动化执行"在本项目里是**可证的**：策略的产物里没有一行正文、没有任何能力名，唯一的下行通道是 `execute`。

### 可研究的核心问题

1. 教学策略图是否比单轮提示词更能稳定执行苏格拉底式教学？
2. Runtime 与 Pedagogical Graph 解耦后，是否能降低新增教学策略的工程成本？
3. 学情记忆反馈是否能提高后续提示、测验和纠错的个性化质量？
4. 事件轨迹是否能支持教学过程复盘、策略比较和可解释性分析？

D-7 让问题 1 与 4 有了更直接的实验手段：决策是纯数据，可以**反事实回放**——固定同一段学情轨迹，只替换决策里的级别或证据条件，重跑执行层即可比较输出差异，不必重跑整个模型链路。

---

## 快速开始

```bash
# 1. 依赖（Python >= 3.11）
pip install -r requirements.txt

# 2. 配置：复制模板并填入自己的 API key
cp .env.example .env          # Windows: copy .env.example .env
#   也可以完全不配密钥：把 LLM_PROVIDER 设为 fake，用确定性 FakeProvider 离线跑通链路

# 3. 跑测试（无需网络与密钥）
py -m pytest -q                       # 221 passed

# 4. 离线冒烟：验证"模型 → 工具 → 事件"闭环
py scripts/smoke_test_mvp0.py --fake  # 7/7，17 条事件

# 5. 终端对话（Runtime 级 REPL）
py scripts/chat_repl.py --fake        # 或去掉 --fake 走 .env 里的真实 Provider

# 6. 起服务
uvicorn api.app:app --host 0.0.0.0 --port 8000
```

### 切换模型

只改配置，不改代码。`LLM_PROVIDER` 支持 `deepseek` / `openai` / `qwen` / `fake`，四者共用 OpenAI 兼容协议适配层；未知 Provider 名会显式抛 `ValueError` 而不是静默回退。

> ⚠️ `LLM_MAX_TOKENS` 默认 4096 而非 1024。推理模型（如 `deepseek-flash`）会先消耗 reasoning token，预算过小会得到 `finish_reason=length` 的空正文。该情形下 Runtime 返回结构化的 `model_truncated` 错误，不会用空内容污染会话上下文。

### 复现失败可观测

```bash
$env:LLM_MAX_TOKENS='64'; py scripts/chat_repl.py   # 应返回 model_truncated 而非空回复
```

### API

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `GET` | `/health` | 运行时健康检查；返回 provider / tools / skills / capabilities / action_bindings / sandbox（白名单组装，不含密钥与本机路径） |
| `POST` | `/sessions` | 创建或恢复会话 |
| `POST` | `/sessions/{id}/messages` | 发送一轮消息，SSE 流式输出（Runtime 链路） |
| `POST` | `/sessions/{id}/teaching-turn` | 按教学策略图执行一轮，SSE 流式输出（Assess → Teach/Ask/Hint/Correct → Test → UpdateProfile） |
| `GET` | `/sessions/{id}/events` | 按 `from_sequence` 回放事件，供前端断线重连且不重复渲染 |

```bash
SID=$(curl -s -X POST localhost:8000/sessions \
  -H "Content-Type: application/json" -d '{"learner_id":"stu_001"}' | jq -r .session_id)

curl -N -X POST "localhost:8000/sessions/$SID/messages" \
  -H "Content-Type: application/json" -d '{"content":"什么是梯度下降？"}'

curl "localhost:8000/sessions/$SID/events?from_sequence=0"
```

失败一律返回结构化错误体（`code` / `message` / `details`），前端与教学图按 `code` 分支，不解析字符串。

---

## 项目结构

```text
DeepProf/
├── runtime/                      # 自研 DeepProf Runtime（不反向依赖上层）
│   ├── core/                     # Agent loop · Session · Event · Message · Ports（窄面/宽面）
│   ├── capabilities.py           # 通用能力原语 + ActionDispatcher（不认识教学词）
│   ├── tools/                    # Tool 注册、校验、审批与执行
│   ├── providers/                # Provider 抽象与 OpenAI 兼容适配（deepseek/openai/qwen/fake）
│   ├── memory/                   # 记忆接口、策略与 SQLite 实现
│   ├── storage/                  # Session / Resource / Trace 存储与迁移
│   ├── sandbox/                  # 目录白名单、网络与进程策略
│   ├── plugins/                  # Plugin Runtime（manifest / registry / lifecycle / trust）
│   └── testing.py                # FakeRuntime（同时满足端口窄面与宽面）
├── graph/education/              # LangGraph 教学策略图（只产出声明式决策）
│   ├── contracts.py              # PedagogicalDecision / CapabilityResult
│   ├── bindings.py               # 动作 → 能力绑定表（纯数据，组合根注入）
│   ├── nodes/                    # assess · teach · ask · hint · correct · test · reflect · update_profile
│   └── policies/                 # 阈值与可评审文案（教师评审入口）
├── skills/                       # 教育能力：socratic · rag · quiz · diagnosis · paper_reader
├── tools/                        # 项目级原子工具（检索等）
├── pet/                          # 桌宠：情感与好感度
├── models/learner/               # 学情模型（attempt / estimate）
├── api/                          # FastAPI 组合根与路由（SSE）
├── config/                       # settings 与路径
├── migrations/ · scripts/ · tests/ · docs/ · media/
└── plugins/example_echo/         # 示例插件
```

**依赖方向**（`tests/test_architecture_boundaries.py` 机械检查，违反即测试失败）：

```text
UI / API → Pedagogical Graph → RuntimePort（窄面：execute / emit）→ ActionDispatcher
                                                                        ↓
                                                            RuntimeHost（宽面）
                                                    Skill / Tool / Provider / Memory
```

`runtime/` 不得导入 `graph/education`、`skills`、`pet`，不得出现教学动作名，也不得直接依赖具体数据库 SDK。

---

## 当前进度

| 阶段 | 目标 | 状态 |
| --- | --- | --- |
| **MVP-0** | 最小 Runtime：Agent + Session + Event + Provider 跑通对话；事件可回放 | ✅ 已验收（2026-09-17） |
| **MVP-1** | Tool / Storage / Memory：Session 重启恢复、学情记忆可读写 | ⏳ 进行中 |
| MVP-2 | Pedagogical Graph：Assess → Ask/Teach → Test → UpdateProfile 主链路可测试 | 🔌 接口部分已完成（D-7） |
| MVP-3 | 教育能力：Socratic、RAG、Quiz、Diagnosis，引用可追踪 | 📋 计划 |
| MVP-4 | Plugin Runtime / Sandbox：插件安装、危险 Tool 审批与边界测试 | 📋 计划 |
| MVP-5 | 桌宠与语音：流式文本、情感标签、语音动作联动 | 📋 计划 |
| v1.0 | 申报演示与研究评测：对照实验、演示脚本、可复现实验记录 | 📋 计划 |

> **诚实原则。** 题库、向量库、学情模型等尚未接入的能力不假装实现：对应 Skill 返回 `{"status": "not_implemented", ...}` 并说明缺什么、由谁补。当前五个教育 Skill 已注册但主体待 MVP-3 落地；`tools/retrieval/search_textbook.py` 在证据不足时如实返回，不编造引用。

---

## 已确认决策

| 编号 | 决策项 | 结论 |
| --- | --- | --- |
| D-1 | 桌宠前端 | Electron + React，Live2D / PNG 与语音走受控接口 |
| D-2 | 试点课程 | 高质量教材与题库优先，由教育组与指导教师落实 |
| D-3 | 事件与数据 | `EventStore` / `MemoryStore` / `ProfileStore` 抽象 + 本地 SQLite |
| D-4 | 插件范围 | 仅加载受信、审核后的 Python 插件，manifest 管理能力与生命周期 |
| D-5 | 隔离方案 | 目录白名单 + 审批；启用任意代码执行前升级进程 / 容器隔离 |
| D-6 | 学情建模 | BKT 跟踪掌握度，IRT 标定题目与能力，统一接口支持后续模型比较 |
| D-7 | 策略与执行的接缝 | 图只产出 `PedagogicalDecision`，Runtime 按注入绑定表执行；边界由三条守卫测试兜住 |

---

## 团队

| 成员 | 角色 | 模块 |
| --- | --- | --- |
| 刘俊鹏 | 项目负责人 | 总体架构、`runtime/core/`、`runtime/plugins/`、`api/` |
| 许阳毅 | 教育层开发 | 教材 RAG、论文阅读 |
| 孙一新 | 教育层开发 | 苏格拉底提问、出题与评价 |
| 张钧翔 | 桌宠前端开发 | Electron + React、Live2D 形象与交互 |
| 谢浪 | 记忆与数据 | 记忆存储与学情数据 |
| 欧阳文凯 | 记忆与数据 | 学情诊断与数据链路 |
| 刘雨烟 | 商业与框架设计 | 计划书商业章节、人工智能框架设计评审 |
| 唐欢容 | 指导教师 | 教学法评审与试点课程 |

---

## 文档

| 文档 | 内容 |
| --- | --- |
| [DESIGNv0.4.1.md](docs/DESIGNv0.4.1.md) | 架构总纲：分层、接口契约、依赖规则、目录树、MVP 路线、团队任务 |
| [MVP-0_验收报告.md](docs/MVP-0_验收报告.md) | MVP-0 验收判据、实测证据、测试覆盖与复现方式 |
| [license_audit.md](docs/license_audit.md) | 11 个参考项目的 License 核查台账与风险处置 |

## 参考项目与合规

架构与接口设计参考了 openai/codex、deepseek-ai/deepseek-harness、1Vewton/dsh-edu、THU-MAIC/OpenMAIC、HowieWang1121/Socratic-Education-System、Zenglian990/AI_Tutor_Release、ZZhouWJ/EduAgent-Studio，以及桌宠方向 suan-11/mea-pet-public、Project-N-E-K-O/N.E.K.O、cqzaaa/AgentPet、JulesLiu390/PetGPT。

**借鉴边界**：仅参考架构与接口设计思路，不复制任何代码。其中 [cqzaaa/AgentPet](https://github.com/cqzaaa/AgentPet) 无 License（按版权法默认视为保留所有权利），仅作概念级参考。核查明细与处置措施见 [license_audit.md](docs/license_audit.md)。

## 许可

本仓库目前**未附 LICENSE 文件**，按版权法默认原则视为保留所有权利（All Rights Reserved）。许可证选型待团队确认后补充；在此之前请勿直接复制、分发或用于商业用途。

## 联系方式

大学生创新创业训练计划项目。问题与协作意向请通过本仓库的 Issue 提出。