# 代码事实标准 vs 设计文档 —— 不一致清单

> **产出日期**：2026-09-19（初版 12:31 · **v2 复核 15:23**）
> **比对对象**：
> - 文档：`C:\Users\87092\Desktop\DESIGNv0.4.1.md`（**新基线**，2026-09-19，v0.4 → v0.4.1）
> - 旧基线：`C:\Users\87092\Desktop\DESIGNv0.4.md`（v0.4，2026-09-17）
> - 代码（事实标准）：`desktop/src/main/index.js`（Mock Runtime + IPC + 窗口 + 穿透轮询）、
>   `desktop/src/preload/index.js`、`desktop/src/renderer/src/{App.jsx, petAnimations.js, petState.js, petStats.js, persona.js, tts.js}`
>
> **原则**：本清单只做记录，**不修改任何代码**。冻结契约时以代码为准的项已在
> `events.json` / `pedagogy-actions.json` / `ipc.json` 中固化；需要后端或文档改的项在下面逐条给出建议。
>
> **快照说明**：`desktop/` 下有成员正在同时开发。本版复核以 **2026-09-19 15:23** 的代码状态为准
> （`main/index.js` 15:22、`App.jsx` 15:22、`FramePet.jsx` 15:22、`RigPet.jsx` 15:23、`petAnimations.js` 14:22、
> `preload/index.js` 15:21、`petStats.js` 12:30）。引用一律用**函数名/差分描述**而不是行号，避免漂移。

---

## 〇、结论先行（v2 复核）

**初版共记录 16 条不一致 + 7 条前端自身缺陷（初版正文自称「17 条」，本身即一处笔误），编号 1~23。**
本次按 v0.4.1 逐条复核，并新增 8 条（编号 24~31）。**当前共 31 条**：

| 状态 | 条数 | 编号 | 说明 |
| --- | --- | --- | --- |
| ✅ **已修复** | **5** | 1 · 9 · 17 · 19 · 22 | `sequence`、node.exited 顺序、重连提示不显示、Live2D 不可切换、Teach/Ask/Hint 不可区分 |
| 🟠 **部分修复** | **3** | 3 · 5 · 23 | Reflect 的另外两张映射表仍缺；好感度只有 UI 没有通道；`petStats` 契约登记了但没有通道 |
| ❌ **仍存在** | **14** | 2 · 4 · 6 · 7 · 8 · 10 · 11 · 12 · 13 · 14 · 15 · 16 · 18 · 21 | 见下文逐条（含 5 条纯文档问题） |
| ⚪ **已不适用** | **1** | 20 | `scenario` 演示参数 —— 判定为「设计如此」，不再算缺陷 |
| 🆕 **v0.4.1 新提出、未实现** | **6** | 24 · 25 · 26 · 27 · 28 · 29 | capability / capability_status、CapabilityResult.status 承载、answer_leaked、hint 不调模型、params 隐私纪律、动作命名双轨 |
| 🆕 **本轮新记录的前端缺陷** | **2** | 30 · 31 | 两条交互路径在界面上不可达；`cancelled` 复用 `sad` 表情 |

**一句话总结**：**前端侧的缺陷这一轮修掉了大半（5 条修复 + 3 条部分修复），
而后端侧与文档侧的问题一条没动** —— 因为前者有人在写，后者还没有人接手。
v0.4.1 的 D-7 修订（教学图只出声明式决策）**全部落在后端，桌宠侧零改动需求**，
但它新增了一批**决策事件的必填字段**，这些字段的消费者正是做实验复盘和教师评审的人。

**当前最该做的一件事**：`pedagogy.decision` 补齐 v0.4.1 §5.4 要求的 `capability` / `capability_status`
与依据字段（第 24 条）。它是唯一一条**会直接卡住 §3.4 研究问题 4（教学过程复盘）与 §16.7 教师评审**的缺口。

---

## 一、汇总表

状态列：✅ 已修复 · 🟠 部分修复 · ❌ 仍存在 · ⚪ 已不适用 · 🆕 v0.4.1 新增

| # | 状态 | 级别 | 问题 | 文档位置 | 代码位置 | 谁该改 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | ✅ | 🔴 | `sequence` 字段：文档要求、代码没有 | §18.2 | `makeEvent()` | — 已修 |
| 2 | ❌ | 🔴 | `model.stream.delta` 的 `delta` 是**累计全文**，不是增量片段 | §5.4（仍未定义语义） | `runScript()` 的 `tick()` | 后端（按累计实现） |
| 3 | 🟠 | 🔴 | `Reflect` 节点映射：PNG 动画已补，另两张表仍缺 | §6.2 / §16.3 | `petAnimations.js`、`petState.js` | 前端补 2 行 |
| 4 | ❌ | 🔴 | 「情感标签」文档三处承诺，事件模型无承载，代码无实现 | §3.2 / §7.2 / §16.3 | 全无 | 双方 |
| 5 | 🟠 | 🔴 | 「好感度」本地闭环，**没有通道接出去**（UI 已补） | §16.4 / §3.2 | `petStats.js` | 双方 |
| 6 | ❌ | 🟠 | `session.compacted` 被误用作「取消」 | §5.1 / §5.4 | `cancel()` | 后端 |
| 7 | ❌ | 🟠 | 决策字段名：`next` / `next_action` / `action` **三处三个名字** | §6.4 vs §7.1 | `runScript()` | 双方 |
| 8 | ❌ | 🟠 | `reconnect()` 路径下 `trace_id` 为 `null` | §18.2 | `reconnect()` | 后端 |
| 9 | ✅ | 🟠 | `pedagogy.node.exited` 排在 `agent.turn.completed` 之后 | — | `runScript()` 收尾分支 | — 已修 |
| 10 | ❌ | 🟠 | `memory.read.query` 是字符串，§7.1 定义为 dict | §7.1 | `runScript()` | 后端 |
| 11 | ❌ | 🟠 | `memory.write` 缺 source/confidence/ttl/revocable（合规要求） | §5.5 / §13.2 / §6.2 | `runScript()` 收尾分支 | 后端 |
| 12 | ❌ | 🟠 | 前端没有 `learner_id` / `request_id`，§18.2 的 SessionRequest 四字段凑不齐 | §18.2 | 全无 | 双方 |
| 13 | ❌ | 🟡 | 事件公共字段集合：§5.4 与 §18.2 **仍然**互相矛盾 | §5.4 vs §18.2 | 代码取并集 | 文档 |
| 14 | ❌ | 🟡 | 教学动作数量：文档里仍有 6 / 7 / 8 三种说法 | §4.1 / §6.2 / §16.3 | 映射表 8 个 | 文档 |
| 15 | ❌ | 🟡 | §9 目录树里**仍然没有** `desktop/`、`shared/` | §9 vs §16.4 | 实际在 `desktop/` | 文档 |
| 16 | ❌ | 🟡 | `Update Profile`（带空格）vs `UpdateProfile` | §4.1 | §6.2 + 代码 | 文档 |
| 17 | ✅ | 🔴 | `reconnect` 演示时那句「会话已恢复」**永远不会显示** | — | `App.jsx` `model.completed` 分支 | — 已修 |
| 18 | ❌ | 🟠 | `NODE_TO_EXPRESSION` / `resolveExpression` / `assetUrl` 是死代码 | — | `petState.js` | 前端 |
| 19 | ✅ | 🟠 | Live2D 整条链路未启用（`mode` 无 setter） | §16.4 | `App.jsx` | — 已修（有残留） |
| 20 | ⚪ | 🟡 | `sendMessage` 的 `scenario` 参数是演示专用 | — | `preload` / `main` | — 设计如此，迁移时删 |
| 21 | ❌ | 🟡 | `pet:walk` 通道不属于 Runtime 事件流 | §4.4 | `main` 漫游逻辑 | 维持（需显式排除） |
| 22 | ✅ | 🟠 | `Teach` / `Ask` / `Hint` 三个节点的动画几乎看不见 | — | `petAnimations.js` / `Main.runScript` | — 已修 |
| 23 | 🟠 | 🟠 | `petStats.js` 定义了一份对外数据契约，缺通道 | §16.4 | `petStats.js` | 双方 |
| 24 | 🆕 | 🔴 | `pedagogy.decision` 缺 v0.4.1 要求的 `capability` / `capability_status` 与全部依据字段 | §5.4 / §7.1 | `runScript()` | 后端（**最高优先级**） |
| 25 | 🆕 | 🔴 | `CapabilityResult.status` 无事件承载 → 桌宠分不清「装配失败的空回复」与「没话说」 | §7.3 | 全无 | 后端 |
| 26 | 🆕 | 🟠 | `hint` / `reflect` / `end` 绑定 `render_template`「绝不调模型」，Mock 违反 | §6.2 | `runScript()` | 后端 + Mock |
| 27 | 🆕 | 🟠 | `answer_leaked` 未实现（教师评审会直接查） | §5.4 | 全无 | 后端 |
| 28 | 🆕 | 🟠 | 决策事件不得含学生正文；`params` 不得 emit | §5.4 / §6.4 / §13.2 | Mock 合规，但 `agent.turn.started` 带原文 | 后端 |
| 29 | 🆕 | 🟡 | 动作命名双轨：节点名 PascalCase vs `action` snake_case，文档未给映射 | §7.1 vs §5.4 | 事件 `node` vs 绑定表 | 文档 |
| 30 | 🆕 | 🟠 | 两条交互路径在界面上不可达：强制穿透**开了关不掉**；漫游关闭时「随便走走」**点了没反应** | — | `App.jsx` `toggleThrough` / `main` `wanderOnce` | 前端 |
| 31 | 🆕 | 🟡 | `cancelled` 复用 `sad`（难过）表情，与 `error` 视觉重复 | — | `petAnimations.js` `STATUS_TO_ANIMATION` | 前端 |

