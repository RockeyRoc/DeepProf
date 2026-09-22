# MVP-1 验收记录：Runtime、契约与 Provider Hub

| 项目 | 内容 |
| --- | --- |
| 项目 | DeepProf（大创） |
| 模块主责 | 刘俊鹏（MVP-1 主责：Runtime 与后端、Provider Hub、跨端协议） |
| 本轮落地 | 刘俊鹏（在 v0.6 工作区完成验收取证，并补齐 §4 的跨 Profile 集成测试与 1 处纪律修复） |
| 模块 | `runtime/`（`core/` · `providers/` · `memory/` · `storage/` · `sandbox/` · `plugins/` · `capabilities.py` · `skills.py` · `service.py` · `assembly.py` · `testing.py`）· `packages/contracts/` · `api/` · `integrations/pi/` |
| 版本 | DESIGNv0.6.1 / MVP-1 |
| 验收依据 | §10.3 MVP-1 验收标准、§16.1 第一轮任务与验收、§5.6 Provider Hub、§7.1 端口、§7.3 失败与回退、§13.4 密钥边界、§18.2 交接字段、§18.3 验收记录要求 |
| 验收日期 | 2026-09-22 |
| 验收环境 | Windows · Python 3.14.7 · pytest 9.1.1 · provider=MockTransport / FakeProvider（零网络、零真实密钥、可完全离线复现） |
| 结论 | **通过**（§10.3 两项判据均达成；§16.1 五项任务：2、4 达成，1、3、5 部分达成；§16.1 四条验收中 3 条达成、1 条因前端未交付无法验证。**8 项未完成/待补见 §7**，其中第 1 项限定了 fallback 的生效范围） |

---

## 一、交付范围与责任边界

| 归属 | 内容 | 落点 |
| --- | --- | --- |
| 通用 Agent 执行底座 | Agent 循环、Session、Event、Message、Tool、Provider、Memory、Plugin | `runtime/core/` · `runtime/service.py` |
| 能力分发（HOW 侧） | 六个通用原语 + `ActionDispatcher`（D-7）；Runtime 不认识教学词汇 | `runtime/capabilities.py` |
| Provider Hub | Profile / 逻辑角色路由 / 能力探测 / 显式 fallback / Secret 引用 | `runtime/providers/` |
| 持久化与隔离 | EventStore / SessionStore / MemoryStore（SQLite）、SandboxPolicy | `runtime/storage/` · `runtime/memory/` · `runtime/sandbox/` |
| 契约包 | `ClientCommand` / `RuntimeEvent` / `ProviderProfile` / `PedagogicalDecision` / `CapabilityResult` | `packages/contracts/`（5 份 json，`contract_version=1.4.0`） |
| Gateway | 会话接入、命令分发、SSE 事件流、Provider 管理、/health | `api/` |
| Pi 开发者适配器 | 严格 LF JSONL 帧、事件转换、开发者模式标记 | `integrations/pi/`（不接入学生主链路） |

**边界由守卫测试强制**：`runtime/` 不反向依赖 `graph/` / `skills/` / `api/`；`runtime/` 字符串常量不含教学动作词；
`/health` 与 Provider 接口不回显密钥；工作台只监听环回地址（`api_host` 默认 `127.0.0.1`）。

### 本轮补齐与修复（为了让验收可证）

| # | 事项 | 原因 | 落点 |
| --- | --- | --- | --- |
| 1 | 新增跨 Profile 集成测试（3 例） | §16.1 验收要求“切换 Provider Profile 不改 Graph”，此前**无任何断言** | `tests/integration/test_provider_switch.py` |
| 2 | 修复：降级兜底时不再附引用 | 新集成测试暴露——模型失败降级成兜底文案后，检索到的片段仍被当作引用回传（`source_attached=True`），与 §7.3“失败不附引用”矛盾 | `runtime/capabilities.py`（`degraded_to_fallback` + `metadata.evidence_attached`），配套 2 例单测 |

---

## 二、启动方式（可复现）

无需网络与密钥。实测命令与结果：

