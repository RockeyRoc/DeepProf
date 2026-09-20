/**
 * 桌宠动画配置 —— 教学节点 → 表情/动画
 *
 * ═══ 素材现状（2026-09-20 · 换角色后，晚场补齐最后三张表情）═══
 *
 * 角色换了：从**上一版原创角色**（深蓝双马尾 + 浅蓝开衫）换成**官方 Q 版新角色**
 * （白发 + 蓝蝴蝶结 + 水手风蓝白裙）。**换的只是素材，渲染代码一行没动。**
 * 上一个角色的素材全部移到了 `_素材工作区\_旧素材归档\`，**没有删**。
 *
 * 【9 张 Q 版表情立绘】已到位，在 `assets/pet/expressions/`：
 *   统一 **1536×1536** 画布、脚底对齐、头心对齐、角色高 **1200px**。
 *   由 `_素材工作区\动作帧\` 的流水线处理产出，别手动改这些 PNG。
 *   ⚠️ 这里**曾错记成「768×768 / 角色高 620px」，别再写回去** ——
 *      渲染端按 `geometry.json` 的 1536 算缩放、贴图却只有 768 的话，
 *      角色会**恒为一半大**：不报错、不崩溃、只是"看着不对"。
 *
 * ✅ 走路 4 帧 —— **2026-09-20 解禁**（组长给了新角色的侧视四格图）。
 * ✅ 眨眼 2 帧 —— **2026-09-20 解禁**（组长给了站姿的睁眼/闭眼两张）。
 * ✅ 待机基准 —— **2026-09-20 晚换成「站姿闭嘴」**（`frames/blink/blink_0.png` 换了内容，
 *    路径没变）。换之前那张是「睁眼张嘴」，小人待机时嘴一直张着。
 * ✅ 表情立绘 —— **2026-09-20 晚补齐 hint / correct / quiz，9 张、0 复用**。
 * ⏸ 说话 —— **仍停用，但停用的理由变了**（这条极容易踩，务必读完再动）：
 *    · ❌ 旧理由"没有同姿势的闭嘴图" **已不成立** —— 组长 2026-09-20 晚把闭嘴图给了
 *    · ✅ 现理由：`STATUS_TO_ANIMATION` 的优先级**高于**教学节点与情感标签，
 *      而 status 在**整轮对话期间一直停在 `streaming`**
 *      （`pedagogy.node.entered` 不重置 status；见 `App.jsx` 的 `setStatus` 调用点）。
 *      → 一旦放开 `streaming: 'talk'`，**整个演示期间桌宠都停在说话帧，
 *        9 个教学表情一个都看不到**，等于把"教学节点切表情"这条验收项废掉。
 *      要接得先解决优先级（给 status 分级、或改成 overlay 短播），那是产品决策。
 *    · 旧的帧还在 `frames/talk/` 下（画的是**上个角色**），只是不再 import。
 * ⏸ 待机 2 帧（`idle_0` / `idle_1`）—— 仍是单帧。
 *    第四批**给了两张**，但**用不了**：角色高只有 932px，而归一化目标高是 1200px
 *    → 要放大 1.29× 会糊（站姿批的基准图角色高 1386px，是缩小的、锐利）。
 *    另：那两张彼此像素差 **27.5%**，是同一姿势的**两次独立生成**，不是一呼一吸的微差，
 *    直接当两帧轮播会全身"重画"。要真待机帧得请组长**按角色高 ≥1024px 重出**。
 *    现在靠 FramePet 的 sway 形变滤镜 + 弹跳补一点"活着"的感觉。
 *
 * ⚠️ `assets/pet/idle.png`（642×1024）**仍是上一个角色**，但它现在**没有出口**：
 *    唯一用它的 RigPet 已在右键菜单里 disable（见 App.jsx 那段注释）。
 *    接回分层走路时，要连它和 `rig/` 三层一起换掉。**别只删它** ——
 *    `hitTest` 与 `rig.json` 的 source 都指着这个路径。
 */

