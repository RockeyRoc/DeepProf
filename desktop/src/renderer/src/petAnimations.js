/**
 * 桌宠动画配置 —— 教学节点 → 表情/动画
 *
 * ═══ 素材现状（2026-09-20 · 换角色后）═══
 *
 * 角色换了：从**上一版原创角色**（深蓝双马尾 + 浅蓝开衫）换成**官方 Q 版新角色**
 * （白发 + 蓝蝴蝶结 + 水手风蓝白裙）。**换的只是素材，渲染代码一行没动。**
 * 上一个角色的素材全部移到了 `_素材工作区\_旧素材归档\`，**没有删**。
 *
 * 第①批【6 张 Q 版表情立绘】已到位，在 `assets/pet/expressions/`：
 *   统一 **1536×1536** 画布、脚底对齐、头心对齐、角色高 **1200px**。
 *   由 `_素材工作区\动作帧\` 的流水线处理产出，别手动改这些 PNG。
 *   ⚠️ 这里**曾错记成「768×768 / 角色高 620px」，别再写回去** ——
 *      渲染端按 `geometry.json` 的 1536 算缩放、贴图却只有 768 的话，
 *      角色会**恒为一半大**：不报错、不崩溃、只是"看着不对"。
 *
 * ⏸ 走路 / 眨眼 / 说话 —— **三批旧帧已停用**（画的是上一个角色，会串味）。
 *    文件还在 `frames/` 下，只是不再 import；接回步骤见文件中部那段注释。
 * ⏸ 待机 2 帧（`idle_0` / `idle_1`）—— 仍未产出，`idle` 还是单帧。
 *    单帧时靠 FramePet 的 sway 形变滤镜 + 弹跳补一点"活着"的感觉。
 *
 * ⚠️ `assets/pet/idle.png`（642×1024）**仍是上一个角色**，但它现在**没有出口**：
 *    唯一用它的 RigPet 已在右键菜单里 disable（见 App.jsx 那段注释）。
 *    接回分层走路时，要连它和 `rig/` 三层一起换掉。**别只删它** ——
 *    `hitTest` 与 `rig.json` 的 source 都指着这个路径。
 */

/* ── 表情立绘：现在是 **6 张**（2026-09-20 换成官方 Q 版角色）──
 *
 * ⚠️ 上个角色那批是 **9 张**（idle/think/teach/ask/hint/correct/quiz/happy/sad），
 *    组长这回给的 Q 版只有 **6 个姿态**，没有 hint / correct / quiz 三张。
 *    所以那三个槽位**复用最接近的一张**（见下面 ANIMATIONS 的注释与映射理由），
 *    不是漏了。要补齐得让组长按 `动作帧/_新角色出图prompt.md` 再出三张。
 *
 * 归一化产物在 `_素材工作区/动作帧/归一化/表情/`，由 `归一化帧.py 表情 --接入` 产出。
 * ⚠️ 那个脚本把结果拷到 `assets/pet/frames/<动作>/`，而表情批实际读 `expressions/`
 *    —— 拷完要手动挪过来。这个不一致本轮没改脚本（怕动到别的动作），先记在这。
 */