---

## 二、✅ 已修复（5 条）与 ⚪ 已不适用（1 条）

> 保留处理痕迹便于追溯（§11.3「任何代码引入前都要记录」的同类精神）。
> 这一轮修复全部发生在**前端侧**，且多是在本契约层建立后的一两个小时内完成的。

### 1. ✅ `sequence` 字段：已补上，且前端已用它去重

**原问题**：§18.2 把 `sequence` 列为 RuntimeEvent 必需字段，但 `makeEvent()` 只产出 7 个字段，没有它。
后果是 §16.4 验收项「断线重连**不重复展示**」在技术上无法实现。

**现在怎么做**（`main/index.js` 的 `makeEvent()`）：

```js
let sequence = 0
function makeEvent(sessionId, traceId, type, payload = {}, source = 'runtime') {
  return {
    event_id: uid('evt'), session_id, trace_id,
    sequence: ++sequence,          // ← 新增
    timestamp: new Date().toISOString(), type, payload, source
  }
}
```

渲染端 `App.jsx` 的 `handleEvent` 开头做了**双重去重**：

```js
const seenIdsRef = useRef(new Set())      // event_id 命中即丢，>400 条清空
const lastSeqRef = useRef({})             // session_id -> 已处理到的最大 sequence
// sq <= last 即丢（补发的旧事件）
```

**残留的两点**（不足以推翻「已修复」，但要在契约里写明）：
- Mock 的 `sequence` 是**进程级全局计数器**，不是「同一 session_id 内从 1 起」。当前只有一个
  `session_id`（`uid('sess')` 只在构造时调一次），所以等价；后端的 `session_id` 会变时需按会话分组计数。
  前端 `lastSeqRef` 已按 `session_id` 分桶，无需改动。
- §5.4 的 envelope 清单里**仍然没有 `sequence`**（第 13 条）。契约取代码的 8 字段并集。

**契约已固化**：`events.json` 的 `envelope.fields.sequence` 已改为 `required:true, implemented:true`，
并新增 `transport.dedup_zh` 说明两张去重网。

---

### 3 / 部分. 🟠 `Reflect` 节点映射：PNG 动画已补，另外两张表仍缺

**原问题**：§6.2 定义了 Reflect，§16.3 验收明确要求「Reflect 回退」——也就是说它一定会被发出来。
但 `NODE_TO_ANIMATION` 与 `NODE_TO_EXPRESSION` 都只有 7 个键，没有 Reflect。解析函数末尾的
`|| 'idle'` 兜底会让它**静默退回待机**：不报错、不崩溃、没有任何表现。

**已修的部分**：`petAnimations.js` 的 `NODE_TO_ANIMATION` 现在是 **8 个键**，含 `Reflect: 'think'`。
选 `think` 而不是 `sad` 的理由是：Reflect 是**内部策略调整**，对学生应表现为「再想想」，
不应表现成出错，否则学生会误以为是自己答错了（§13.1）。

**仍缺的部分**：

| 表 | 位置 | 是否有 Reflect | 影响 |
| --- | --- | --- | --- |
| `NODE_TO_ANIMATION` | `petAnimations.js` | ✅ 有 | — |
| `NODE_TO_EXPRESSION` | `petState.js` | ❌ 无 | 无（**死代码**，第 18 条） |
| `NODE_TO_LIVE2D` | `petState.js` | ❌ 无 | **有**：Live2D 已可切换（第 19 条），发 Reflect 会落到默认值 `{Idle,0,0}` —— **这是一个新的静默失败** |

**建议**：前端补 `NODE_TO_LIVE2D.Reflect`（建议 `{ motion:'Idle', motionIndex:0, expression:2 }`，与 Assess 同）。
`NODE_TO_EXPRESSION` 要么接上、要么删掉（第 18 条）。

---

### 9. ✅ `pedagogy.node.exited` 的顺序已修正

**原问题**：Mock 里 `pedagogy.node.exited(Teach)` 排在 `agent.turn.completed` **之后** ——
「本轮已结束」早于「节点已退出」。数据组按「收到 turn.completed = 本轮所有活动已归档」设计会漏掉最后一条。

**现在怎么做**：`runScript()` 的收尾分支顺序是

```
pedagogy.node.exited { node: <最后一个节点> }
memory.write { ... }
model.completed { text }
agent.turn.completed { status: 'ok' }
```

`node.exited` 已先于 `agent.turn.completed`。**所有 node.entered / node.exited 现在成对**，
共 6 进 6 出（Assess→Teach→Ask→Hint→Correct→UpdateProfile）。

---

### 17. ✅ 重连那句「会话已恢复」现在能显示了

**原问题**：`reconnect()` 只发 `model.completed`，不发任何 `model.stream.delta`。
而 `App.jsx` 的 `model.completed` 分支写的是 `const reply = streamRef.current`，
重连场景下它是空字符串 → `if (reply)` 为假 → **消息不入列表、TTS 不朗读**。
用户点「重连」后看到的是：状态闪到 reconnecting，然后直接回 idle，**什么都没有**。
这不是「不重复展示」，是**不展示**。

**现在怎么做**（`App.jsx`）：

```js
const reply = streamRef.current || payload.text || ''
```

优先用累积的流式缓冲，为空时退回事件自带的全文。两个场景都对：
正常流式用累积值（与覆盖语义自洽），重连场景用 `payload.text`。

**顺带**：`events.json` 里 `model.completed.payload.text` 的旧警告（「后端必须保证 delta 与 completed 成对」）
已随之降级为建议 —— 不再是硬前提。

---

### 19. ✅ Live2D 已可切换（有两点残留）

**原问题**：`App.jsx` 里 `const [mode] = useState('png')` 只有 getter 没有 setter，
`mode` 恒为 `'png'`，Live2DPet 分支永不渲染 —— 而 §16.4 第二条明确要求「先用 PNG 跑通表情状态，**再接 Live2D**」。

**现在怎么做**：`mode` 有 setter 了，右键菜单里有「切到 Live2D（链路测试）」/「切回 PNG 桌宠」；
切到 Live2D 后聊天面板还会多出一排 `f00~f07` 的**手动表情按钮**（`manualExpr`）。
所以 `pedagogy-actions.json` 的 `live2d` 列**已生效**。

**两点残留**（记在第 3 条与第 21 条）：
1. `NODE_TO_LIVE2D` 缺 `Reflect`；
2. `resolveLive2D(status, node)` **没有 pose 参数、没有走路分支**，而 `resolveAnimation(pose, ...)` 有。
   所以 **Live2D 模式下漫游时桌宠不会走路，只会平移**。原先 `mode` 恒为 png，这个不对称是潜伏的；
   现在能切过去了，它就变成了一个**可复现的实际缺陷**。

> ⚠️ 用的是 Live2D 官方 Haru 样例模型，**仅开发期验证链路，不进最终交付物**
> （Live2D 授权条款需另行核对）。界面菜单里已标明「链路测试」。