/* ── 表情立绘：现在是 **9 张**（2026-09-20 晚补齐最后三张）──
 *
 * 除待机外有 8 张教学表情立绘：think / teach / ask / hint / correct / quiz / happy / sad
 * （待机不是这 9 张里的，它走**站姿**基准 —— 见下面 `ANIMATIONS.idle`）
 *
 * ⚠️ 版本沿革，别记混：
 *   · 上个角色那批是 9 张逐帧立绘 —— 随角色更换**已归档**，不是现在这套
 *   · 官方 Q 版**第一批只给了 6 个姿态**（think/teach/ask/happy/sad + 一张漂浮 idle），
 *     当时 hint / correct / quiz 三张没有 → 那三个槽位是**复用**最接近的一张顶着
 *   · **2026-09-20 晚组长补了一张三格表情图（Hint / Correct / Quiz 并排），
 *     三个槽位现在各有专属立绘，复用全部消除（0 复用）**
 *     拆图用 `desktop/tools/素材流水线/三格表情拆分.py`（去底部英文标签牌 + 去头旁的语义气泡）
 *
 * 归一化产物在 `_素材工作区/动作帧/归一化/表情/`，由 `归一化帧.py 表情` 产出。
 * ⚠️ 那个脚本的 `install()` 会把结果拷到 `assets/pet/frames/<动作>/`，
 *    而表情批实际读 `expressions/` —— **别用 `--接入`，手动拷**。
 *    这个不一致本轮没改脚本（怕动到别的动作），先记在这。
 */
import thinkUrl from './assets/pet/expressions/think.png'
import teachUrl from './assets/pet/expressions/teach.png'
import askUrl from './assets/pet/expressions/ask.png'
import hintUrl from './assets/pet/expressions/hint.png'
import correctUrl from './assets/pet/expressions/correct.png'
import quizUrl from './assets/pet/expressions/quiz.png'
import happyUrl from './assets/pet/expressions/happy.png'
import sadUrl from './assets/pet/expressions/sad.png'
import geometry from './assets/pet/expressions/geometry.json'
// ⚠️ `expressions/idle.png`（漂浮坐姿那张）**不再被 import 了** —— 它唯一的出口是
//    `ANIMATIONS.talk`，而 talk 已改成指向站姿基准（见下面 talk 那段）。
//    **文件没删**：`拆腿.py` / `预览rig.py` 还从磁盘读它（`DEFAULT_SRC` 指的就是这个路径），
//    只是不进构建产物了。别顺手把它删掉。
/* =========================================================================
 * ⏸ 三批旧帧里，只剩【说话】还停用（走路 / 眨眼已用新角色素材解禁）
 *
 * 背景：`frames/walk|blink|talk/` 这三批原本画的都是**上一个角色**
 * （深蓝双马尾 + 开衫）。换角色后一并停用 —— 两套并存的话，小人
 * **一站定是新角色、一走/一眨眼/一说话就变回旧角色**，比不做还糟。
 *
 * 后来组长按新角色补了走路四格图和站姿眨眼两张，**walk / blink 已经重新接上**
 * （见下面各自的 import 与 ANIMATIONS 里的 frames）。
 * **talk 没有再启用，但原因跟素材无关** —— 是优先级问题，
 * 完整理由见文件头「⏸ 说话」和 `ANIMATIONS.talk` 那两处，接之前务必读。
 * 旧的说话帧还在 `frames/talk/` 下（画的是上个角色），没有删，也不进构建产物。
 *
 * ⚠️ walk 必须带自己的 geo —— 走路那批角色框比表情批【宽】（侧视伸出去），
 *    共用一份会让缩放算错。
 * ========================================================================= */
// ✅ 走路 4 帧 —— 2026-09-20 解禁：组长给了新角色的四格图
//    （侧视朝右、纯白底、四格一张；帧间高度差只有 0.5%，比例很稳）
//    走的是 裁四格.py → 归一化帧.py walk --模式 逐帧 → 预览.py 这条流水线
import walkGeo from './assets/pet/frames/walk/geometry.json'
import walk0Url from './assets/pet/frames/walk/walk_0.png'
import walk1Url from './assets/pet/frames/walk/walk_1.png'
import walk2Url from './assets/pet/frames/walk/walk_2.png'
import walk3Url from './assets/pet/frames/walk/walk_3.png'
// ✅ 眨眼 2 帧 —— 2026-09-20 解禁。走的是同一批站姿图（睁眼 / 闭眼）。
//    ⚠️ blink_1（闭眼）**不是**组长那张原生闭眼图，而是拿 blink_0 改眼睛合成出来的
//    —— 两张原图是【两次独立生成】，全身 21.5% 像素不同、脚底还差 9px，
//    整图硬切会全身抖。合成后 blink_1 的身体像素与 blink_0 完全一致，抖动为零。
//    合成脚本：`desktop/tools/素材流水线/眨眼合成.py`（随交付走，可重跑）。
import blinkGeo from './assets/pet/frames/blink/geometry.json'
import blink0Url from './assets/pet/frames/blink/blink_0.png'
import blink1Url from './assets/pet/frames/blink/blink_1.png'
// ⏸ 说话仍停用 —— ⚠️ 理由**不是"缺素材"**（闭嘴图 2026-09-20 晚已到手），
//    是**优先级问题**：放开它会把整轮的教学表情全顶掉。
//    完整理由见文件头「⏸ 说话」与下面 `ANIMATIONS.talk` 两处，**别照着旧说法放开它**。
// import talk0Url from './assets/pet/frames/talk/talk_0.png'
// import talk1Url from './assets/pet/frames/talk/talk_1.png'