import idleUrl from './assets/pet/expressions/idle.png'
import thinkUrl from './assets/pet/expressions/think.png'
import teachUrl from './assets/pet/expressions/teach.png'
import askUrl from './assets/pet/expressions/ask.png'
import happyUrl from './assets/pet/expressions/happy.png'
import sadUrl from './assets/pet/expressions/sad.png'
import geometry from './assets/pet/expressions/geometry.json'
/* =========================================================================
 * ⏸ 走路 / 眨眼 / 说话 —— 三批旧帧【已停用】，2026-09-20
 *
 * 为什么停用：这三批帧（`frames/walk|blink|talk/`）画的是**上一个角色**
 * （深蓝双马尾 + 开衫）。而表情批已经换成官方 Q 版新角色
 * （白发 + 蓝蝴蝶结 + 水手风）。两套并存的话，小人**一站定是新角色、
 * 一走/一眨眼/一说话就变回旧角色** —— 比不做还糟。
 *
 * 文件**没有删**，还在 `assets/pet/frames/` 下，`_几何.json` 也在。
 * 只是不再 import，所以也不会进构建产物。
 *
 * ═══ 新帧到手后怎么接回来 ═══
 *
 *   1. 把新帧按 `_素材工作区/动作帧/README.md` 跑归一化：
 *        python 归一化帧.py walk --接入
 *      （blink / talk 同理；眨眼和说话的第一帧要跟表情批的 idle **同一张图**才无缝）
 *   2. 恢复下面这几行 import（路径不变）
 *   3. 把 ANIMATIONS 里 walk / talk / blink 的 frames 填回去，
 *      并把 walk 的 geo 恢复成 `frames/walk/geometry.json`
 *   4. STATUS_TO_ANIMATION 里恢复 `streaming: 'talk'`
 *
 * ⚠️ walk 必须带自己的 geo —— 走路那批角色框比表情批【宽】（侧视伸出去），
 *    共用一份会让缩放算错。
 * ========================================================================= */
// import walkGeo from './assets/pet/frames/walk/geometry.json'
// import walk0Url from './assets/pet/frames/walk/walk_0.png'
// import walk1Url from './assets/pet/frames/walk/walk_1.png'
// import walk2Url from './assets/pet/frames/walk/walk_2.png'
// import walk3Url from './assets/pet/frames/walk/walk_3.png'
// import blink0Url from './assets/pet/frames/blink/blink_0.png'
// import blink1Url from './assets/pet/frames/blink/blink_1.png'
// import talk0Url from './assets/pet/frames/talk/talk_0.png'
// import talk1Url from './assets/pet/frames/talk/talk_1.png'

/**
 * 角色在画布里的外接框（由 `_素材工作区` 的归一化脚本产出，别手改）。
 * 渲染端用它来定锚点和缩放 —— 直接按整张画布缩放会让角色白白小掉四成，
 * 因为画布是方的（1536×1536）而桌宠窗口是窄高的（220×300），会变成宽度受限。
 */
export const EXPRESSION_GEOMETRY = geometry
// WALK_GEOMETRY 已随走路帧一并停用（原先导出但全项目无人引用）。接回走路时恢复：
//   import walkGeo from './assets/pet/frames/walk/geometry.json'
//   export const WALK_GEOMETRY = walkGeo

