# shared/contracts —— 接口契约层

> **版本**：1.1.0 · **冻结日期**：2026-09-19（v2 复核）
> **负责人**：张钧翔（桌宠前端，DESIGNv0.4.1 §16.4）
> **对接人**：刘俊鹏（Runtime / api，事件生产方）、孙一新（Pedagogical Graph，教学动作生产方）、谢浪 / 欧阳文凯（数据组）
> **依据**：DESIGN**v0.4.1** §16.4 模块清单 `desktop/ · pet/ · shared/contracts/`
> **设计基线变更**：v0.4（2026-09-17）→ **v0.4.1（2026-09-19）**。本层已按新基线复核，详见第六节。

---

## 一、为什么需要这一层

### 1.1 直接原因：这是一处被任务书列名、却从未建立的交付物

DESIGNv0.4.1 §16.4 把 `shared/contracts/` 明确列为张钧翔的模块之一，§16.4 结尾的交接项也写得很清楚：

> **交接**：消费刘俊鹏提供的会话事件；与孙一新确认教学动作；向数据组提交经用户授权的偏好更新。

但项目的总目录树（§9）里**没有 `shared/`**，也没有 `desktop/` —— v0.4.1 更新了 §9 的 `runtime/` 内部结构
（新增 `capabilities.py`、`testing.py`、`graph/education/contracts.py`、`bindings.py`），
**但仍然没有登记 `desktop/` 与 `shared/`**（见 `INCONSISTENCIES.md` 第 15 条）。

结果是：前后端之间的事件契约**一直没有固化下来**，只存在于两处：

| 位置 | 形态 | 问题 |
| --- | --- | --- |
| `DESIGNv0.4.1.md` §5.4 / §6.2 / §6.4 / §7.1 / §18.2 | 散文 + 列表 | 只给了事件**名字**，没有字段、没有类型、没有必填性；而且 §5.4 与 §18.2 的字段清单**仍然互相打架**（见第 13 条） |
| `desktop/src/main/index.js` 的 MockRuntime | 可执行代码 | 字段是真的，但埋在主进程文件里，后端同学要读代码才能知道该发什么 |

于是形成了一个真实的交付缺口：**刘俊鹏要冻结事件 schema（§16.1 第一轮任务 1），
但没有任何一份可以直接照着实现的文件可以冻结。**

### 1.2 这一层要解决的问题

1. **把「事实上已经在跑的东西」写成「可以照着实现的东西」。** Mock Runtime 已经在发 15 种事件了，
   这些字段就是事实标准 —— 只是没人把它们从代码里提取出来。
2. **让后端不必读前端的 Electron 代码。** 刘俊鹏是 Python/FastAPI 技术栈，
   不该为了知道 `model.stream.delta` 该发什么而去读一个 Electron 主进程文件。
3. **让静默失败不再静默。** 前端对未知的教学节点名有 `|| 'idle'` 兜底 —— 不崩、不报错、只是没有表现。
   这类问题在联调时几乎不可能被发现，必须在契约层显式约定。
4. **为文档写了但代码没做的功能留出字段。** 「情感标签」（§7.2）、「好感度交互」（§16.4）
   都写进了设计文档，但事件模型里没有承载（情感标签至今没有）；
   v0.4.1 新增的 `capability` / `capability_status` / `answer_leaked` 同样如此。

---

## 二、文件清单