---

### 22. ✅ `Teach` / `Ask` / `Hint` 的动画现在能看出来了

**原问题**：三个节点**都映射到 `'talk'`**，视觉上完全一样；更严重的是 Mock 只在 Assess 后发一次
`node.entered{Teach}` 就紧接着发收尾事件（**同步发出，没有任何延时**），整轮在一帧内跑完，
`status` 立刻回 idle —— **Teach 的 talk 动画根本来不及播**。

**现在怎么做**（两处都改了）：

1. `petAnimations.js`：`teach` / `ask` / `hint` 三个独立动画名，各有独立素材与参数
   （讲解稳稳地讲、提问歪着头慢、提示小幅快弹）；`Correct` 也不再借用 `sad`，有了独立的 `correct`。
2. `main/index.js` 的 `runScript()`：走**完整 6 个节点**（Assess→Teach→Ask→Hint→Correct→UpdateProfile），
   每个节点之间有 **800~900ms 的 dwell**，让动画播得出来；每个节点各自有一段逐字流式文本。

**副作用（值得知道）**：一轮的时长从 v0.4 时代的约 1 秒变成了**约 9~10 秒**。
演示时说服力强了很多，但如果评审只想看「一轮快速跑通」，现在的体感是偏长的。

---

### 20. ⚪ `scenario` 参数：已不适用（设计如此，非缺陷）

`window.deepprof.sendMessage(text, scenario)` 的第二参数取值 `normal | loading | error | reconnect`，
是现场触发四种 UI 状态的**演示开关**，服务的是 §16.4 第一条。
**真实后端没有这个概念**，契约已把它标为「迁移时必须删除或忽略」。
本次复核未发现它被误用进真实链路，故由「缺陷」改判为「已不适用（设计如此）」。
`ipc.json` 的 `session:send` 条目已保留这条警告。

---

## 三、❌ 仍存在的项

### 🔴 阻断级

### 2. `model.stream.delta` 的 `delta` 是**累计全文**，不是增量

**代码怎么做**（`runScript()` 的 `tick()`）：

```js
j += 4
this.emitEvent('model.stream.delta', { delta: st.text.slice(0, j), index: j })
```

`st.text.slice(0, j)` 是从第 0 个字符到第 j 个字符的**完整前缀**。消费方 `App.jsx` 也是按累计语义写的
（`streamRef.current = payload.delta` —— **覆盖**，不是追加）。两者自洽。

**⚠️ v0.4.1 有一处新变化放大了这个坑**：Mock 现在一轮有 **6 段**流式文本（6 个节点各一段），
每一段都**从空串重新累计**。所以气泡里的文字是「逐段替换」而不是一路增长。
而 §6.2 给后端的建议是「中间节点只发 entered + delta」—— 若后端按**一段长流**实现，
前端会表现为「每段覆盖前一段」，看上去像文本被吞掉。

**文档怎么说**：§5.4 **仍然只列了事件名，完全没有定义 payload 语义**。v0.4.1 未修正。
所以这不是「文档与代码冲突」，而是**契约空白** —— 恰恰是最危险的一种。

**影响**：字段名叫 `delta`，任何人都会理解为「增量片段」。若后端按增量实现，前端会**只显示最后一片的几个字**，
而且 `model.completed` 时用 `streamRef.current` 作为最终回复入库 + 朗读，等于把残缺文本当成完整回答。

同类问题：`index` 字段名看起来像「分片序号」，实际是**字符偏移量**。

**建议**：契约已锁定为**累计语义**（`events.json` 已固化，并新增了 `typical_sequences.normal_turn_notes_zh`
说明 6 段各自累计）。后端必须照此实现。若后端坚持发增量，则必须同时改 `App.jsx` 为 `+=` —— 破坏性变更，需双方签字。

---

### 4. 「情感标签」被文档承诺了三次，事件模型里没有，代码里也没有

**文档怎么说（v0.4.1 **没有**修正，反而更明确了）**：

- §7.2 主会话链路：「Runtime 流式输出文本与**情感标签** → 交互层驱动桌宠表达」
- §3.2 宠物侧：「语音与**情感反馈**」
- §16.3 交接：「向前端输出文本、教学动作和**情感标签**」
- §16.4 标题：「桌宠形象集成、**情感反馈**、好感度交互与语音链路」

**代码怎么做**：`grep` 整个 `desktop/src`，**没有任何 `emotion` / `sentiment` / 情感字段**。
桌宠的表情完全由前端**自己猜** —— `resolveAnimation(status, node)` 只看运行状态和教学节点名。

**影响**：§7.2 把「情感标签」写成了架构链路的一环，但 §5.4 的事件模型里**没有任何事件承载它**，
§7.1 的 `RuntimePort` 协议里也没有对应方法。这是一条**只存在于文字里的链路**。
后端将来真做了情感分析，**没有地方发**；前端想接也**没有字段可读**。

**建议**：`events.json` 的 `proposed_events.emotion.tagged` 已定好形状
（`emotion` 枚举 + `intensity` + `trace_id`）。建议取值与 `petState.js` 现有表情名对齐。

---

### 🟠 语义 / 顺序 / 合规错误

### 6. `session.compacted` 被用来表示「取消」（**未修**）

**代码怎么做**（`cancel()`）：

```js
this.emitEvent('agent.failed', { reason: 'cancelled', message: '本轮已取消' }, 'renderer')
this.emitEvent('session.compacted', { reason: 'cancelled' })   // ← 问题在这
```

**文档怎么说**：`session.compacted` 在 §5.4 里与 `session.started / session.resumed / session.ended` 并列；
§5.1 的 Session 组件接口是 `load / append / fork / **compact**` —— `compact` 指**上下文窗口压缩**
（对话太长，把早期历史摘要化），与「用户取消本轮」毫无关系。

**影响**：两件事被塞进同一个事件名。后端实现 compact 时前端会误以为用户取消了；反之亦然。
**顺带**：代码里**没有任何事件专门表达「取消」**，前端完全依赖 `agent.failed.payload.reason === 'cancelled'`
这个字符串判断。这本身可以接受（契约已把 `cancelled` 固化为合法值），但 `session.compacted` 那一条是多余且有害的。

**建议**：后端实现时，取消**只**发 `agent.failed { reason: 'cancelled' }`。
`session.compacted` 保留给真正的上下文压缩。Mock 侧建议删掉那一行。

---

### 7. 决策字段名：`next` / `next_action` / `action` —— **三处三个名字**

**代码怎么做**（`runScript()`）：

```js
this.emitEvent('pedagogy.decision', { next: 'Teach', reason: '先确认先验知识' })
```

**文档怎么说**：

| 位置 | 字段名 | 上下文 |
| --- | --- | --- |
| §6.4 策略图状态 | `next_action` | 图内部状态的可序列化字段 |
| §7.1 决策契约（v0.4.1 新增） | `action` | `PedagogicalDecision` —— 「教学动作或信息请求名；决定绑定到哪个 capability」 |
| 代码 / 事件流 | `next` | pedagogy.decision 的 payload |

**影响**：v0.4.1 不但没收敛，还引入了第三个名字。而且 `action` 是 **snake_case**
（`teach` / `update_profile` / `diagnose`），`next` 是 **PascalCase 节点名**（`Teach` / `UpdateProfile`）——
两者**不是同一个命名空间**，不能互相赋值。

**建议**：**以代码为准，事件流统一用 `next`（PascalCase 节点名）**（已固化在 `events.json`）。
若后端要在同一个事件里带上 `action`（v0.4.1 需要它来对应 capability），
请**另开一个字段**而不是复用 `next`，并在契约里写明两套命名的映射关系。
文档侧建议在 §6.4 或 §7.1 补一句说明。

---

### 8. `reconnect()` 路径下 `trace_id` 为 `null`（**未修**）

**代码怎么做**（`reconnect()`）：

```js
reconnect() {
  this.clearTimer()
  this.emitEvent('session.resumed', { resumed_from: this.sessionId })  // traceId 此时是 null
  this.timer = setTimeout(() => {
    this.emitEvent('model.completed', { text: '会话已恢复，可以继续。' })   // null
    this.emitEvent('agent.turn.completed', { status: 'ok' })              // null
    this.traceId = null
  }, 900)
}
```

