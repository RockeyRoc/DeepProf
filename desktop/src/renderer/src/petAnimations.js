/**
 * 桌宠动画配置 —— 教学节点 → 表情/动画
 *
 * ═══ 素材现状（2026-09-19）═══
 *
 * 第①批【9 张表情立绘】已到位，在 `assets/pet/expressions/`：
 *   统一 **1536×1536** 画布、脚底对齐、头心对齐、角色高 **1200px**（实测 1201，差 1px）。
 *   由 `_素材工作区\动作帧\` 的流水线处理产出，别手动改这些 PNG。
 *   ⚠️ 这里**曾错记成「768×768 / 角色高 620px」，别再写回去** ——
 *      渲染端按 `geometry.json` 的 1536 算缩放、贴图却只有 768 的话，
 *      角色会**恒为一半大**：不报错、不崩溃、只是"看着不对"。
 *      实测 `expressions/*.png` 现在**全部 1536×1536**，与 geometry 已对齐。
 *
 * 素材进度（2026-09-19）：
 *   ✅ 走路 4 帧 —— **已接进本文件**（见下面 `walk`，带自己的 `geo`）。
 *   ✅ 眨眼 2 帧 / 说话 2 帧 —— **已产出**（`frames/blink/`、`frames/talk/`），
 *      但**还没接**：本文件没有 `blink` 条目，`talk` 仍指向 `teach.png` 单图。
 *      📌 记录见 `ASSETS_LICENSE.md` §3.5（该节明说"记录的是素材产出，不代表已在桌宠里播放"）。
 *   ⏸ 待机 2 帧（`idle_0` / `idle_1`）—— **仍未产出**，所以 `idle` 还是单帧。
 *   单帧时靠 FramePet 的 sway 形变滤镜 + 弹跳补一点"活着"的感觉。
 *   帧到位后把 frames 数组填长即可，其他代码一行都不用改。
 *
 * ⚠️ `assets/pet/` 根目录现在**只剩 `idle.png`**（642×1024），它**不是**旧素材：
 *    那是**当前角色**的立绘，且是**在用**的活素材 —— `RigPet.jsx` 拿它做点击命中掩码、
 *    `rig/rig.json` 的 `source` 指向它。**别删**。
 *    （原先同目录还有 happy/quiz/sorry 三张**与它逐字节相同**的重复图，
 *      纯旧文件，已于 2026-09-19 移出交付目录，见 `ASSETS_LICENSE.md` §八 第 3 条。）
 */

// ── 9 张表情立绘 ──
import idleUrl from './assets/pet/expressions/idle.png'
import thinkUrl from './assets/pet/expressions/think.png'
import teachUrl from './assets/pet/expressions/teach.png'
import askUrl from './assets/pet/expressions/ask.png'
import hintUrl from './assets/pet/expressions/hint.png'
import correctUrl from './assets/pet/expressions/correct.png'
import quizUrl from './assets/pet/expressions/quiz.png'
import happyUrl from './assets/pet/expressions/happy.png'
import sadUrl from './assets/pet/expressions/sad.png'
import geometry from './assets/pet/expressions/geometry.json'
// 走路 4 帧（侧视朝右，8fps）。归一化时用的同一套规格，脚底和表情那批对齐。
import walkGeo from './assets/pet/frames/walk/geometry.json'
import walk0Url from './assets/pet/frames/walk/walk_0.png'
import walk1Url from './assets/pet/frames/walk/walk_1.png'
import walk2Url from './assets/pet/frames/walk/walk_2.png'
import walk3Url from './assets/pet/frames/walk/walk_3.png'
// 眨眼 2 帧 / 说话 2 帧（2026-09-19 出图；与表情批共用同一份 geometry）
import blink0Url from './assets/pet/frames/blink/blink_0.png'
import blink1Url from './assets/pet/frames/blink/blink_1.png'
import talk0Url from './assets/pet/frames/talk/talk_0.png'
import talk1Url from './assets/pet/frames/talk/talk_1.png'

/**
 * 角色在画布里的外接框（由 `_素材工作区` 的归一化脚本产出，别手改）。
 * 渲染端用它来定锚点和缩放 —— 直接按整张画布缩放会让角色白白小掉四成，
 * 因为画布是方的（1536×1536）而桌宠窗口是窄高的（220×300），会变成宽度受限。
 */
export const EXPRESSION_GEOMETRY = geometry
export const WALK_GEOMETRY = walkGeo

export const ANIMATIONS = {
  // ---- 待机：帧没出，先用 idle 单图 + 形变滤镜做呼吸 ----
  idle: { frames: [idleUrl], fps: 1.5, loop: true, sway: false },

  // ---- 走路：真的逐帧播了（2026-09-19 出图）----
  // ⚠️ 必须带自己的 geo：走路的角色框比表情那批【宽】（尾巴伸出去），
  //    共用一份会让缩放算错。两批的脚底和头心是对齐的，所以高度一致。
  walk: {
    frames: [walk0Url, walk1Url, walk2Url, walk3Url],
    // ⚠️ 从 8 降到 6：8fps 下一个循环只有 0.5 秒，配上走路帧本身的
    //    高度起伏，看起来像在"一胀一缩地赶路"。6fps 稳一些。
    fps: 6,
    loop: true,
    sway: false,
    geo: walkGeo
  },

  // ---- 说话：嘴部 2 帧开合（2026-09-19 接入）----
  // 6fps：一开一合约 0.33 秒，接近正常说话的语速。
  // 这两帧与原表情批共用同一份 geometry（脚底/头心对齐），所以不用单独指定 geo。
  talk: { frames: [talk0Url, talk1Url], fps: 6, loop: true, sway: false },

  // ---- 眨眼：不是独立动作，是**叠在待机上**的一条 overlay ----
  // 播放方式见 App.jsx：随机间隔触发，每次把 trigger 加一就重播一遍。
  // blink_0 是睁眼（与 idle 同一张图，天然无缝），blink_1 是闭眼。
  // holds 是「每帧停多久（毫秒）」——闭眼必须比睁眼短得多，
  // 等间隔播放的话会变成"闭着眼发呆"，不是眨眼。
  blink: {
    frames: [blink0Url, blink1Url],
    holds: [90, 110],
    loop: false,
    sway: false
  },

  // ---- 教学节点，一个节点一张对应的表情 ----
  teach: { frames: [teachUrl], fps: 6, loop: true, sway: false },
  ask: { frames: [askUrl], fps: 4, loop: true, sway: false },
  hint: { frames: [hintUrl], fps: 6, loop: true, sway: false },
  correct: { frames: [correctUrl], fps: 4, loop: true, sway: false },
  quiz: { frames: [quizUrl], fps: 1.5, loop: true, sway: false },
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
  streaming: 'talk', // 流式输出中 = 正在说话 → 嘴部 2 帧开合
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
  if (emotion && !NODE_TO_ANIMATION[node]) {
    console.warn(
      `[桌宠] 不认识的情感标签 "${emotion}"（教学节点 "${node || '无'}"）——` +
        '已退回待机。若这是后端新增的取值，请补进 petAnimations.js 的 EMOTION_TO_ANIMATION。'
    )
  }

  return NODE_TO_ANIMATION[node] || 'idle'
}