| 文件 | 内容 | 主要读者 |
| --- | --- | --- |
| `README.md` | 本文件。契约层的存在理由、生产者/消费者关系、v0.4.1 带来的变化、使用与变更流程 | 全员 |
| `events.json` | ⭐ **核心**。事件契约：envelope 公共字段（8 个）+ 15 个已实现事件 + 10 个已声明未实现事件 + 3 个建议新增事件 + §7.1 的决策/结果契约登记 | 刘俊鹏（主要）、张钧翔 |
| `pedagogy-actions.json` | 教学动作（8 个节点）+ **§6.2 动作→能力绑定表全表** + 到桌宠表现的完整映射（PNG 动画 / PNG 表情 / Live2D）+ 运行状态映射 + 优先级链 | 孙一新、张钧翔 |
| `ipc.json` | preload 暴露的 **16 条 invoke 通道 + 2 条推送通道** + 安全约束 + 窗口行为（穿透轮询 / 关窗口=隐藏 / 托盘第一项=退出）+ 接真后端需新增的 5 条通道 | 张钧翔（主要）、刘俊鹏 |
| `INCONSISTENCIES.md` | ⭐ **代码 vs 文档的全部不一致清单**（31 条），每条标「已修复 / 部分修复 / 仍存在 / 已不适用 / v0.4.1 新增未实现 / 本轮新记」 | 全员（尤其文档维护者） |

**版本**：四份契约文件现均为 `1.1.0`（对齐 v0.4.1）。各文件的 `changelog_zh` 段写明了逐项变更。

---

## 三、谁生产、谁消费

### 3.1 事件流向

```text
  后端（未来）                        主进程（现在）                渲染进程
┌──────────────────┐            ┌──────────────────┐        ┌─────────────────┐
│ FastAPI          │            │  MockRuntime     │        │  App.jsx        │
│ Pedagogical Graph│───事件───▶ │  (将来替换为①)    │──IPC──▶│  handleEvent()  │
│ Runtime          │   ①        │  ②               │  ③    │  ④              │
└──────────────────┘            └──────────────────┘        └─────────────────┘
                                    win.webContents.send        window.deepprof
                                      ('runtime:event')           .onEvent()
```

四段的实现状态：

| 段 | 说明 | 状态 |
| --- | --- | --- |
| ① | 后端产生事件（刘俊鹏） | ❌ 未实现 |
| ② | 主进程 MockRuntime 产生事件 | ✅ 已实现（`main/index.js`） |
| ③ | 主进程 → preload → 渲染进程转发 | ✅ 已实现（`runtime:event` 通道） |
| ④ | App.jsx 消费事件驱动 UI | ✅ 已实现（含 `event_id` + `sequence` 双重去重） |

**关键设计**：Mock 放在**主进程**而不是渲染进程。因为按 §7.2，真实事件由后端产生，
渲染进程只负责「消费统一事件」。这样将来换成真后端时，**渲染层一行都不用改**，
只需替换 ② 这一段的实现。

### 3.2 各角色的职责边界

| 角色 | 生产什么 | 消费什么 |
| --- | --- | --- |
| **刘俊鹏**（Runtime / api） | `session.*` · `agent.*` · `model.*` · `tool.*` · `memory.*` · `plugin.*` 事件；§7.1 的 `RuntimePort.execute` 返回值（`CapabilityResult`） | 前端的 `session:send` 请求；前端的偏好更新上报（**仍未实现**） |
| **孙一新**（Pedagogical Graph） | `pedagogy.*` 事件（节点进入/退出、分支决策）；§7.1 的 `PedagogicalDecision` | `memory.read` 的学情估计；证据检索结果（经绑定表引用，不直接调用） |
| **谢浪 / 欧阳文凯**（数据组） | `LearnerEstimate`（§18.2，桌宠不直接消费） | 全部事件的落盘（EventStore）；前端上报的偏好更新（**仍未实现**） |
| **张钧翔**（桌宠前端） | `session:send` / `cancel` / `reconnect` 三条 IPC 请求；`pet:walk`（纯本地窗口状态） | **全部 runtime 事件** |
| **唐欢容**（指导教师） | —— | 评审入口（v0.4.1 §16.7）：`graph/education/policies/` + `bindings.py`；以及**决策事件里的 `capability_status` 与 `answer_leaked`** |

### 3.3 一条重要的边界

§4.4 写明：

> UI 不直连 Tool、Memory 或 Provider；所有动作由 Runtime 接入并产生事件。