```powershell
# 1) 全量回归（-o addopts="" 是为了让 pytest 打印汇总行；pytest.ini 自带 -q）
python -m pytest -o addopts="" tests          # 269 passed, 0 skipped

# 2) 本模块用例（契约 + Provider + 插件 + Gateway + Pi 适配器 + 跨 Profile 切换）
python -m pytest -o addopts="" tests/test_contract_consistency.py tests/runtime/test_providers.py `
  tests/runtime/test_provider_registry.py tests/runtime/test_plugins.py tests/api/test_gateway.py `
  tests/integration/test_pi_adapter.py tests/integration/test_provider_switch.py
```

- Gateway 入口：`api.app::create_app`（组合根 `build_service_with_bindings`）；
- 装配辅助：`runtime/assembly.py::build_runtime_service`；
- 测试替身：`runtime/testing.py`（`make_service` / `RecordingHost` / `FakeRuntime`）；
- Provider 契约测试全部走 `httpx.MockTransport` 或 `FakeProvider`，**不联网**（已核实 tests/ 中无真实外部调用）。

---

## 三、验收判据与逐项结果

### 3.1 §10.3 MVP-1 验收标准

| 编号 | 判据 | 判定方法 | 结果 |
| --- | --- | --- | --- |
| A | 契约测试全绿 | `tests/test_contract_consistency.py` 9 例（9 组一致性校验） | 通过 |
| B | Provider 能力探测与显式 fallback 可复现 | `test_provider_registry.py`（探测/回退/禁用拒绝）+ 本轮 §5 实跑证据 | 通过（**生效范围见 §7 第 1 项**） |

### 3.2 §16.1 第一轮任务

| # | 任务 | 结果 | 证据 |
| --- | --- | --- | --- |
| 1 | v1.0 演示闭环 + 冻结 `ClientCommand / RuntimeEvent / ProviderProfile / ModelRequest` schema | **部分**：schema 已冻结；端到端演示闭环**未交付**（`apps/` 不存在） | 5 份 json `contract_version=1.4.0`；`packages/contracts/` |
| 2 | Provider Registry / OpenAI-compatible Adapter / Secret Ref / capability+healthcheck；两套 Base URL 契约测试 | **达成** | `runtime/providers/`；`test_provider_registry.py` 12 例；本轮跨 Profile 集成测试 3 例 |
| 3 | Session/Event sequence 重连 + Client SDK 最小版 | **部分**：SSE 按 sequence 补发已实现并实测；**Client SDK 未交付** | `api/events.py`（§4 实测）；`packages/client_sdk/` 不存在 |
| 4 | 组合根装配 + `/health` 暴露非敏感装配状态 | **达成** | `api/app.py::build_service_with_bindings`；§4 实测 payload |
| 5 | 受信 Python 插件 manifest/注册/启停/审批；Pi Developer Adapter（不写 Learner Memory） | **部分**：插件库与 Pi 适配器均已实现；**插件未装配进组合根**（`service.plugins is None`） | `runtime/plugins/`（8 例）；`integrations/pi/`（14 例）；§4 实测 |

### 3.3 §16.1 验收口径

| 判据 | 结果 | 说明 |
| --- | --- | --- |
| 切换 Provider Profile 不改 Graph | **达成** | 本轮新增证据：两套不同 Base URL 下动作/引用/提示词逐字一致（§6） |
| 重启可恢复 | **达成（存储层）** | `test_sqlite_stores_survive_reopen`、Session/Event 持久化用例；**进程级重启未验证**（无 CLI/工作台可重启） |
| 守卫测试全绿 | **达成** | `test_architecture_boundaries.py` 21 例，0 skipped |
| v1.0 演示闭环当场可演示 | **未满足** | `apps/desktop`、`apps/cli` 未交付（MVP-3 / MVP-5） |

---

## 四、实跑证据（本机输出，非文档推测）

### 4.1 契约包

```
capability_result.json:     contract_version=1.4.0 keys=[CapabilityResult, _failure_contract, _owner, contract_version, statuses]
client_command.json:        contract_version=1.4.0 keys=[ClientCommand, _boundary, _surfaces, command_types, contract_version]
events.json:                contract_version=1.4.0 keys=[_privacy, _source_of_truth, _sse_frames, contract_version, envelope, events]
pedagogical_decision.json:  contract_version=1.4.0 keys=[PedagogicalDecision, _owner, contract_version]
provider_profile.json:      contract_version=1.4.0 keys=[ProviderProfile, _logical_roles, _secret_boundary, contract_version]
```

### 4.2 `/health` 装配状态（无密钥）