上一轮结束时 `this.traceId = null`，重连后**没有新开一个 trace**，所以这三条的 `trace_id` 全是 `null`。

**文档怎么说**：§18.2「图 checkpoint 保存教学状态和 Memory 引用，Session 负责交互事实，
**两者通过 session_id/trace_id 关联**」；§12 要求「轨迹可审计」「模型、工具、记忆与策略决策均可关联到同一 trace」。

**影响**：`trace_id: null` 的事件**无法被归入任何一条轨迹**。
做教学复盘（§3.4 研究问题 4）时，恢复会话的这一段是黑盒。
另外 `resumed_from: this.sessionId` 传的是**会话自己**，语义上等于「从我自己恢复」。

**建议**：后端每个 turn 都开新 trace（**包括重连后的恢复轮次**）；
`resumed_from` 若要保留，应传真正的旧 `session_id`，或改名为 `session_id`。

---

### 10. `memory.read.query` 是字符串，§7.1 定义的是 dict（**未修**）

**代码怎么做**：`this.emitEvent('memory.read', { query: 'recent_mistakes' })` —— 字符串。

**文档怎么说**：§7.1 `async def read_memory(self, query: dict, ctx: dict) -> list[dict]` —— **结构化 dict**。
v0.4.1 还新增了 §6.2 的绑定说明：「recall → read_memory，读该学习者的长期学情记忆；
**身份取自调用上下文，检索口径来自绑定**」—— 也就是说查询口径（dimension / concept_id / limit）
应该来自绑定表，而不是一个裸字符串。

**影响**：按 §7.1 实现的后端会发 `{ query: { dimension: 'episodic', concept_id: 'db_normalization', limit: 5 } }`，
前端拿到对象。目前 `App.jsx` 不消费 `memory.read`，所以不炸；但契约层面「同一字段两种类型」，
SDK 生成或类型校验会直接报错。

**建议**：以后端结构化 `dict` 为准（§7.1 是接口设计，Mock 是占位）。
`events.json` 已标注此警告，并新增了建议的 `records` 字段（v0.4.1 §6.4：
读回来的记录若进事件，只保留 `record_id` 与误解标签，不含正文）。

---

### 11. `memory.write` 缺少 §5.5 / §13.2 / §6.2 要求的合规字段（**未修**）

**代码怎么做**：`runScript()` 收尾分支发 `memory.write { dimension: 'episodic', key: 'db_normalization' }`。
**只有两个字段。** 而且它排在最后一条 `node.exited` 之后 —— 跟任何一个 node 都没有从属关系。

**文档怎么说**：

- §5.5：「任何长期记忆写入都必须包含**来源、置信度、过期策略和可撤回标记**」
- §13.2：「长期记忆可查看、可**纠正**、可**删除**、可**关闭**」
- §6.2（v0.4.1 新增，把责任方写明了）：「update_profile → write_memory。
  记录由**节点构造**（来源/置信度/过期策略/可撤回），绑定只声明『写进去』」

**影响**：§5.5 要求的四样（`source` / `confidence` / `ttl` / `revocable`）**一个都没有**，导致：

- 数据组**无法审计**这条记忆是怎么来的；
- **无法显示不确定性**（§13.1「学情诊断必须展示证据和不确定性」）；
- **无法实现「可撤回」**（§13.2）—— 不知道哪些记忆是用户可删的；
- **无法做过期清理**（TTL 缺失）。

**建议**：`events.json` 已把 `source` / `confidence` / `revocable` 标为 `required:true, implemented:false`，
`ttl` 标为可选必补。请刘俊鹏与谢浪在实现 MemoryStore 时一并落地，
并注意 v0.4.1 把「谁构造这四个字段」明确指派给了**节点侧**。

---

### 12. 前端没有 `learner_id` / `request_id`，§18.2 的 SessionRequest 四字段凑不齐（**未修**）

**文档怎么说**：§18.2 交接字段表（v0.4.1 **未改动这一行**）

| 对象 | 必需字段 | 责任接口 |
| --- | --- | --- |
| SessionRequest | `session_id, learner_id, request_id, content` | 前端 → 后端 |

**代码怎么做**：

| 字段 | 状态 |
| --- | --- |
| `session_id` | ✅ 主进程构造（`uid('sess')`），前端拿不到也不需要 |
| `learner_id` | ❌ **全链路不存在**。没有登录、没有本地用户标识、没有配置项 |
| `request_id` | ❌ **不存在**。Mock 用后端生成的 `trace_id` 代替，但前端发请求时还没有 |
| `content` | ✅ `session:send` 的 `text` 参数 |

**影响**：

- 后端按 §18.2 实现后，前端**发不出第一个合法请求**；
- §16.4 交接项「向数据组提交经用户授权的偏好更新」**无法归属** —— 不知道这条偏好是谁的；
- §16.5 验收项「一个学生不能读取另一学生记录」在前端侧**无从谈起**。

**建议**：MVP 阶段定一个最小的 `learner_id` 方案。考虑 §13.2「不主动索取真实姓名、学校、学号等非必要信息」，
建议**本地生成匿名 UUID 并持久化**，而不是让用户填身份。
`request_id` 建议由前端在调 `session:send` 时生成（UUID），随请求一起发。
两者都需要新增一条 IPC（见 `ipc.json` 的 `future_channels.session:connect`）。

---

### 🟡 文档内部自相矛盾（不影响代码，但会让新成员读错）

### 13. 事件公共字段：§5.4 与 §18.2 **仍然**打架（v0.4.1 未修正）

| 字段 | §5.4（v0.4.1） | §18.2（v0.4.1） | 代码 |
| --- | --- | --- | --- |
| `event_id` | ✅ | ✅ | ✅ |
| `session_id` | ✅ | ✅ | ✅ |
| `trace_id` | ✅ | ✅ | ✅ |
| `sequence` | ❌ 缺 | ✅ | ✅ |
| `timestamp` | ✅ | ❌ 缺 | ✅ |
| `type` | ✅ | ✅ | ✅ |
| `payload` | ✅ | ✅ | ✅ |
| `source` | ✅ | ❌ 缺 | ✅ |

**v0.4.1 把这条原样带过来了，只字未改。** 同一份文档里两处对「一个事件有哪些字段」给出不同答案，而且**谁都不完整**。

**现状**：代码产出 **8 个字段**（两处的并集），契约以代码为准并已固化。

**建议**：§5.4 的清单补 `sequence`，§18.2 的 RuntimeEvent 行补 `timestamp` 与 `source`，三处归一。

---

### 14. 教学动作到底有几个？文档里仍有 6 / 7 / 8 三种说法（v0.4.1 未修正）

| 位置 | 数量 | 列出的动作 |
| --- | --- | --- |
| §4.1 分层总览表（v0.4.1） | **6** | Teach、Ask、Hint、Correct、Test、Update Profile（无 Assess、无 Reflect） |
| §6.2 教学决策节点表（v0.4.1） | **8** | Assess、Teach、Ask、Hint、Correct、Test、UpdateProfile、**Reflect** |
| §6.2 动作→能力绑定表（v0.4.1 新增） | **11** | 上述 8 + `end` + `recall` + `diagnose`（后三个是**信息请求名**，不是教学动作） |
| §16.3 孙一新第一轮任务 1 | **7** | Assess→Teach/Ask/Hint/Correct/Test→UpdateProfile（无 Reflect） |
| §16.3 验收 | **6** | 「分支测试覆盖**六类**教学动作」 |
| 代码映射表 | **8** | 8 个节点键全有（含 Reflect） |

**建议**：以 §6.2 的 **8 个节点**为设计全集，以 §6.2 绑定表的 **11 个动作名**为「动作」全集
（其中 `recall` / `diagnose` / `assess` 是信息请求，`end` 是收束），以代码的 **8 个**为当前实现集。
§4.1 与 §16.3 应补齐。`pedagogy-actions.json` 已按这个方式组织。

---

### 15. §9 目录树里**仍然没有** `desktop/` 和 `shared/`（v0.4.1 部分改善）

**§9 的目录树（v0.4.1）** 列出了：`runtime/（core/ capabilities.py testing.py tools/ providers/ memory/ storage/ sandbox/ plugins/）· graph/education/ · skills/ · tools/ · models/ · pet/ · api/ · config/ · tests/ · docs/`

**但 §16.4 张钧翔的模块是**：`desktop/ · pet/ · shared/contracts/`