因此渲染进程的事件入口**只有 `runtime:event` 一条**（`ipc.json` 已固化这条约束）。
将来任何新功能都不得为图方便另开一条专用推送通道 —— 正确做法是新增一个事件类型，登记进 `events.json`。

v0.4.1 把这条精神**在后端也强化了一遍**：`RuntimePort` 收窄为 `execute + emit`，
图节点拿不到 `RuntimeHost` 宽面，只能出决策。**两个方向上是同一条原则：能力要经统一的接缝暴露，不能随手直连。**

唯一的例外是 `pet:walk`：它由主进程的**漫游/拖动**逻辑直接发出，语义是「窗口正在移动」，
不是「Runtime 发生了什么」，**不是 Runtime 事件**。后端不要试图生产它（见 `INCONSISTENCIES.md` 第 21 条）。

---

## 四、事实标准声明（最重要的一条规则）

> ### 本契约层的事实标准是**代码**，不是文档。

`events.json` 的 `source_of_truth` 字段逐条写明了每个定义来自哪个文件。当 DESIGNv0.4.1 与代码冲突时：

- **以代码为准冻结契约** —— 因为代码是**实际在跑、被 UI 实际消费**的东西。
  文档写错了顶多误导人，代码写错了会当场白屏。
- **但把冲突记录下来** —— 不为了「好看」把代码对齐到文档，也不反过来。
  全部冲突逐条列在 `INCONSISTENCIES.md`，标明谁该改、为什么。
- **不擅自修改 `desktop/`** —— 本契约层的作者只拥有 `shared/`。
  前端代码的问题以「建议 + 影响分析」的形式记录，由代码所有者决定何时改。

举例说明这条规则怎么用：

| 冲突 | 文档 | 代码 | 契约取哪个 | 理由 |
| --- | --- | --- | --- | --- |
| 决策字段名 | §6.4 `next_action` / §7.1 `action` | `next` | **`next`** | 前端读的是 `payload.next`，改名会当场失效。三处三个名字的问题记在第 7 条 |
| `delta` 语义 | 未定义 | 累计全文 | **累计全文** | App.jsx 用 `=` 覆盖赋值，与累计语义自洽 |
| `memory.read.query` | §7.1 `dict` | 字符串 | **`dict`** | 这条反过来 —— 代码是 Mock 占位，§7.1 是接口设计；两端都还没实现，取更规范的 |
| `session.compacted` 用途 | 上下文压缩 | 表示「取消」 | **§5.4 的语义** | 代码这里是明显误用，不应被固化 |
| envelope 字段集 | §5.4（7 个）vs §18.2（6 个） | 8 个 | **代码的 8 个** | 文档两处都不完整，代码是两处的并集 |

即：**默认以代码为准，但当代码明显是临时占位或误用时，取文档的语义并在清单中说明。**

---

## 五、怎么用

### 5.1 后端（刘俊鹏）：实现事件总线时

1. 打开 `events.json`，读 `envelope` 段 —— 这是每条事件的公共外壳，**八个字段**一个都不能少。
2. 读 `events` 段 —— 每个 `type` 的 payload 字段、类型、必填性、中文含义、代码实例都在里面。
   `implemented: true` 的是当前前端**已经在消费**的，必须优先实现。
3. 读 `typical_sequences` 段 —— 六种典型场景（正常一轮 / 取消 / Provider 报错 / 网络错误 / 重连）
   的**完整事件顺序**。⚠️ 正常一轮现在会走 **6 个教学节点**（约 9~10 秒、40~60 条事件），
   与 v0.4 时代完全不同，另附 `normal_turn_notes_zh` 说明 6 段 delta 各自累计这件事。
4. 实现完成后，逐条对照 `consumer_checklist_zh` 的 **16 条**自检项（含 v0.4.1 新增的 6 条）。
5. 特别注意两处最容易踩的坑：
   - `model.stream.delta` 的 `delta` 是**累计全文**，不是增量；
   - **取消**只发 `agent.failed { reason: 'cancelled' }`，**不要**发 `session.compacted`。
