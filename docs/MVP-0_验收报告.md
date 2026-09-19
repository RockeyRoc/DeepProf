# MVP-0 验收报告

| 项目 | 内容 |
| --- | --- |
| 项目 | DeepProf（大创） |
| 里程碑 | MVP-0 最小 Runtime |
| 验收依据 | [DESIGNv0.4.md](DESIGNv0.4.md) §10 里程碑表、§12 测试范围、§16.1 负责人验收 |
| 验收日期 | 2026-09-17 |
| 验收环境 | Windows · Python 3.14 · provider=deepseek · model=deepseek-flash · LLM_MAX_TOKENS=4096 |
| 结论 | **通过**（6 项验收全部达成；2 项非阻塞观察项见 §6） |

---

## 一、验收标准与判定

§10 对 MVP-0 的标准为：**「Agent + Session + Event + Provider 跑通终端对话；事件可回放」**。
§16.1 补充为：**「一条带 trace_id 的请求完成模型→工具→事件闭环；重启可恢复；失败与取消可观测」**。

拆成 6 项可实测判据：

| 编号 | 判据 | 判定方法 |
| --- | --- | --- |
| A | 终端对话闭环（Agent+Session+Event+Provider） | 真实 Provider 跑多轮对话，逐块检查输出 |
| B | 事件可回放 | 全量回放 + 断点续传，检查 sequence 单调与去重 |
| C | 重启可恢复 | 重新装配 Runtime 后读回会话 |
| D | Provider 可替换 | 遍历工厂所有 Provider，检查能力声明与错误分支 |
| E | 模型→工具→事件闭环（带 trace_id） | 冒烟脚本真实/离线双模式 |
| F | 失败与取消可观测 | 注入故障复现，检查结构化错误与轨迹 |

判定原则：**以本机实际运行的输出为准**，不接受"代码看起来对"作为通过依据。

---

## 二、逐项验收结果

### A. 终端对话闭环 — 通过

真实 Provider 跑两轮连贯对话，会话 `sess_20163ad13d61`：

| 轮次 | trace_id | 分片数 | 结束块 | 正文长度 |
| --- | --- | --- | --- | --- |
| 第 1 轮「用一句话说明什么是递归。」 | `trace_acc_run_6ab8a0b4cd5a_t1` | 32 | `done` | 52 字 |
| 第 2 轮「那它和迭代的区别呢？」 | `trace_acc_run_6ab8a0b4cd5a_t2` | 160 | `done` | 261 字 |

- 两轮均正常结束并产出可见文本，Agent 循环未空转、未触达轮次上限。
- 会话事件 **203 条**，精确等于 `session.started`(1) + 第 1 轮(37) + 第 2 轮(165)，无遗漏、无重复记账。
- 事件类型覆盖完整生命周期：`session.started`、`agent.started`、`agent.turn.started`、`agent.turn.completed`、`model.requested`、`model.completed`、`model.stream.delta`。
- 第 2 轮能引用第 1 轮语境（"那它和迭代的区别"），证明 Session 上下文在轮间正确累积。

### B. 事件可回放 — 通过

| 检查项 | 结果 |
| --- | --- |
| 全量回放条数 | 203 |
| sequence 严格单调递增且无重复 | 是 |
| 事件归属同一会话 | 是（`same_session` 全为真） |
| 断点续传（`from_sequence=6524`） | 返回 6525–6529 共 5 条，`event_id` 无重复 |

续传语义正确：从已消费位置之后精确续发，不重发边界事件，满足断线重连不重复渲染的要求。

### C. 重启可恢复 — 通过

重新装配 `RuntimeService` 后按 `session_id` 读回会话：

- 消息数 **5**，角色序列 `system → user → assistant → user → assistant`。
- 与两轮对话的预期完全一致，说明会话持久化与重建路径可用。

### D. Provider 可替换 — 通过

| Provider | 装配 | supports_tools | supports_streaming |
| --- | --- | --- | --- |
| `fake` | 成功 | 是 | 是 |
| `deepseek` | 成功 | 是 | 是 |
| `openai` | 成功 | 是 | 是 |
| `qwen` | 成功 | 是 | 是 |

未知名称 `bogus` 得到明确错误而非静默降级：

```
ValueError: 未知 LLM_PROVIDER: 'bogus'，支持 deepseek|openai|qwen|fake
```

`fake` 提供零依赖离线链路，是 MVP-0 可在无网络环境复现的关键。

### E. 模型→工具→事件闭环 — 通过

冒烟脚本 `scripts/smoke_test_mvp0.py` 两种模式均 **7/7 通过**：

| 检查项 | 真实模式 | 离线模式 |
| --- | --- | --- |
| 模型被调用 | 通过 | 通过 |
| 事件挂同一 trace_id | 通过 | 通过 |
| 工具调用进入轨迹 | 通过 | 通过 |
| 本轮成功结束 | 通过 | 通过 |
| 有可见文本输出 | 通过 | 通过 |
| 会话已落盘 | 通过 | 通过 |
| 重启后可读回 | 通过 | 通过 |