三处对不上（与 v0.4 时代相同）：

1. **`desktop/` 不在 §9 目录树里** —— 而它是本项目**唯一已经跑起来的代码资产**（Electron + React 桌宠）；
2. **`shared/contracts/` 不在 §9 目录树里** —— 这也解释了为什么这个目录一直不存在：
   任务书把它列为模块，但总目录树从没登记过它；
3. **`pet/` 位置对不上** —— §9 把 `pet/` 放在项目根，实际桌宠前端代码全在 `desktop/src/renderer/src/`。
   根目录下并没有 `pet/`。

**✅ 唯一改善**：v0.4.1 给 §9 的 `pet/` 加了注释「**Live2D / 情感 / 好感度 / 语音**」——
现在能看出它是**服务端宠物能力目录**，与 `desktop/` 的前端渲染不是一回事。这个命名冲突的误导性降低了。

**仍缺**：§9 也没有 `team-site/`（§17.2 的门户交付物）。

**建议**：更新 §9 目录树，加入 `desktop/`（标注为 Electron 桌宠前端）、`shared/contracts/`、`team-site/`。

---

### 16. `Update Profile`（带空格）vs `UpdateProfile`（v0.4.1 未修正）

§4.1 表里**仍然**写作 `Update Profile`（带空格），§6.2、§16.3 和全部代码都写作 `UpdateProfile`（PascalCase）。

事件 payload 里的 `node` 值是前端用来查表的键，**一个空格就查不到**，会直接落到 `'idle'` 兜底
（与第 3 条 Reflect 当初一样的静默失败模式）。当前代码已有 8 个键，
但因为没人会去发 `'Update Profile'`，这条**暂时只是文档问题**。

**建议**：统一为 `UpdateProfile`（已固化在契约中）。§4.1 表应修正。

---

## 四、⚙️ 前端自身缺陷（持续复核）

### 18. ❌ `NODE_TO_EXPRESSION` / `resolveExpression` / `assetUrl` 仍是死代码

`petState.js` 里导出的这三个东西，**全项目没有任何文件 import**（`grep` 确认；`App.jsx` 只 import 了 `resolveLive2D`）。

也就是说：**PNG 模式下的表情映射表 `NODE_TO_EXPRESSION` 从未参与渲染**。
真正驱动 PNG 桌宠的是 `petAnimations.js` 的 `NODE_TO_ANIMATION → ANIMATIONS[].frames`，
由 `RigPet` / `FramePet` 消费。

**后果**：`petState.js` 里那张 `FALLBACK` 表（thinking→idle、correct→sorry 等）和 `NODE_TO_EXPRESSION`
是**读起来像事实标准、实际不生效**的配置。将来有人照着它调表情，改了半天没效果。
而且它**没有 Reflect 键**（第 3 条的残留），这也解释了为什么「补 Reflect」时容易漏掉这张表。

**建议**：二选一 —— 要么在 `App.jsx` 里接上 `resolveExpression`（但那样会与 `petAnimations` 的
8 个动作名打架），要么**删掉这三样**。当前状态（留着但不用）是最容易误导的。
`pedagogy-actions.json` 已把 `expression` 列标注为不生效。

---

### 20. ⚪ 已不适用（见上）—— `scenario` 参数

（保留编号，说明它已被重新判定为「设计如此」，不再列为缺陷。）

---

### 21. ❌ `pet:walk` 通道不属于 Runtime 事件流（维持现状，但需要显式排除）

`win.webContents.send('pet:walk', { walking, dir })` 由主进程的**漫游逻辑**直接发出
（`glideTo()` / `stopMoving()`），不经过 `runtime:event`，也不由后端产生。
它的语义是「窗口正在移动」，不是「Runtime 发生了什么」。

**风险**：如果不在契约里显式排除，后端可能会试图生产 `pet:walk` 事件（因为它看起来像个事件），
或者前端会误以为所有事件都该走 `runtime:event` 而把走路也塞进去 —— 后者会破坏
§4.4「所有动作由 Runtime 接入并产生事件」的边界。

**✅ 契约侧的处置**：`ipc.json` 的 `event_channels.pet:walk` 已明确写「**它不是 Runtime 事件**，
后端不要生产它」。

**❌ 仍存在的代码遗留**：`preload/index.js` 的 `onWalk` 写的是
`const listener = (_e, walking) => handler(walking)`，形参名 `walking` 实际收到的是 `{ walking, dir }` 对象。
`App.jsx` 的 `onWalk` 订阅回调为此写了对象/布尔双兼容分支。功能没问题，但属于历史遗留，建议清理
（把形参改名为 `payload`，删掉 `App.jsx` 里的布尔兼容分支）。

**🆕 新增的一条相关发现**：`win:set-ignore-mouse` 的渲染端调用点 `toggleThrough()` 无论开还是关都传 `true`：

```js
const toggleThrough = async () => {
  const next = !through
  setThrough(next)
  setMenuOpen(false)
  await window.deepprof.win.setIgnoreMouse(true)   // ← 关的时候也传 true
}
```

而且 `toggleThrough` 只能从右键菜单进入，进去就把菜单关掉（`setMenuOpen(false)`）。
所以**「关掉强制穿透」这条路径在界面上走不到** —— 一旦开启强制穿透，就只能重启应用，
或等主进程的 `uiOpen` 判断接管。见下面第 30 条。

---

### 5. 🟠 「好感度交互」：本地闭环 + UI 已补，**缺的只是通道**

**文档怎么说**：

- §16.4 开篇：「桌宠形象集成、情感反馈、**好感度交互**与语音链路」
- §3.2 宠物侧：「**好感度**与主动陪伴」
- §16.4 交接：「向数据组提交**经用户授权的偏好更新**」

**代码怎么做**：`desktop/src/renderer/src/petStats.js` 用 `localStorage`（键 `deepprof.petStats.v1`）
实现了好感度 / 心情 / 精力三个维度，含衰减、互动增益、四个阶段、`consent` 授权开关（**默认关闭**）。
本轮又补上了**授权面板 UI** 与**按阶段变化的摸头台词**（详见第 23 条）。

**问题在哪**：好感度**在前端本地闭环了，但一条通道都没接出去**。

1. **没有后端 → 前端的通道**：后端无法把好感度变化推给桌宠。前端自己算自己的，
   与学情（`LearnerEstimate`）、与教学行为完全没有关联 ——
   摸头加的好感度和「答对了题」加的好感度在系统里无法区分。
2. **没有前端 → 后端的通道**：`exportForDataTeam()` 全项目**没有任何调用方**。
   preload 没有偏好上报通道，§16.4 那句「向数据组提交经用户授权的偏好更新」**仍然没有落地**。
3. **`deepprof.preference.v1` 是一份事实契约**，其形状已在 `events.json` 的
   `proposed_events.profile.preference.update` 中逐字段登记，但**通道不存在**（见第 23 条）。

**影响**：

- 数据组（谢浪 / 欧阳文凯）拿不到任何互动数据，§16.4 的交接项无法履约。
- §13.2「长期记忆可查看、可纠正、可删除、可关闭」在前端侧目前**无法响应后端的删除请求**（没有反向通道）。
- 好感度与教学效果脱钩：§16.4 期待的是「好感度交互」服务于伴学体验，
  而当前实现只对「摸头 / 点击 / 聊天次数」反应，**对学习行为无感知**。

**建议**：MVP 阶段**先把「前端 → 后端」这条通道打通**（一条 IPC + 一个 POST 端点），
让 `exportForDataTeam()` 有地方可去；「后端 → 前端」的推流可以晚一步。
`STAGES` / `GAIN` / `RATE` 三组硬编码参数建议一并纳入版本管理（详见第 23 条）。

---

### 23. 🟠 `petStats.js` 定义了一份对外数据契约，缺的只是通道

`petStats.js` 的 `exportForDataTeam()` 返回：

```js
{ schema: 'deepprof.preference.v1', affinity, mood, energy, stage,
  counts: { pat, tap, chat }, daysSinceBorn, note }
```

**它带 `schema` 字段，说明作者已经把它当作一份需要版本化的对外载荷** —— 这正是契约的定义。

**✅ 本轮已改善的部分**：

1. **schema 已登记进契约**：`events.json` 的 `proposed_events.profile.preference.update` 已把
   `deepprof.preference.v1` 逐字段登记，后端可直接照此收数。位置不对的问题已缓解。