6. **v0.4.1 的三条新要求**（本节最重要）：
   - `pedagogy.decision` 要带 `capability` / `capability_status` 与依据字段（第 24 条，P0）；
   - `hint` / `test` 要声明 `answer_leaked`（第 27 条）；
   - 决策事件里**不得含学生正文**，`params` 本身不要 emit（第 28 条）。
7. `proposed_events` 段是建议新增的事件（情感标签、好感度、偏好上报），**当前前端不消费**，
   但建议先把字段留出来。
8. 读 `pedagogical_decision_contract` 段 —— §7.1 的 `PedagogicalDecision` 与 `CapabilityResult`
   两个契约对象的登记。⚠️ 它们**不走事件流**，登记在这里是为了让前端知道将来会以什么形状出现，
   以及**桌宠要看到 §7.3 的失败语义，前提是后端把它转写成事件**（第 25 条）。

### 5.2 后端（孙一新）：实现教学策略图时

1. 打开 `pedagogy-actions.json`，先读 **`v041_bindings_reference`** 段 —— 这是 §6.2 的
   **动作 → 能力绑定表全表**（11 个动作），含 `capability` / `require_evidence` /
   `prefix` / `suffix` / `fallback` / `insufficient_text` / `memo` 与两种模板块形态。
2. `payload.node` 的取值必须是 `actions` 的键名（**PascalCase**），大小写和空格都不能变。
3. 读 `backend_contract_zh` 段 —— 10 条要点，其中最重要的一条是：
   **前端不会因为收到未知节点名而报错，只会静默退回 idle**。
   所以新增节点时必须同步通知前端补映射，不能指望测试时暴露问题。
4. `Reflect` 节点的 PNG 动画映射**已补**（→ `think`），可以放心发出；
   但 Live2D 映射仍缺 `Reflect`（第 3 条）。
5. **两条 v0.4.1 的硬约束**：
   - 绑定到 `render_template` 的动作（`hint` / `reflect` / `end`）**绝不调用模型** ——
     不要发 `model.requested` / `model.stream.delta`，否则桌宠会显示「正在思考」，与「确定性渲染」矛盾（第 26 条）；
   - 证据只在**同一轮内检索一次**（绑定声明 `memo`），避免出现「Assess 判证据充分、Teach 却说证据不足」的自相矛盾。

### 5.3 前端（张钧翔）：改动桌宠表现时

1. 打开 `pedagogy-actions.json`，先读 `implementation_status_warning_zh` 段 ——
   它告诉你**哪张映射表是真正生效的**，避免改了半天没效果。
2. 当前真正生效的链路是：`NODE_TO_ANIMATION` → `ANIMATIONS[].frames` → `RigPet` / `FramePet`。
3. `petState.js` 里的 `NODE_TO_EXPRESSION` / `resolveExpression` / `assetUrl` 是**死代码**（无人 import）；
   而 `NODE_TO_LIVE2D` / `resolveLive2D` **是活的**（Live2D 现在可以在右键菜单切过去）。改动前先确认。
4. `ipc.json` 的 `window_behavior` 段记着几条**从签名上看不出来**的行为：
   穿透由主进程 50ms 轮询决策、关窗口 = 隐藏、托盘第一项是退出。
5. `ipc.json` 的 `future_channels` 段列出了接真后端时需要新增的五条通道
   （连接、偏好上报、工具审批、TTS 流式化、capability 状态）。
6. **两处待修的前端问题**（都只有一行）：`toggleThrough` 传错了参数（第 30 条）、
   `NODE_TO_LIVE2D` 缺 Reflect（第 3 条）。

### 5.4 数据组（谢浪）：实现 EventStore 时

1. `events.json` 的 `envelope.event_id` 是幂等写入的依据（§16.5 验收项「重复 event_id 不重复写入」）；
   `envelope.sequence`（✅ 代码已实现）是**断线续传与顺序恢复**的依据（§18.2）。