```
keys     = ['bindings', 'plugins', 'providers', 'roles', 'skills', 'status', 'tools']
skills   = ['diagnosis', 'paper_reader', 'quiz', 'rag', 'socratic']
tools    = ['search_textbook']
roles    = {"tutor.default": ["mock", "mock-model"]}     # 无可用真实 Profile 时回落 mock（§10.2 演示可用）
plugins  = []  (service.plugins is None -> True)          # 插件未装配，见 §7 第 2 项
providers[0] 可见字段 = ['base_url_host', 'capabilities', 'default_model', 'display_name',
                        'enabled', 'health', 'last_probe', 'profile_id', 'protocol']
```

`base_url_host` 只出主机名、`api_key_ref` / `extra_headers` 一律不出现；
守卫用例：`test_health_endpoint_payload_is_secret_free`、`test_provider_status_never_leaks_headers_or_secret`。

### 4.3 SSE 按 sequence 补发（重连不重复渲染）

```
全部（from_sequence=0）:      [(1, 'session.started'), (2, 'pedagogy.node.entered'), (3, 'pedagogy.decision')]
从 1 之后（from_sequence=1）: [(2, 'pedagogy.node.entered'), (3, 'pedagogy.decision')]
从 3 之后（from_sequence=3）: []
```

断点参数是查询串 `?from_sequence=`；**不支持** `Last-Event-ID` 请求头（见 §7 第 5 项）。

### 4.4 Provider 能力探测（MockTransport，零网络）

```
probe-ok:     status=ok      kind=ok
              capabilities={'stream': True, 'tools': False, 'json': False, 'vision': False,
                            'reasoning': False, 'responses': False, 'embeddings': False}
probe-region: status=failed  kind=region_or_permission_blocked  message=provider probe-region 返回 HTTP 403
```

探测只发固定的最小请求（`PROBE_PROMPT` + `max_tokens=512`），**不发送任何用户对话内容**；
空正文 + `finish=length` 记为 `inconclusive` 而不是失败（避免推理模型假阴性）。
错误分类以 HTTP 状态码优先（403 归为 region/permission，不误判为 401 鉴权失败）。

### 4.5 Pi 开发者适配器（仅开发者模式）

```
adapt_event({"event": "message.delta", ...}) = {"type": "model.stream.delta", "payload": {"text": "x"},
                                                "session_id": "", "trace_id": "", "source": "pi"}
is_developer_only_event = True
```

`integrations/pi` 只被 `integrations/` 与测试引用，未进入学生主链路（守卫：`test_product_code_does_not_import_pi`）。

---

## 五、Provider 能力探测与显式 fallback（判据 B 的证据）

| 行为 | 证据 |
| --- | --- |
| 逻辑角色 → Profile → Model 路由（教学代码不写厂商名） | `ProviderRegistry.set_role/resolve`；§6 实测 |
| 未配置角色时回落默认角色 / 首个 enabled Profile | `_primary()` |
| **失败禁止静默换模型**：只有显式 `set_fallback` 的链才切换 | `test_provider_registry.py::test_explicit_fallback_records_degraded_from` 等用例；禁用 Profile 被拒绝 |
| 切换后记录 `degraded_from` / `fallback_index` | `ProviderRegistry.generate` |
| 未知 Provider 抛 `ValueError`（不静默） | `get()` / `profile()` / `set_role()` |
| 能力声明 + 探测结果合并进 Profile（只升不降，保守） | `normalize_capabilities` + `probe()` |
| 按 HTTP 状态码分类失败（403 不误判为鉴权失败） | `runtime/core/errors.py::classify_external_failure`；§4.4 实测 |
| 路由记进轨迹（实验可复现） | `model.requested` 事件携带 `role` / `provider_profile` / `model`（§6 实测） |
| ~~能力缺失显式报 `provider_capability_missing`~~ | **未落地**，见 §7 第 8 项 |

---

## 六、跨 Provider Profile 切换不改教学策略（本轮新增证据）

`tests/integration/test_provider_switch.py`（3 例，零网络）：
同一段学情分别走 `api-a.example` 与 `api-b.example` 两套 Profile + 两个不同模型，实测结论：