2. **授权 UI 已经做了**：`App.jsx` 的聊天面板底部有「未授权上报 / ⚠ 已授权上报」按钮 + 可展开的
   `consent-box`，能预览导出 JSON、能撤回授权、能清空记录。
3. **好感度已被用户感知**：`persona.js` 的 `LINES.pat` 按四个阶段（stranger/familiar/close/attached）换台词 ——
   这是「好感度系统唯一被用户直接感知到的地方」（该文件注释原话）。

**❌ 仍然缺的**：

1. **`exportForDataTeam()` 依然没有任何调用方** —— 函数写好了、`consent` 校验做了、隐私边界
   （不含对话内容）也声明了，但**没有通道把它发出去**。§16.4 那句「向数据组提交经用户授权的偏好更新」
   **仍然没有落地**。
2. **没有后端 → 前端的通道**：后端无法把好感度变化推给桌宠。
   好感度与学情（`LearnerEstimate`）、与教学行为完全没有关联 ——
   「答对了题加好感」和「摸头加好感」在系统里无法区分。
3. **`STAGES` / `GAIN` / `RATE` 是硬编码的体验参数**，未纳入契约版本管理。
   只要后端将来也参与好感度计算，这三组值就必须双方一致，否则同一句「你答对了」两边算出的增量不同。

**建议**：MVP 阶段**先把「前端 → 后端」这条通道打通**（一条 IPC + 一个 POST 端点）。
`events.json` 已把字段名对齐 `petStats.js`（`affinity` / `mood` / `energy` / `stage` / `reason` 的枚举都取自该文件）。

---

## 五、🆕 v0.4.1 新提出、我们没有实现的部分

> 这一节是本次对齐的**核心产出**。v0.4.1 的 D-7 修订（教学图只产出声明式决策 `PedagogicalDecision`，
> `RuntimePort` 收窄为 `execute + emit`，能力面拆为 `RuntimeHost`，新增 `CapabilityResult` 与绑定表）
> **全部落在后端，桌宠前端零改动需求**。但它同时给**决策事件**加了一批必填字段 ——
> 而这些字段的最终消费者，是做实验复盘的人（§3.4 研究问题 4）和教师评审（§16.7）。

### 24. 🔴 `pedagogy.decision` 缺 v0.4.1 要求的全部新字段（**最高优先级**）

**v0.4.1 §5.4 的原话**：

> `pedagogy.decision` 是实验复盘的主要依据（§3.4 研究问题 4）：它记录节点、动作、
> `reason`、依据（`evidence_sufficient` / `evidence_count` / `attempt_count` /
> `wrong_streak` / `hint_level` / `turn_count` / `max_turns`）以及本轮**实际执行了哪个能力**
> （`capability` / `capability_status`）。因此"策略判了什么"与"执行做成了什么"
> 在同一条轨迹里可分别核对。

**代码怎么做**：`runScript()` 只发两个字段 ——

```js
this.emitEvent('pedagogy.decision', { next: 'Teach', reason: '先确认先验知识' })
```

**缺的字段（11 个）**：

| 字段 | v0.4.1 要求 | 状态 |
| --- | --- | --- |
| `node` | §5.4「记录**节点**、动作、reason」 | ❌ 无（前端只能从紧邻的 `node.entered` 推断） |
| `capability` | 「本轮实际执行了哪个能力」 | ❌ 无 |
| `capability_status` | 同上 | ❌ 无 |
| `evidence_sufficient` | 「依据」字段 | ❌ 无 |
| `evidence_count` | 「依据」字段 | ❌ 无 |
| `attempt_count` | 「依据」字段 | ❌ 无 |
| `wrong_streak` | 「依据」字段 | ❌ 无 |
| `hint_level` | 「依据」字段 | ❌ 无 |
| `turn_count` | 「依据」字段 | ❌ 无 |
| `max_turns` | 「依据」字段 | ❌ 无 |
| `answer_leaked` | 「`hint` / `test` 还额外声明 `answer_leaked`」 | ❌ 无（第 27 条） |

**为什么这是最高优先级**：

1. **它是 §3.4 研究问题 4 的唯一数据源**。没有这批字段，「事件轨迹是否能支持教学过程复盘」
   这个研究问题**在数据上就答不了** —— 轨迹里只有「进了哪个节点」，没有「为什么进」和「做成了没有」。
2. **§5.4 明确说 `capability` / `capability_status` 的存在意义是「可分别核对」** ——
   这是 v0.4.1 把架构改成「图只出决策、Runtime 按绑定表执行」之后，
   唯一能**验证这个改动真的生效了**的手段。没有它，评审时无法证明某个动作真的走了声明的能力。
3. **教师评审会直接看**（§16.7 唐欢容的评审项含「提问、提示、纠错与引用质量」，
   §16.7 还新增了「评审入口（D-7 之后）：教学策略现在集中在两个文件……审这两处即可覆盖策略」）。
4. 前端不消费这些字段（只进事件日志），但**契约里不写死，将来前端就没得展示**。

**建议**：`events.json` 已把这 11 个字段全部登记，标 `implemented:false`，
并给出了 `example_v041_target`（字段齐备后建议的形状）。请刘俊鹏在实现事件总线时一并落地。

---

### 25. 🔴 `CapabilityResult.status` 无事件承载 → 桌宠分不清「装配失败的空回复」与「没话说」

**v0.4.1 §7.3 新增了一条很硬的规定**：

> **装配与绑定失败必须显式**：没有为动作注入绑定、绑定指向未注册能力、绑定引用了请求里不存在的字段、
> 按取值选模板却选不到——这四种情况都返回结构化状态，
> **不允许静默兜底成一段自由发挥的文本**。

并给出了四种状态与处理（`no_binding` / `capability_not_found` / `invalid_request` / `error(template_not_found)`），
以及一条明确的产品取舍：

> 节点侧不覆盖这些失败……失败的学生可见表现因此是"**空回复**"，这是有意的取舍：
> **装配错误是部署缺陷，应当响亮地暴露，而不是用一句安慰话遮住**。

**问题**：这条规定**对桌宠有一个直接且未被处理的后果**。

`CapabilityResult`（`content` / `evidence` / `records` / `status` / `capability` / `metadata`）
是 **`RuntimePort.execute` 的返回值，在「图 ↔ Runtime」之间传递，不走事件流**。
也就是说 **事件流里没有任何字段承载 `status`**。

后果：桌宠收到的是一个 `content` 为空的轮次 —— 它**无法区分**下面两种情况：

| 情况 | 应该的表现 |
| --- | --- |
| 装配失败（`no_binding` / `capability_not_found` / …） | 「响亮地暴露」—— 按 §7.3 的意图，应该是**明确的错误信号** |
| 模型真的没话说（正常的空 content） | 保持沉默，不该报错 |

当前 `App.jsx` 对这两种情况的表现**完全一样**：什么都不说。
这与 §7.3「响亮地暴露」的意图**在表现层是矛盾的**。

**建议**：把 `capability_status` 写进 `pedagogy.decision`（已在第 24 条登记），
或用一条新事件承载 `CapabilityResult` 的摘要（`ipc.json` 的 `future_channels` 里列了一条
建议通道 `capability:status`）。**前端侧需要一次明确的决策**：
装配失败时桌宠应该说「我这边配置有点问题」还是保持沉默？这需要与刘俊鹏、唐欢容一起定。

---

### 26. 🟠 `hint` / `reflect` / `end` 绑定 `render_template`「绝不调模型」，Mock 违反

**v0.4.1 §6.2 的绑定表**明确写了两条：

- `hint` → `render_template`：「**确定性渲染，绝不调用模型**：提示强度是实验自变量，
  不能因换模型而漂移（§3.4）」
- `reflect` / `end` → `render_template`：「换策略建议与收束语都是**可评审的固定文案**」

**Mock 怎么做**：`runScript()` 走 6 个节点，**每个节点都逐字流式吐字**，
而且在整轮开头（进入 Assess 后 500ms）发了**一条 `model.requested`**，
之后 6 段文本全部走 `model.stream.delta`。

**冲突点**：

1. **Hint 段在调模型**（在 Mock 里表现为流式吐字），而绑定表说它绝不调模型；
2. **`pedagogy.decision` 的 capability 与实际执行的能力对不上** —— 这正好是第 24 条
   `capability` / `capability_status` 要解决的问题，但 Mock 连字段都没有；