2. `memory.write` 需要补 `source` / `confidence` / `ttl` / `revocable` 四个字段。
   ⚠️ v0.4.1 §6.2 明确这四个字段由**节点**构造，绑定只声明「写进去」—— 责任方是孙一新那边（第 11 条）。
3. 注意 §5.4 的隐私纪律：**决策事件的载荷里不得包含学生正文**。
   ⚠️ 但 `agent.turn.started.payload.input` 带的是**未脱敏的完整学生输入**，
   且它会落盘。§13.2 要求「Session 轨迹和学习画像分库存储，**导出时默认脱敏**」——
   **请确认这条对 `agent.turn.started` 生效**（第 28 条）。
4. `petStats.js` 的 `deepprof.preference.v1` 已在 `events.json` 中逐字段登记，后端可照此收数；
   **缺的只是通道**（第 23 条）。

---

## 六、v0.4.1 带来了什么变化

**先说结论：v0.4.1 的 D-7 修订对桌宠前端零改动需求。**

| v0.4.1 的变更 | 影响谁 | 对本层的影响 |
| --- | --- | --- |
| 教学图只产出声明式决策 `PedagogicalDecision`（新增 `action` / `reveal_answer` / `require_evidence` / `params` 等字段） | 孙一新 | 新增 `pedagogical_decision_contract` 段登记该契约 |
| `RuntimePort` 收窄为 `execute + emit`；能力面拆为 `RuntimeHost` | 刘俊鹏 | 同上。`emit` 留在窄面的理由（事件轨迹是图自己的可观测行为）已记录 |
| 新增 `CapabilityResult`（含 `status` 六态） | 刘俊鹏 | 登记该契约，并指出**桌宠看不到它**（第 25 条，P0） |
| 新增 §6.2「动作 → 能力绑定表」（`bindings.py`） | 孙一新 | `pedagogy-actions.json` 新增 `v041_bindings_reference` 段，全表登记 |
| 新增 §7.3 四种装配/绑定失败状态，要求**显式失败、不允许静默兜底** | 刘俊鹏 | 第 25 / 26 条。⚠️ 这条与桌宠的表现层**有直接冲突**：学生可见表现是「空回复」，而桌宠无法区分「装配失败」与「没话说」 |
| `pedagogy.decision` 升级为「实验复盘的主要依据」，新增 11 个必填字段 | 刘俊鹏 | **第 24 条，本轮最高优先级缺口** |
| `hint` / `test` 新增 `answer_leaked` | 刘俊鹏 | 第 27 条 |
| 新增隐私纪律：决策事件不得含学生正文 | 刘俊鹏 + 谢浪 | 第 28 条 |
| §16.7 教师评审入口改为 `policies/` + `bindings.py` | 唐欢容 | `pedagogy-actions.json` 已在绑定表段记录 |
| §9 目录树补充 `runtime/` 内部结构 | 文档维护者 | `pet/` 的命名冲突已被注释澄清（第 15 条，**部分改善**） |

**v0.4.1 明确没有修正的**（v0.4 时代就有，本轮原样带过来）：§5.4 与 §18.2 的 envelope 字段打架（第 13 条）、
教学动作数量 6/7/8 三种说法（第 14 条）、§9 缺 `desktop/` 与 `shared/`（第 15 条）、
`Update Profile` 带空格（第 16 条）、`next_action` 命名（第 7 条 —— 反而又多了一个 `action`）。

---

## 七、变更流程

契约是**冻结**的（`status: "frozen-draft"`），但不是不可改。改动规则：

1. **新增事件类型**：先在本目录对应文件里定义（含全部字段与中文含义），升级 `version`，再改代码。
   顺序不能反 —— 这正是 §12「接口先行：Runtime Port、事件 schema、Plugin manifest 先于具体实现」的要求。
2. **修改已有字段的语义**（尤其是 `model.stream.delta` 这类）属于**破坏性变更**，
   必须前后端双方确认，并同步评估 `App.jsx` 的改动量。