| 比对项 | 结果 |
| --- | --- |
| 教学动作 / 情感标签 / `hint_level` / `evidence_sufficient` | **逐项一致** |
| 引用（可定位引用列表） | **逐项一致**（引用不随厂商变化） |
| 送给模型的**提示词** | **逐字一致**（策略与提示词在绑定表里，与厂商无关） |
| 决策事件的策略判据字段 | **逐项一致** |
| 模型 / 回复正文 | 不同（`model-a` vs `model-b`；正文分别含各自 host） |
| `model.requested` 事件 | 分别记录 `("profile-a", "model-a")` 与 `("profile-b", "model-b")`，`role` 均为 `tutor.default` |
| 凭据缺失时 | **不发出任何模型请求**，走绑定声明的确定性失败表述；不静默换到另一家模型（§5.6、§13.4） |

---

## 七、未完成项与待补依赖（非阻塞，但必须由责任人补齐）

| # | 事项 | 现状（诚实状态） | 影响 | 责任 / 下一步 |
| --- | --- | --- | --- | --- |
| 1 | **显式 fallback 未接入流式主链路** | `ProviderRegistry.generate`（带 chain）目前**无生产调用方**；`RuntimeService.generate`（Skill / 教学链路走这里）只调 `resolve`，不使用 fallback chain | 判据 B 在 registry 层可复现，但**教学流式链路实际不会按 chain 切换**；崩溃时表现为结构化错误而不是自动降级 | 刘俊鹏：让 `RuntimeService.generate` 走 `resolve_chain`，仅在**尚未产出任何 delta** 时切换，并把 `degraded_from` 写进 `model.requested/模型事件`（需与“不重复渲染”约束一起评审） |
| 2 | **插件未装配进组合根** | `runtime/plugins/` 库齐备（manifest/registry/lifecycle/trust，8 例），但 `service.plugins` 恒为 `None`，无 `register` 调用、无 example 插件、无 `trusted.json` | §16.1 第 5 项只完成一半；`/health.plugins` 恒为空 | 刘俊鹏：确定受信名单位置与装配时机（是否 `start_all`）、补一个最小受信插件样例 |
| 3 | **系统凭据库未实现** | `runtime/providers/secrets.py` 有 `Env / InMemory / Chained`，**无 Windows DPAPI / Keychain / Secret Service 适配** | §17.3 的“API Key 进系统凭据库”目前由环境变量承担；桌面端接密钥前必须补齐 | 刘俊鹏（与 MVP-3 张钧翔对接 Secret IPC） |
| 4 | **Client SDK 未交付** | `packages/client_sdk/` 不存在 | §16.1 第 3 项缺一半；“工作台/CLI/挂件共享同一 Session”无落地件 | 刘俊鹏（MVP-5 协同张钧翔） |
| 5 | **SSE 不支持 `Last-Event-ID`** | 断点只能用查询串 `?from_sequence=` | 浏览器原生 `EventSource` 自动重连会从头补发（由 sequence 去重兜住，不重复渲染，但会多传） | 刘俊鹏：读取请求头并优先于查询串 |
| 6 | **`apps/` 未交付** | `apps/desktop`、`apps/cli`、`apps/pet` 均不存在 | §16.1 验收“v1.0 演示闭环当场可演示”**无法验证** | MVP-3（张钧翔）/ MVP-5 |
| 7 | 评分/量化指标未覆盖 Runtime | 无 Provider 延迟、token 成本、首字时间的度量 | 不影响本轮判据 | 欧阳文凯（MVP-5 评测协同） |
| 8 | **能力缺失未强制校验** | `runtime/providers/capabilities.py` 的 `REQUIRED_CAPABILITIES` 与 `missing_capabilities()` **无任何调用方**（全仓仅定义与再导出）；`probe_provider` 只在探测失败时借 `KIND_CAPABILITY_MISSING` 表达“空正文未截断” | §19.6 承诺的“绑定要求 tools/结构化输出而 Profile 不支持时返回 `provider_capability_missing`”尚未落地：请求前不校验能力，能力不足会表现为运行期错误而非显式降级提示 | 刘俊鹏：在 `resolve` / `RuntimeService.generate` 前置校验（先只强制 `REQUIRED_CAPABILITIES=(stream,)` 以免误伤既有 Profile），并补契约测试 |

---

## 八、测试覆盖

`python -m pytest -o addopts="" tests` → **269 passed, 0 skipped**（exit 0）。

本模块相关用例（实测计数，含参数化展开后的收集数）：