3. **桌宠的表现会因此矛盾**：Hint 段会显示 `status='streaming'` → `talking=true` → 嘴在动、
   气泡逐字出现。而按绑定表，hint 是**确定性模板渲染**，理想表现应该是**文本直接出现**，
   不带「正在思考」的中间态。

**建议**：

- **后端**：严格遵守绑定表。绑定到 `render_template` 的动作不要发 `model.requested` / `model.stream.delta`。
- **Mock**：若要贴合 v0.4.1，应把 Hint 段改成「一次性发完整文本」，并让 `pedagogy.decision`
  带上 `capability: 'render_template'`。**但这会削弱演示效果**（逐字吐字更好看），
  所以这是一处需要团队定夺的取舍，不是单纯的 bug。
- **契约侧**：`pedagogy-actions.json` 的每个 action 已新增 `v041` 字段（`capability` / `model_call` / `answer_leaked`），
  并在 `v041_bindings_reference` 段完整登记了 §6.2 的绑定表。

---

### 27. 🟠 `answer_leaked` 未实现（教师评审会直接查）

**v0.4.1 §5.4**：「`hint` / `test` 还额外声明 `answer_leaked`，用于核查『**是否过早泄露答案**』。」

这条与两处设计互相印证：

- §6.3 教育能力：Socratic 的职责含「生成递进问题，**避免过早泄露答案**」；
- §7.1 决策契约：`reveal_answer` —— 「是否允许给出最终结论（**提示与追问恒为 False**）」。

**代码怎么做**：全项目无此字段。

**影响**：这是**教师评审会真的去查的一个指标**（§16.7 唐欢容的评审项含「提问、提示、纠错与引用质量」，
§16.7 还要求「审查学情指标解释、实验设计与申报材料」）。
「是否过早泄露答案」直接关系到苏格拉底教学法的有效性论证 —— 如果 hint 总是把答案说了，
整个 Ask/Hint 分支的实验意义就没了。**没有这个字段，就无法从轨迹里核查。**

**建议**：`events.json` 已把 `answer_leaked` 登记为 `pedagogy.decision.payload` 的字段
（`scope_zh` 写明仅 hint 与 test 需要携带），标 `implemented:false`。

---

### 28. 🟠 决策事件不得含学生正文；`params` 不得 emit

**v0.4.1 §5.4 新增的隐私纪律**：

> 隐私纪律：决策事件的载荷里**不得包含学生正文**——学生文本只出现在决策的
> `params` 里（供能力层使用），事件只记长度与依据字段（§6.4、§13.2）。

§7.1 也重复了一遍：`params` —— 「执行所需补充输入。**学生正文只在这里，且不得整体写进事件**」。

**当前状态**：

| 检查项 | 结果 |
| --- | --- |
| `pedagogy.decision` 是否含学生正文 | ✅ **合规** —— Mock 只发 `next` 与 `reason`，`reason` 是固定文案 |
| `params` 是否被 emit | ✅ **合规** —— Mock 根本没有 `params` 字段 |
| **`agent.turn.started.payload.input` 含未脱敏的完整学生输入** | ⚠️ **值得讨论** |

关于第三项：`agent.turn.started` **不是决策事件**，所以严格按 §5.4 的字面规定它不算违规。
而且 §5.1 的 Session 组件职责包含「交互事实」，用户输入本身就是交互事实的一部分。
**但要注意**：`agent.turn.started` 会落盘（`consumer` 含 `storage`），
所以学生正文会以明文进入 Session 轨迹。§13.2 要求「Session 轨迹和学习画像分库存储，
**导出时默认脱敏**」—— 脱敏的责任在数据组（谢浪），不在前端。**这一点需要数据组明确确认**。

**建议**：

1. 后端实现时，**不要把 `PedagogicalDecision.params` 写进事件**。
   若要保留可观测性，建议发一个 `params_length`（字符数）—— 契约已登记为建议字段
   （v0.4.1 只说「事件只记长度」，没给字段名，这是本契约的可执行化建议）。
2. 请谢浪在 EventStore 侧确认「导出时默认脱敏」对 `agent.turn.started.payload.input` 是否生效。

---

### 29. 🟡 动作命名双轨：节点名 PascalCase vs `action` snake_case，文档未给映射

v0.4.1 同时引入了两套动作命名，**文档没有说明二者的对应关系**：

| 命名空间 | 形态 | 出现位置 | 例子 |
| --- | --- | --- | --- |
| 节点名 | PascalCase | `pedagogy.node.entered.payload.node`、§6.2 节点表 | `Assess` / `UpdateProfile` / `Reflect` |
| 动作名 | snake_case | §6.2 绑定表的动作列、§7.1 `PedagogicalDecision.action` | `assess` / `update_profile` / `reflect` / `end` / `recall` / `diagnose` |

而且**两边的集合大小不一样**：动作名有 11 个（8 个教学动作 + `end` / `recall` / `diagnose`），
节点名只有 8 个（§6.2 节点表），`end` / `recall` / `diagnose` **没有对应的节点**。

**影响**：前端按 `node` 查表，只认 8 个 PascalCase 节点名。
如果后端按 §7.1 发 `action: 'update_profile'`，前端查不到；
如果后端发 `action: 'end'`，前端**没有任何节点可以对应**（§6.2 的 Reflect 描述里包含「求助教师」与收束，
可能对应 `end`，但文档没说）。

**建议**：文档在 §6.2 或 §7.1 补一张**节点名 ↔ 动作名对照表**。
`pedagogy-actions.json` 的 `v041_bindings_reference.naming_warning_zh` 已把这件事写清楚，
`events.json` 的 `pedagogy.node.entered.payload.action` 也已登记（标 `implemented:false`）。

---

### 30. 🆕 两条交互路径在界面上不可达（新发现，都是「看起来能点，其实不行」）

#### 30-A：强制穿透**开了关不掉**



**代码怎么做**（`App.jsx`）：

```js
const toggleThrough = async () => {
  const next = !through
  setThrough(next)
  setMenuOpen(false)
  await window.deepprof.win.setIgnoreMouse(true)   // ← 无论 next 是 true 还是 false，都传 true
}
```

**问题**：函数意图是「开关强制穿透」，但传给主进程的**永远是 `true`**。
`through` 这个 state 只在渲染端翻转（决定菜单文案显示「点击穿透」还是「恢复鼠标操作」），
主进程的 `forceThrough` 标记**只进不出**。

而且 `toggleThrough` 只能从右键菜单进入，进去就 `setMenuOpen(false)` ——
**用户没有任何机会再点第二次**。所以：

- 菜单文案会显示「恢复鼠标操作」（暗示可以关掉），
- 但**关不掉**。一旦开启强制穿透，只能重启应用，或等主进程的 `uiOpen` 判断在面板/菜单打开时临时接管。

**修复**（一行）：

```js
await window.deepprof.win.setIgnoreMouse(next)   // 传 next，不是 true
```

⚠️ 注意这一行曾被有意写成 `true`，旧注释的理由是「关掉强制穿透时回到自动模式：先设为穿透，
随后由 mousemove 判定」—— 那是**旧的转发式穿透方案**下的逻辑。现在穿透已改由主进程
`startThroughWatch()` 轮询决策（见 `ipc.json` 的 `window_behavior.click_through`），
这个理由已经失效，正确做法就是传 `next`。

**建议**：前端修这一行。本次未改代码（约束：只碰 `shared/contracts/` 与文档）。

#### 30-B：漫游关闭时，右键菜单的「随便走走」点了没反应

**代码怎么做**：

```js
// main/index.js
function wanderOnce() {
  if (!win || win.isDestroyed() || !roaming) return;   // ← roaming 为 false 时直接返回
  ...
}
// App.jsx 的菜单项
<button onClick={() => { setMenuOpen(false); window.deepprof.win.wander() }}>随便走走</button>
```

主进程的 `roaming` 默认为 **false**，而「随便走走」这条菜单项**照常显示、照常可点** ——
点了之后 `wanderOnce()` 直接 return，**小人一动不动，界面上没有任何反馈**。

**影响**：用户会认为「走路功能坏了」。
实际的正确操作是先点菜单里的「让它自己走」（打开漫游），再点「随便走走」——
但界面上没有任何提示告诉你这个依赖关系。