3. **新增可选字段**：向后兼容，前端必须忽略不认识的字段而不是报错
   （`events.json` 的 `envelope.payload` 已写明这条原则）。
4. **每次改动都要更新 `INCONSISTENCIES.md`**：某条不一致被修掉了就标记为已解决并写明修复方式，
   不要直接删掉 —— 保留处理痕迹便于追溯（§11.3「任何代码引入前都要记录」的同类精神）。
   **本版就是这么做的**：v1 的 23 条全部逐条复核并标了状态。
5. `version` 采用语义化版本：新增字段 +0.0.1，新增事件 +0.1.0，破坏性变更 +1.0.0。
   **设计基线升级（v0.4 → v0.4.1）不属于以上任何一类**，本层的处理是 +0.1.0 并在
   `design_baseline` 与 `changelog_zh` 两段写明。

---

## 八、本次交付的边界

**做了什么**：

- 把契约层从 **v0.4 对齐到 v0.4.1**，四份契约文件全部升到 `1.1.0`。
- **逐条复核了 v1 记录的 23 条（16 条不一致 + 7 条前端缺陷）**，标出
  ✅ 已修复 5 条（1 / 9 / 17 / 19 / 22）、🟠 部分修复 3 条（3 / 5 / 23）、
  ❌ 仍存在 14 条、⚪ 已不适用 1 条（20）。
- **新增 8 条**（编号 24~31）：v0.4.1 新提出但我们未实现的 6 条（24~29）
  + 本轮新记录的前端缺陷 2 条（30 两条界面不可达的交互路径、31 `cancelled` 复用 `sad` 表情）。
- 逐行比对了 `main/index.js`（Mock Runtime + 16 条 IPC + 穿透轮询 + 窗口）、`preload/index.js`、
  `App.jsx`、`petAnimations.js`、`petState.js`、`petStats.js`、`persona.js`、`tts.js`、
  `FramePet.jsx`、`RigPet.jsx` 与 DESIGNv0.4.**1** 的 §3.2 / §3.4 / §4.1 / §4.4 / §5.1 / §5.4 / §5.5 /
  §6.2 / §6.3 / §6.4 / §7.1 / §7.2 / §7.3 / §9 / §12 / §13 / §16.3 / §16.4 / §16.5 / §16.7 / §18.2。

**没做什么（明确声明）**：

- ❌ **未修改 `desktop/src/` 下的任何文件。** 包括 `App.jsx`、`main/index.js`、`petAnimations.js`、
  `petState.js`、`petStats.js`。所有对前端代码的修改建议都以文字形式记录在 `INCONSISTENCIES.md`
  与 `pedagogy-actions.json` 的 `suggested_mapping` 中（例如第 30 条的 `toggleThrough` 一行修复），
  由代码所有者决定何时执行。**这是本次任务的硬约束**（有其他成员正在改这些文件）。
- ❌ 未修改 `DESIGNv0.4.1.md`。文档问题（第 13~16、29 条）已逐条给出修订建议，
  但改文档是文档维护者的职责。
- ❌ 未新增依赖，未执行任何安装命令。四份契约文件均为纯数据（JSON）与纯文本（Markdown），
  无运行时依赖，Python 可直接 `json.load()` 读取，Node 可直接 `require()`。

**遗留的、需要他人决策的事项**：