| 测试文件 | 用例数 | 覆盖要点 |
| --- | --- | --- |
| `tests/test_contract_consistency.py` | 9 | 5 份契约与代码的 9 组一致性（事件集/envelope/SSE 帧/statuses/命令类型/Profile 字段…） |
| `tests/runtime/test_providers.py` | 22 | 响应映射、流式 delta/usage/finish、工具调用按 index 装配、SDK 流关闭、HTTP/连接错误分类、探测 4 例（含“不发送对话内容”） |
| `tests/runtime/test_provider_registry.py` | 12 | 角色路由、**显式 fallback 与 `degraded_from`**、禁用拒绝、能力探测 |
| `tests/runtime/test_plugins.py` | 8 | manifest 权限域、受信名单、install→start→stop→uninstall、依赖检查 |
| `tests/api/test_gateway.py` | 21 | 命令分发、会话、SSE 补发、Provider 管理、`/health` 非敏感装配状态、错误映射 |
| `tests/integration/test_pi_adapter.py` | 14 | LF JSONL 帧编解码、事件投影、开发者模式隔离（零网络、零真实进程） |
| `tests/integration/test_provider_switch.py` | 3 | **跨 Profile 切换不改教学策略**（本轮新增） |
| `tests/runtime/test_capability_dispatch.py` | 31 | 分发器契约、装配失败显式化、证据原文/引用边界、**降级不附引用**（本轮新增 2 例） |
| `tests/test_architecture_boundaries.py` | 21 | 反向依赖、教学词汇、端口窄面、密钥边界、环回地址 |
| `tests/test_config_consistency.py` | 17 | Settings 与 `.env.example` 对齐（防配置漂移） |
| `tests/test_paths.py` | 7 | `~/.deepprof` 三层布局与 `DEEPPROF_HOME` 覆盖 |
| `tests/runtime/test_tools_and_sandbox.py` | 11 | Tool schema 校验、审批门、目录白名单 |

---

## 九、交接对象

| 对象 | 交接内容 | 通道 |
| --- | --- | --- |
| 教育组（孙一新 / 许阳毅） | 冻结的 `RuntimePort` 窄面、`CapabilityResult`、六个原语与绑定表契约 | `packages/contracts/` · `runtime/core/ports.py` · `runtime/capabilities.py` |
| 数据组（欧阳文凯 / 谢浪） | EventStore / MemoryStore 抽象与 SQLite 实现、`pedagogy.attempt` 事件通道 | `runtime/storage/` · `runtime/memory/` |
| 前端 / 桌宠（张钧翔） | SSE（`from_sequence` 补发）、命令类型、`/health` 装配状态；**Client SDK 待交付** | `api/` · `packages/contracts/client_command.json` |
| 指导教师（唐欢容） | 装配与失败回退口径（§7.3）：装配失败显式返回、不用兜底文案掩盖 | `runtime/capabilities.py` |
| 全员 | 统一契约、Mock Runtime、FakeProvider 与 Client SDK（后者未交付） | `runtime/testing.py` |

---

## 附录：验收证据快照

| 指标 | 实测值 |
| --- | --- |
| 全量测试 | 269 passed, 0 skipped（exit 0） |
| 契约 | 5 份 json，`contract_version=1.4.0`，9 组一致性校验全绿 |
| SSE 补发 | `from_sequence=0/1/3` 分别补发 3/2/0 条，sequence 单调、不重复 |
| Provider 探测 | 正常 Profile `ok`；403 归为 `region_or_permission_blocked`（状态码优先） |
| 探测隐私 | 只发固定最小请求，不含用户对话内容 |
| 密钥边界 | `/health` 仅出 `base_url_host`；`api_key_ref` / `extra_headers` 不出现；Profile `export()` 剔除 secret 引用 |
| 跨 Profile 切换 | 动作/引用/提示词/策略判据逐字一致；模型与正文不同；`model.requested` 如实记录 profile+model |
| 凭据缺失 | 零模型请求 + 结构化失败表述（不静默换模型） |
| 失败与回退 | 装配失败显式返回 `no_binding` / `capability_not_found` / `invalid_request` / `template_not_found`；降级兜底**不附引用** |
| 架构守卫 | `test_architecture_boundaries.py` 21 例，0 skipped |
| 运行依赖 | MockTransport / FakeProvider，零网络零真实密钥，可完全离线复现 |
| 提交状态 | 工作区改动**尚未提交**（本轮按负责人要求不提交） |