- 真实模式：281 条事件，事件类型含 `tool.requested` / `tool.started` / `tool.completed`，证明工具调用确实进入轨迹而非仅停留在模型侧。
- 离线模式：17 条事件，同一套断言全通过，链路可在无网络下复现。
- Provider 流式接口：收到 2 个分片共 4 个字符，多分片流式正常。

### F. 失败与取消可观测 — 通过

**取消**：`tests/integration/test_runtime_turn.py::test_cancellation_is_observable` 覆盖，取消产生 `agent.cancelled` 事件。

**失败**：将 `LLM_MAX_TOKENS` 压到 64 触发真实的截断故障，三个入口分别验证：

| 入口 | 结果 |
| --- | --- |
| Agent 链路（`run_turn`） | 结束块 `kind=error`，`code=model_truncated` |
| 教学图端口（`RuntimePort.generate`） | 末块 `type=error`，全程无 `done` |
| Provider 层 | 如实上报 `finish_reason=length`、`content_len=0`，不抛裸异常 |

结构化错误：

```
{'code': 'model_truncated',
 'message': '模型输出被截断且没有正文（finish_reason=length）；请提高 LLM_MAX_TOKENS 或改用非推理模型。',
 'details': {'finish_reason': 'length', 'provider': 'deepseek'}}
```

关键点：

- Agent 侧事件含 `agent.failed`，同时保留 `model.completed`（`finish_reason=length`）作为审计事实，失败可追溯。
- 截断后会话消息数保持 **2**（system + user），空答案**未污染**会话上下文。
- 端口层与 Agent 层判定一致，图节点经 `collect_stream` 收到 `error` 后会走既有失败分支，不会把空串当成一段正常讲解输出给学生。

---

## 三、测试覆盖（§12 最低测试范围）

`py -m pytest -q` → **150 passed**，覆盖 18 个测试文件：

| 测试文件 | 用例数 |
| --- | --- |
| tests/runtime/test_plugins.py | 17 |
| tests/skills/test_skill_registry.py | 17 |
| tests/graph/test_education_routing.py | 15 |
| tests/runtime/test_providers.py | 13 |
| tests/runtime/test_message_events.py | 12 |
| tests/runtime/test_sandbox_tools.py | 12 |
| tests/runtime/test_sqlite_store.py | 11 |
| tests/graph/test_education_evidence.py | 10 |
| tests/runtime/test_agent_loop.py | 7 |
| tests/integration/test_runtime_turn.py | 7 |
| tests/runtime/test_memory_service.py | 6 |
| tests/runtime/test_session.py | 6 |
| tests/api/test_sessions.py | 5 |
| tests/api/test_teaching_turn.py | 3 |
| tests/integration/test_runtime_plugins.py | 3 |
| tests/api/test_health.py | 2 |
| tests/api/test_tools.py | 2 |
| tests/test_skeleton.py | 2 |
| **合计** | **150** |

§12 所列范围与 MVP-0 的对应关系：

| §12 范围 | 状态 | 对应测试 |
| --- | --- | --- |
| Core 单元测试 | 已覆盖 | test_agent_loop、test_message_events、test_session |
| Provider 契约测试 | 已覆盖 | test_providers（13 项，注入 mock client，不联网） |
| Session 恢复测试 | 已覆盖 | test_session、test_sqlite_store |
| Tool 权限测试 | 已覆盖 | test_sandbox_tools |
| Memory 写入审计测试 | 已覆盖 | test_memory_service |
| Graph 路由测试 | 已覆盖 | test_education_routing、test_education_evidence |
| 端到端教学闭环测试 | 已覆盖 | test_teaching_turn |

其中 Provider 契约测试为本轮补齐。该测试验证的关键协议适配在真实调用中几乎不会触发（终端 REPL 未注册工具，模型不会返回 `tool_calls`），因此用注入 mock client 的方式覆盖了流式 `tool_calls` 按 index 分片拼装、非法 JSON 透传、建连失败与读流中断两条错误路径、请求参数优先级、工厂错误分支。**已用变异验证有效性**：把参数拼接由 `+=` 改为 `=` 后测试立即失败，证明断言真实生效而非空跑。

---

## 四、结论

MVP-0 的 6 项验收判据**全部通过**。

- **达成**：终端对话闭环、事件可回放、重启可恢复、Provider 可替换、模型→工具→事件闭环、失败与取消可观测。
- **超出**：§12 最低测试范围已全部有覆盖；Tool、Memory、Graph、Plugin、API 层的测试同时就位，为 MVP-1/MVP-2 提供了基线。
- **可复现**：离线模式（`--fake`）使全部链路检查无需网络与密钥即可重跑。

至此 MVP-0 可判定完成，具备进入 MVP-1（Tool / Storage / Memory）的条件。