/**
 * 角色在画布里的外接框（由 `_素材工作区` 的归一化脚本产出，别手改）。
 * 渲染端用它来定锚点和缩放 —— 直接按整张画布缩放会让角色白白小掉四成，
 * 因为画布是方的（1536×1536）而桌宠窗口是窄高的（220×300），会变成宽度受限。
 */
export const EXPRESSION_GEOMETRY = geometry
export const WALK_GEOMETRY = walkGeo
export const BLINK_GEOMETRY = blinkGeo

export const ANIMATIONS = {
  // ---- 待机：⚠️ 用【站姿】眨眼批的第 0 帧 ----
  //    为什么不用 expressions/idle.png：那张是**漂浮/坐姿**，而眨眼批是**站姿**
  //    （轮廓宽高比 0.77 vs 0.92）。idle 若还是漂浮的，眨眼瞬间小人会
  //    从"坐着"跳到"站着" —— 那不是眨眼，是变身。
  //    所以 idle 与 blink_0 **必须是同一张图**（这条 petAnimations 自己的接回
  //    说明里就写了：「眨眼和说话的第一帧要跟表情批的 idle 同一张图才无缝」）。
  //    现在两张是同一个文件，天然无缝。
  //    ⚠️ 2026-09-20 晚这张图**换成了「睁眼闭嘴」**（原来那张是「睁眼张嘴」，
  //    小人待机时嘴一直张着）。路径没变，所以这里一行都不用改。
  //    ⚠️ 代价：8 张教学表情仍是漂浮姿势，切表情时会在两种姿势间跳 —— 这在本轮
  //    之前就存在（立绘本来就各是不同姿势），不是这次引入的。反倒是本轮新加的
  //    hint / correct / quiz 三张也是**站姿**，跟待机一致，比原来那批更协调。
  idle: { frames: [blink0Url], fps: 1.5, loop: true, sway: false, geo: blinkGeo },

  // ---- 走路：✅ 2026-09-20 解禁（组长给了新角色的侧视四格）----
  // ⚠️ 必须带自己的 geo：走路那批角色框比表情批【宽】（侧视时身体/头发前后伸出去），
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

  // ---- 说话：⏸ 停用中 —— **理由已经换了，别照着旧理由放开它** ----
  //
  // ❌ 旧说法"没有同姿势的闭嘴图" **2026-09-20 晚起已不成立**：那张图拿到了
  //    （现在就是 idle 基准，同姿势、轮廓 IoU 0.991）。
  // ✅ 真正的原因：`STATUS_TO_ANIMATION` 的优先级**高于**教学节点与情感标签，
  //    而 status 在**整轮对话期间一直停在 `streaming`** —— `pedagogy.node.entered`
  //    只设节点、不重置 status。所以放开 `streaming: 'talk'` 的后果是
  //    **整个演示期间桌宠都停在说话帧，9 个教学表情全被顶掉**。
  //
  // 这里的 frames **故意只放一张基准**：没有路径能到达它，放什么都不会被播到，
  // 但**绝不能放 `expressions/idle.png`**（那是漂浮坐姿）—— 万一哪天有人只把
  // `STATUS_TO_ANIMATION` 那一行取消注释，就会立刻看到"坐着 ↔ 站着"的姿势跳。
  // 指向站姿基准至少是同一个姿势，坏得没那么突然。
  //
  // ═══ 真要接的话，两件事都得先做 ═══
  //   1. **素材**：还得有「张嘴」那一帧。素材是有的（上一版基准「睁眼张嘴」，
  //      归档在 `_素材工作区/动作帧/原始/blink/_上一版_张嘴基准/`），
  //      但**不能直接整图当第二帧** —— 它和闭嘴图是两次独立生成，全身像素差 9.3%，
  //      硬切会全身抖。要照 `眨眼合成.py` 那套**只移植嘴部区域**，
  //      合成出身体像素与基准完全一致的张嘴帧。
  //   2. **优先级**：给 status 分级、或把 talk 改成 overlay 短播（同眨眼的机制）。
  //      这是产品决策，没定之前不要动 `STATUS_TO_ANIMATION`。
  talk: { frames: [blink0Url], fps: 6, loop: true, sway: false },

  // ---- 眨眼：✅ 2026-09-20 解禁（站姿睁眼/闭眼两张）----
  // 帧序**必须是 [睁眼, 闭眼]**：FramePet 会拿 frames[0] 和当前贴图比对，
  // 相等就**跳过第 1 帧**直接从闭眼起播（省掉"重播一遍已经显示着的图"）。
  // 所以 idle 与 blink_0 用同一个 import 是硬要求，不是巧合。
  // holds[0] 因此平时用不到（只在没跳过时兜底）；真正起作用的是 holds[1] = 闭眼 110ms。
  // 眨眼节奏由 App.jsx 的 blinkTimer 3~7 秒随机触发（不等长停留，避免机械感）。
  blink: { frames: [blink0Url, blink1Url], holds: [90, 110], loop: false, sway: false },

  /* ---- 教学节点 ----
   *
   * **2026-09-20 晚起 9 个槽位各有专属立绘，0 复用。**
   * 之前有 3 个是复用顶着的（hint/correct/quiz 借 think/teach），补齐后不用再借了。
   *
   * Mock 走的教学节点顺序（也是演示时肉眼能看到的顺序）：
   *     Assess → Teach → Ask → Hint → Correct → UpdateProfile
   *
   *    Assess   → think   睁眼拿书      = 评估、琢磨
   *    Teach    → teach   讲解
   *    Ask      → ask     邀你来答
   *    Hint     → hint    食指点唇      = "给你个提示"
   *    Correct  → correct 闭眼笑举手    = "答对了"（表扬，不是纠错那层意思）
   *    Test     → quiz    拿书提问
   *    Reflect  → think   ↑ 唯一还借着的：Reflect 没有专属立绘，借"琢磨"
   *
   * ⚠️ 历史教训（补齐之前踩的，留着免得再犯）：hint 一开始被指到 ask，结果
   *    **Ask→Hint 表情一模一样**，看着像"桌宠没反应"。所以**别让演示顺序里
   *    相邻的两个撞车**。现在只有 Reflect 是借的，而 Reflect **不在 Mock 顺序里**，
   *    所以相邻对全部不同。
   *
   * ⚠️ 新加的三张是**站姿**，而 think/teach/ask/happy/sad 还是**漂浮坐姿** ——
   *    从待机（站姿）切到 Hint/Correct/Quiz 是顺的，切到 think/teach/ask 会有一次
   *    姿势跳。这是老问题（立绘本来就各是不同姿势），本轮没有加剧。
   */
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
  // streaming: 'talk',  ⏸ 停用中 —— ⚠️ **理由已经不是"没素材"了，别照着旧理由放开**
  //
  // 2026-09-20 白天停用它的理由是"说话帧画的是上个角色"；当晚素材补齐后又换了新理由，
  // 现在是**优先级问题**，而且这个理由**不会因为素材到位而消失**：
  //
  //   · STATUS 的优先级高于情感标签与教学节点（见下面 resolveAnimation）
  //   · 而 status 在**整轮对话期间一直是 `streaming`** ——
  //     `pedagogy.node.entered` 只设节点、不重置 status，
  //     要等整轮 6 个节点走完的 `model.completed` 才回到 idle
  //   · 所以留着这一条 = **整个演示期间桌宠都停在说话帧**，
  //     9 个教学表情（含新补的 hint/correct/quiz）**一个都看不到**，
  //     "教学节点切表情"这条验收项直接作废
  //
  // 删掉之后，流式期间自然落到情感标签/教学节点，
  // 表现是"表情正常、只是嘴不动" —— 这正是现在想要的。
  // 要真接说话，得先解决优先级（给 status 分级 / 改成 overlay 短播），那是产品决策。
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