export const ANIMATIONS = {
  // ---- 待机：帧没出，先用 idle 单图 + 形变滤镜做呼吸 ----
  idle: { frames: [idleUrl], fps: 1.5, loop: true, sway: false },

  // ---- 走路：⏸ 已停用（旧帧是上个角色）----
  // 指向 idle = 漫游时**不换姿势**，用当前表情站在原地。
  // ⚠️ 漫游位移本身还在（托盘里的「自动溜达」，默认关）。演示时别开它 ——
  //    开了小人是"站着滑"，因为走路帧停了。要真的走起来，等新角色的侧视 4 帧。
  walk: { frames: [idleUrl], fps: 6, loop: true, sway: false },

  // ---- 说话：⏸ 已停用（旧帧是上个角色）----
  // 注意：真正让「说话」不再抢占表情的是 STATUS_TO_ANIMATION 里删掉了 streaming 那一条
  // （见文件末尾）。所以流式期间会正常显示当前教学表情，只是嘴不动。
  talk: { frames: [idleUrl], fps: 6, loop: true, sway: false },

  // ---- 眨眼：⏸ 已停用（旧帧是上个角色）----
  // 这里仍然保留 overlay 的结构（App.jsx 靠 ANIMATIONS.blink.frames 触发），
  // 但两帧都是 idle，所以"眨"了等于没眨 —— 不会露出旧角色的脸。
  // 接回新帧时把 holds 恢复成 [90, 110]（闭眼必须比睁眼短，否则是"闭着眼发呆"）。
  blink: { frames: [idleUrl], holds: [90], loop: false, sway: false },

  /* ---- 教学节点 ----
   *
   * 新角色只有 6 个姿态，代码里却有 9 个教学槽位 —— 所以有 3 个是**复用**的，
   * 怎么分都免不了重复。关键是**别让演示顺序里相邻的两个撞车**。
   *
   * Mock 走的教学节点顺序（也是演示时肉眼能看到的顺序）：
   *     Assess → Teach → Ask → Hint → Correct → UpdateProfile
   *
   * 按这个顺序排查相邻对：
   *   Assess(think) vs Teach(teach)      ✓ 不同
   *   Teach(teach) vs Ask(ask)           ✓ 不同
   *   Ask(ask)     vs Hint(?)            ← 必须避开 ask
   *   Hint(?)      vs Correct(teach)     ← 必须避开 teach
   *   Correct(teach) vs UpdateProfile(happy) ✓ 不同
   *
   * ⚠️ hint 一开始被指到 ask，结果 **Ask→Hint 表情一模一样**，看着像"桌宠没反应"。
   *    改成 think 之后相邻对全部不同。重复仍然存在（think 同时供 Assess/Hint/Test），
   *    但**不在演示顺序上挨着**，不影响观感。
   *
   *    Assess   → think  捧书点下巴 = 评估、琢磨
   *    Teach    → teach  捧书张嘴   = 讲解
   *    Ask      → ask    挥手眯眼   = 邀你来答
   *    Hint     → think  ↑ 复用。语义上是"琢磨该给你什么提示"
   *    Correct  → teach  ↑ 复用。纠错本来就是一种讲解
   *    Test     → think  ↑ 复用（不在 Mock 顺序里）
   *    Reflect  → think  ↑ 复用（不在 Mock 顺序里）
   *
   * 补齐缺失的三张（hint / correct / test）后，把这里换成各自的 frames 即可。
   * 出图 prompt 见 `_素材工作区/动作帧/_新角色出图prompt.md` §四。
   */
  teach: { frames: [teachUrl], fps: 6, loop: true, sway: false },
  ask: { frames: [askUrl], fps: 4, loop: true, sway: false },
  hint: { frames: [thinkUrl], fps: 6, loop: true, sway: false },
  correct: { frames: [teachUrl], fps: 4, loop: true, sway: false },
  quiz: { frames: [thinkUrl], fps: 1.5, loop: true, sway: false },
  happy: { frames: [happyUrl], fps: 8, loop: false, sway: false },

  // ---- 运行状态用的 ----
  think: { frames: [thinkUrl], fps: 1.5, loop: true, sway: false },
  sad: { frames: [sadUrl], fps: 1.5, loop: true, sway: false },

  // ---- 点击反应：帧没出，先用高兴的表情 ----
  tap: { frames: [happyUrl], fps: 12, loop: false, sway: false }
}

/**
 * 教学节点 → 动作名（对应 DESIGNv0.4 §6.2 的教学动作）
 *
 * ⚠️ 两个坑，改的时候注意：
 *
 * 1. **`Correct` 以前映射到 `sad`** —— 因为当时没有 correct 的素材，
 *    就借用了"难过"。但「**你答错了**」（老师视角，要温和鼓励）和
 *    「**我掉线了**」（自身视角，要泄气）是两种完全不同的情绪。
 *    现在有专门的 `correct.png` 了，别再借。
 *
 * 2. **`Reflect` 以前没有映射** —— §6.2 定义了它，§16.3 的验收还明确要求
 *    「Reflect 回退」，也就是后端**一定会发**这个节点。没有映射时
 *    resolveAnimation 的 `|| 'idle'` 兜底会让它**静默退回待机**：
 *    不报错、不崩溃、没有任何表现，联调时极难发现。
 */