---

## 五、复现方式

```
py -m pytest -q                       # 150 passed
py scripts/smoke_test_mvp0.py --fake  # 离线 7/7，无需密钥
py scripts/smoke_test_mvp0.py         # 真实 7/7，需 .env 配置
py scripts/chat_repl.py               # 交互式终端对话
```

复现失败可观测（PowerShell）：

```
$env:LLM_MAX_TOKENS='64'; py scripts/chat_repl.py   # 应返回 model_truncated 而非空回复
```

---

## 六、非阻塞观察项

以下两项不影响 MVP-0 判定。第 1 项已于 v0.4.1 修订（2026-09-19）处理，保留记录以便追溯；
第 2 项仍待 MVP-1 处理。

**1.（已解决）`build_runtime_service` 的契约与实现不一致**

原问题：[runtime/service.py](../runtime/service.py) 中 `build_runtime_service()` 的文档
声明它是"脚本、API 与集成测试的统一入口"，但它仅转发 `RuntimeService(**overrides)`，
并未注册默认工具与 Skill；实际注册在 [api/app.py](../api/app.py) 内联完成。
后果是 [chat_repl.py](../scripts/chat_repl.py) 与 [smoke_test_mvp0.py](../scripts/smoke_test_mvp0.py)
直接构造出的 Runtime **零工具、零 Skill**；[skills/__init__.py](../skills/__init__.py)
的示例 `register_default_skills(RuntimeService().skills)` 还会把 Skill 注册进随即丢弃的实例。

处理方式（**改文档与装配位置，不改 `runtime/`**）：

| 动作 | 落点 |
| --- | --- |
| 把 `build_runtime_service` 的文档改成事实：它只构造 Runtime，**不装配项目级能力**，并说明为什么不能（`runtime/` 若 import tools/skills 就构成反向依赖，§9.1 有守卫测试） | [runtime/service.py](../runtime/service.py) |
| 装配收敛成 `configure_runtime(service)`：一次注册工具、Skill 与教学绑定表，幂等 | [api/deps.py](../api/deps.py) |
| `create_app` 与 `get_default_service`（进程内单例）共用同一个装配函数 | [api/app.py](../api/app.py) · [api/deps.py](../api/deps.py) |
| 修正误导示例，写明不要 new 一个 RuntimeService 再注册 | [skills/__init__.py](../skills/__init__.py) |
| `/health` 白名单增加 `capabilities` / `action_bindings`，装配是否完整可直接自检 | [api/routes/health.py](../api/routes/health.py) |
| 新增测试：默认单例也必须装配过（单例与 create_app 是两条路径，只装配后者会留下"跑到教学链路才失败"的坑） | [tests/api/test_health.py](../tests/api/test_health.py) |

关闭的**真实风险**：此前只有 `create_app` 走装配，`get_default_service()` 返回的进程内单例
是一个零绑定的裸内核——教学链路会拿到 `no_binding` 并给学生空回复，现场很难看出原因。

有意**未改**：两个脚本保持 Runtime 级定位。`smoke_test_mvp0.py` 自带 `echo` 工具、
专门验证 Runtime 的工具闭环；`chat_repl.py` 是 Runtime REPL，不是教学链路入口。
给它们装上课件工具（当前只有诚实返回证据不足的 `search_textbook`）只会让对话多出无意义
的工具事件。需要跑教学链路时用 `api.create_app` 或自行调 `configure_runtime`。

**2. 流式收尾时的 `GeneratorExit` 噪音**

真实模式脚本结束时输出 `generator didn't stop after athrow()`（httpcore2 + Python 3.14）。根因是 Provider 流式读完后未显式 `aclose()` SDK 的 stream，属清理噪音，不影响任何验收结果，但会干扰日志可读性。建议在 `openai_compat.stream()` 加 `try/finally` 显式关闭。

**状态（2026-09-19 复核）**：仍未处理——`openai_compat.stream()` 目前只有 `try/except`，
没有 `finally` 关闭 SDK stream。修复本身很小（`try/finally: await stream.close()`），
但该现象依赖真实网络与 httpcore2 + Python 3.14 的收尾行为，**在没有真实调用的情况下无法验证修复是否生效**，
因此留到 MVP-1 连同真实 Provider 联调一起做。

---

## 附录：验收证据快照

| 指标 | 实测值 |
| --- | --- |
| 测试总数 | 150 passed |
| 真实对话会话事件数 | 203（= 1 + 37 + 165） |
| 断点续传精确性 | from 6524 → 返回 6525–6529，无重复 |
| 重启恢复消息数 | 5（system/user/assistant/user/assistant） |
| 冒烟测试（真实） | 7/7，281 条事件，含工具事件 |
| 冒烟测试（离线） | 7/7，17 条事件 |
| Provider 能力 | 4 个均可装配，均为 tools+streaming |
| 截断故障复现 | 三入口一致返回 `model_truncated` |