**建议**（三选一，前端定）：

1. 菜单里「随便走走」在漫游关闭时**置灰**（`disabled`）；
2. 或者点它时**自动打开漫游**（更符合直觉：用户明确表达了"想让它走"）；
3. 或者主进程的 `wanderOnce()` 去掉 `!roaming` 这个条件 ——
   因为「用户手动点一次」和「定时器自动触发」是两种不同的意图，前者不该受漫游开关约束。

方案 3 最贴合语义（`win:wander` 是用户直接指令，不是自动行为），
但要注意 `roaming` 还承担着「停止移动」的职责，改之前先确认 `stopMoving()` 的调用点。

> ⚠️ 这两条（30-A / 30-B）都属于同一类问题：**界面上存在的交互入口，其实际行为与它的文案/可见性不符**。
> 它们不像静默失败那样难查（用户能察觉到"没反应"），但因为没有报错，
> 排查时容易误判成「动画坏了」或「穿透坏了」，而不是「入口本身不通」。

---

### 31. 🆕 `cancelled` 复用 `sad`（难过）表情，与 `error` 视觉重复

**代码怎么做**（`petAnimations.js`）：

```js
STATUS_TO_ANIMATION = {
  loading: 'think', error: 'sad', cancelled: 'sad', reconnecting: 'think'
}
```

`error` 与 `cancelled` **映射到同一个动画**，而 `ANIMATIONS.sad` 用的是 `expressions/sad.png`。
也就是说：用户**主动点取消**，桌宠表现出的情绪和**真的出错了**一模一样。

**为什么这是个问题**：

- 语义上，取消是**用户自己的决定**，不该被表现成像出了故障。用户点了取消、
  看到小人一脸难过，会以为自己把程序弄坏了（这是最常见的误读）。
- §13.1 教育伦理要求表现要**准确、不误导**。把「你按了取消」渲染成「我很难过」，
  是一种轻微的情感操控（guilt-tripping），在同类娱乐桌宠里很常见，
  但用在教育产品上、且要过教师评审，不合适。
- `persona.js` 的台词已经修对了（cancelled 组是「行，那先停这儿。」「好，不讲了。想继续随时说。」
  这种平静口吻，与 error 组的「啧，掉线了。」明显不同）—— **只有表情没跟上**。
  也就是说这是**一处台词与表情不同步**的遗留。

**这一条从 v0.4 时代的清单里就记着，两轮复核都没改。**

**建议**：`cancelled` 不要用 `sad`。两个选项：

| 方案 | 动画 | 理由 |
| --- | --- | --- |
| 映射到 `idle` | `idle` | 最省事，语义中性「回到待机」。但用户点了取消后没有任何反馈，可能以为没点中 |
| 新增一个 `calm` 动画 | 新素材 | 最准确。`petState.js` 的 `FALLBACK` 表当初就预留过独立表情的位置 |

考虑到「必须有反馈但反馈不该是负面情绪」，**推荐映射到 `idle`**（`STATUS_TO_ANIMATION` 里
去掉 `cancelled` 这一项即可，`resolveAnimation` 会自动回落到教学节点或 `idle`）——
代价是取消瞬间视觉变化不明显，而气泡里的台词（「行，那先停这儿。」）已经提供了足够的反馈。

> ⚠️ 注意 `ANIMATIONS.sad` 同时被 `error` 使用，所以**不能改 `sad` 本身**，只能改 `cancelled` 的映射。

---

## 六、冻结建议（给刘俊鹏与孙一新）

1. **本契约以代码为准冻结**（每个文件的 `source_of_truth` 字段写明了这一点）。
2. 后端实现时，**必须满足 `events.json` 的 `consumer_checklist_zh`**（已扩充到 16 条，含 v0.4.1 新增项）。
3. **优先级排序**（按「卡住别人的程度」排）：

   | 优先级 | 条目 | 卡住谁 |
   | --- | --- | --- |
   | P0 | **第 24 条**：`pedagogy.decision` 补 capability / capability_status / 依据字段 | §3.4 研究问题 4、§16.7 教师评审 |
   | P0 | **第 25 条**：`CapabilityResult.status` 需要事件承载（否则桌宠分不清装配失败与没话说） | §7.3 的「响亮地暴露」意图 |
   | P0 | **第 12 条**：`learner_id` / `request_id` | 前端发不出第一个合法请求 |
   | P1 | **第 2 / 10 / 11 条**：delta 累计语义、memory.read 结构化、memory.write 合规四字段 | 数据组合规、事件回放 |
   | P1 | **第 6 / 8 条**：取消不要用 session.compacted、重连要开新 trace | 状态机与轨迹质量 |
   | P2 | **第 4 / 5 / 23 条**：情感标签、好感度通道 | §16.4 交接项 |
   | P2 | **第 26 / 27 条**：hint 不调模型、answer_leaked | 教学实验的有效性论证 |
   | P2 | **第 30 / 31 条**：两条界面不可达的交互路径、`cancelled` 用难过表情 | 演示与评审的观感（都是前端小改） |
   | P2 | **第 3 条残留**：`RigPet` 缺 `case 'correct'`、`NODE_TO_LIVE2D` 缺 `Reflect` | 分层模式与 Live2D 模式的表现完整性 |

4. **第 5 条（好感度）需要在 MVP 范围内做一次决策**：是只保留前端本地的互动玩法，
   还是要让它接入教学效果（答对题加好感、学情影响心情）。选后者则必须打通
   `profile.preference.update` 通道，并让后端与前端在 `STAGES` / `GAIN` / `RATE` 三组参数上取得一致。
   **UI 与 schema 登记都已经做好了，只差一条通道。**
5. **第 13~16、29 条是纯文档问题**，建议由文档维护者统一修订，不要等到联调时才发现大家看的不是同一份说明。
6. **v0.4.1 的架构改动对前端零需求** —— 这一轮前端不需要为 D-7 做任何事。
   但**契约层需要跟着动**：`pedagogy.decision` 的字段清单是本轮唯一必须双方对齐的接口变化。
7. **本清单需要持续维护**：本次复核期间（约 3 小时）`desktop/` 下有 6 个文件被改动，
   其中 `main/index.js` 与 `App.jsx` 的改动**直接推翻了初版 6 条结论**（`sequence`、Reflect、
   node.exited 顺序、重连显示、Live2D 切换、Teach/Ask/Hint 区分）。
   建议每次前端有涉及数据形状的改动后回写本目录，否则契约层会在几周内重新变成「只存在于代码里」。

---

## 附录：本次复核对契约文件的修订清单

| 文件 | 版本 | 主要修订 |
| --- | --- | --- |
| `events.json` | 1.0.0 → **1.1.0** | `sequence` 改 implemented；`pedagogy.decision` 补 11 个 v0.4.1 字段 + 建议形状；新增 `pedagogical_decision_contract` 段（RuntimePort / RuntimeHost / PedagogicalDecision / CapabilityResult / 四种失败状态）；`typical_sequences` 全面重写（6 节点流程）；`consumer_checklist` 扩到 16 条；新增 `design_baseline` 与 `changelog_zh` |
| `ipc.json` | 1.0.0 → **1.1.0** | 通道数 11 → **16**（补登 5 条）；`win:set-ignore-mouse` 描述改写（改为标记式）；`win:drag-state` 修正（记忆式恢复）；`win:set-chat-open` 几何修正（220×**420**）；`win:set-roaming` 不一致标注为已修；新增 `window_behavior` 段（穿透轮询 / 关窗口=隐藏 / 托盘第一项=退出 / 快捷键）；`future_channels` 更新（tts 已存在、新增 capability:status 建议） |
| `pedagogy-actions.json` | 1.0.0 → **1.1.0** | Reflect 改 implemented；Live2D 状态由「未启用」改为「已生效（有残留）」；每个 action 新增 `v041` 字段；新增 `v041_bindings_reference` 段（§6.2 绑定表全表 + 模板块 + memo + 命名警告） |
| `INCONSISTENCIES.md` | v1 → **v2** | 本文件。逐条复核原 23 项的状态；新增第 24~31 条；补充「本次复核对契约文件的修订清单」 |
| `README.md` | 1.0.0 → **1.1.0** | 对齐 v0.4.1；更新文件清单与计数；新增「v0.4.1 带来了什么变化」一节 |