| 事项 | 需要谁决策 | 相关条目 |
| --- | --- | --- |
| `pedagogy.decision` 补齐 11 个 v0.4.1 字段 | 刘俊鹏（**P0**） | 第 24 条 |
| `CapabilityResult.status` 怎么让前端看到（写进 decision？还是新事件？） | 刘俊鹏 + 张钧翔 | 第 25 条 |
| 「装配失败的空回复」桌宠该不该有明确表现（§7.3 说「响亮地暴露」，但学生看到的是空白） | 刘俊鹏 + 唐欢容 + 张钧翔 | 第 25 条 |
| `learner_id` 的生成与持久化方案（建议本地匿名 UUID）；`request_id` 由前端生成 | 张钧翔 + 刘俊鹏 | 第 12 条 |
| `sequence` 改为「同一 session_id 内从 1 起」 | 刘俊鹏 | 第 1 条残留 |
| 情感标签的枚举取值是否直接对齐现有表情名 | 孙一新 + 张钧翔 | 第 4 条 |
| 好感度是否进 MVP；`STAGES` / `GAIN` / `RATE` 三组参数是否纳入版本管理 | 团队（涉及 §16.4 工作量） | 第 5 / 23 条 |
| Mock 的 Hint 段要不要改成「不调模型」（会削弱演示效果） | 张钧翔 + 孙一新 | 第 26 条 |
| `agent.turn.started.payload.input` 的脱敏责任确认 | 谢浪 | 第 28 条 |
| 节点名 ↔ 动作名对照表补进文档 | 文档维护者 | 第 29 条 |
| §9 目录树补 `desktop/` / `shared/` / `team-site/` | 文档维护者 | 第 15 条 |
| `NODE_TO_LIVE2D` 补 `Reflect`；`toggleThrough` 传 `next`；`RigPet` 补 `case 'correct'`（各一行） | 张钧翔 | 第 3 / 30 条 |
| `cancelled` 该用什么表情（建议映射到 `idle`，与 `error` 区分开） | 张钧翔 | 第 31 条 |
| 漫游关闭时「随便走走」的行为（置灰 / 自动开漫游 / 去掉 `!roaming` 条件） | 张钧翔 | 第 30-B 条 |

---

## 九、相关文件索引

| 文件 | 关系 |
| --- | --- |
| `..\..\DESIGNv0.4.1.md` | 设计总纲（**新基线**，2026-09-19）。本契约层的上游依据，也是不一致清单的比对对象。已在团队工作台 `http://182.92.227.120/deepprof/DESIGNv0.4.md` 发布；本地副本与 `DESIGNv0.4.md` 放在同一层目录 |
| `..\..\DESIGNv0.4.md` | 旧基线（v0.4，2026-09-17）。保留用于追溯接口修订前后的差异 |
| `..\..\desktop\docs\` | 阶段 02 集成门槛要求的三份交付文档，与契约层互为「模块视角 ↔ 接口视角」 |
| `..\..\desktop\src\main\index.js` | Mock Runtime、全部 IPC 处理器、穿透轮询、窗口与漫游 —— 事件契约的**事实标准来源** |
| `..\..\desktop\src\preload\index.js` | IPC 暴露面 —— `ipc.json` 的事实标准来源 |
| `..\..\desktop\src\renderer\src\App.jsx` | 事件的**唯一消费方** —— 定义了每个事件在前端产生什么效果，以及去重逻辑 |
| `..\..\desktop\src\renderer\src\petAnimations.js` | 教学节点 → 动画映射（**当前唯一生效的 PNG 映射**） |
| `..\..\desktop\src\renderer\src\petState.js` | 教学节点 → 表情 / Live2D 映射。⚠️ 表情表是死代码；Live2D 表**已生效** |
| `..\..\desktop\src\renderer\src\petStats.js` | 好感度 / 心情 / 精力（本地 localStorage）。其 `exportForDataTeam()` 产出的 `deepprof.preference.v1` 是一份**尚未登记通道的对外数据契约**，已在 `events.json` 中登记字段并给出警告 |
| `..\..\desktop\src\renderer\src\persona.js` | 角色台词库（状态台词、摸头台词按好感度阶段分档）。改语气只改这一个文件 |
| `..\..\desktop\src\renderer\src\tts.js` | TTS 链路 —— 神经语音（edge-tts，经主进程）为主、Chromium 系统合成音兜底 |
| `..\..\desktop\docs\` | 阶段 02 集成门槛要求的三份交付文档（启动说明 / 输入输出 / 失败样例），模块视角 |