export const NODE_TO_ANIMATION = {
  Assess: 'think',
  Teach: 'teach',
  Ask: 'ask',
  Hint: 'hint',
  Correct: 'correct',
  Test: 'quiz',
  UpdateProfile: 'happy',
  Reflect: 'think' // 回退到评估式思考；必须有，否则静默消失
}

/**
 * 节点名 → 动作名，**两套写法都认**。
 *
 * ═══ 为什么不能直接 `NODE_TO_ANIMATION[node]` ═══
 *
 * 后端同时存在两套命名，而且**事件里发的是哪一套，Mock 和真后端不一样**：
 *
 *   | 来源 | 发什么 | 出处 |
 *   | --- | --- | --- |
 *   | Mock（前端自演） | `'Assess'`、`'Teach'` —— PascalCase | `main/index.js` 的 emitEvent |
 *   | 真后端（graph） | `'assess'`、`'hint'`、`'update_profile'` —— 全小写 | `graph/education/nodes/*.py` 的 `NODE = "hint"`、`policies/__init__.py` 的 `ACTION_HINT = "hint"` |
 *
 * 上面这张表的键是照 Mock 写的。只认它的话，**真后端发来的节点名一个都匹配不上**，
 * 全部落进 `|| 'idle'` —— 又是那种"不报错、不崩溃、只是表情不对"的静默失败。
 *
 * `UpdateProfile` ↔ `update_profile` 这组尤其要注意：不只是大小写，
 * 下划线也有无之别，所以归一化要**连下划线和连字符一起吃掉**再做比较。
 *
 * ⚠️ 两套命名并存本身是待确认项（已记进 shared/contracts，需要和后端定一套），
 *    这里只是让前端在定论之前不会静默失效。
 */
export function animationForNode(node) {
  if (!node) return null
  const key = String(node).trim()
  if (NODE_TO_ANIMATION[key]) return NODE_TO_ANIMATION[key]

  const norm = key.toLowerCase().replace(/[_\-\s]/g, '')
  for (const [name, anim] of Object.entries(NODE_TO_ANIMATION)) {
    if (name.toLowerCase() === norm) return anim
  }
  return null
}

/**
 * 情感标签 → 动作名（**优先级高于教学节点**）
 *
 * ═══ 为什么需要这张表 ═══
 *
 * 后端**已经在发情感标签了**，而且这是 §16.3 明文交接的"三件套"之一：
 *   `graph/education/state.py` 写着「action / response_text / emotion / citations 交前端」
 *   `api/routes/sessions.py` 把它们打进事件 payload。
 *
 * 但三方用的是**三套不同的词汇**，直接读 emotion 会大面积落空：
 *
 *   | 教学动作 | 本文件 NODE_TO_ANIMATION | graph 实际发（EMOTION_BY_ACTION） | pet/emotion.py 设计值 |
 *   | Teach    | teach    | explaining  | explaining  |
 *   | Ask      | ask      | **curious** | thinking    |
 *   | Hint     | hint     | encouraging | encouraging |
 *   | Correct  | correct  | **patient** | concerned   |
 *   | Test     | quiz     | **attentive** | strict    |
 *   | Reflect  | think    | **supportive** | thinking |
 *   | Assess   | think    | （不发）    | thinking    |
 *   | UpdateProfile | happy | （不发）   | neutral     |
 *   | （结束）  | —        | warm        | —           |
 *
 * 本表按 **graph 实际发的值**（左列加粗那几个是它独有的）做映射，
 * 同时把 `pet/emotion.py` 的设计词表一并收进来，**两套都认** ——
 * 将来后端改成哪一套都不用再动前端。
 *
 * ⚠️ 语义对齐时注意：`Correct` 拿到的是 `patient`（耐心），不是"难过"。
 *    这和 NODE_TO_ANIMATION 里那条历史注释是同一个坑。
 */
export const EMOTION_TO_ANIMATION = {
  // —— graph 的 EMOTION_BY_ACTION 实际取值 ——
  explaining: 'teach', // Teach
  curious: 'ask', // Ask
  encouraging: 'hint', // Hint
  patient: 'correct', // Correct —— 是"耐心"，不是 sad
  attentive: 'quiz', // Test
  supportive: 'think', // Reflect
  warm: 'happy', // End / 收尾

  // —— pet/emotion.py 的设计词表（后端若改用这套，这里已经认） ——
  thinking: 'think',
  neutral: 'idle',
  concerned: 'correct',
  strict: 'quiz',
  happy: 'happy'
}

/** 后端发来的情感标签 → 动作名；不认识返回 null（由调用方决定怎么处置） */
export function animationForEmotion(emotion) {
  if (!emotion) return null
  return EMOTION_TO_ANIMATION[String(emotion).trim().toLowerCase()] || null
}

/**
 * 运行状态 → 动作名（优先级高于情感标签与教学节点）
 *
 * ⚠️ `streaming` 这条是 2026-09-19 补的。在此之前**没有这一条**，
 *    而 `ANIMATIONS.talk`（嘴部 2 帧）虽然早就填好了 frames，却**没有任何路径会切到它** ——
 *    流式期间播的一直是教学节点那张静态表情（teach.png），嘴部开合只有呼吸滤镜在模拟。
 *    "填了 frames 没人切" 是这套映射表最容易漏的一环：改了 ANIMATIONS 不等于改了表现，
 *    还要确认 resolveAnimation 真的能返回这个键。
 */
export const STATUS_TO_ANIMATION = {
  loading: 'think',
  // streaming: 'talk',  ⏸ 2026-09-20 停用 —— 说话帧是上个角色的，接上去会让小人
  //   在流式输出期间变回旧形象。**删掉这一条**而不是把 talk 指向 idle，是因为
  //   STATUS 的优先级高于情感标签与教学节点：留着它，流式期间就会被 idle 顶掉，
  //   连当前的教学表情都显示不出来。删掉之后流式期间自然落到情感标签/节点，
  //   表现是"表情正常、只是嘴不动" —— 正是停用期间想要的效果。
  error: 'sad',
  cancelled: 'sad',
  reconnecting: 'think'
}

/**
 * 解析当前该播哪个动画
 *
 * 优先级：走路 > 运行状态 > 情感标签 > 教学节点 > idle
 *
 * 为什么「运行状态」压过「情感标签」：状态说的是**当下正在发生什么**
 * （加载中 / 报错 / 重连中），这时候不该被一个来自上一轮的情绪覆盖掉。
 * 而情感标签比教学节点更细，所以它排在节点前面。
 *
 * @param {string} pose     'walk' | 'idle'
 * @param {string} status   运行状态（loading / error / …）
 * @param {string} node     教学节点（PascalCase）
 * @param {string} [emotion] 后端发来的情感标签（见 EMOTION_TO_ANIMATION）
 */
export function resolveAnimation(pose, status, node, emotion) {
  // 走路优先（漫游时）
  if (pose === 'walk') return 'walk'
  if (STATUS_TO_ANIMATION[status]) return STATUS_TO_ANIMATION[status]

  const byEmotion = animationForEmotion(emotion)
  if (byEmotion) return byEmotion

  // ⚠️ 不认识的情感标签**要说出来**，不能静默吞掉。
  //    后端换词表、或新增教学动作时会走到这里 —— 静默退回 idle 的话，
  //    现象是"桌宠对这个动作毫无反应"，和"这个动作本来就没表现"完全分不清，
  //    联调时极难发现（NODE_TO_ANIMATION 上面那条历史注释记的就是同一个坑）。
  const byNode = animationForNode(node)
  if (emotion && !byNode) {
    console.warn(
      `[桌宠] 不认识的情感标签 "${emotion}"（教学节点 "${node || '无'}"）——` +
        '已退回待机。若这是后端新增的取值，请补进 petAnimations.js 的 EMOTION_TO_ANIMATION。'
    )
  }

  return byNode || 'idle'
}
