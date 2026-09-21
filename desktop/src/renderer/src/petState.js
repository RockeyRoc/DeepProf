/**
 * 教学节点 / 运行状态 → 桌宠表情 的映射
 *
 * 这是任务书第二条「把教学动作映射为可解释的表现」的落地位置。
 * 每个表情都对应一个明确的教学含义，不是随便换图。
 */

/*
 * ⚠️ 2026-09-19 清理：这里原先还 import 了 `assets/pet/` 根目录下 4 张 642×1024 的旧图
 *    （`idle` / `happy` / `quiz` / `sorry`.png），并配了一张「表情名 → 素材地址」的
 *    `ASSETS` 表和 `assetUrl()`。那 4 张图**已移出交付目录**，
 *    所以这段 import + `ASSETS` + `assetUrl()`
 *    本次一并删除 —— 它们全项目无人 import，属于死代码
 *    （见 `shared/contracts/INCONSISTENCIES.md` 第 18 条）。
 *
 *    ⚠️ 千万别顺手把 `assets/pet/idle.png`（根目录那个同名文件）也当旧图删掉：
 *       它是**在用的活素材** —— `RigPet.jsx` 用它做点击命中掩码，`rig/rig.json` 的
 *       `source` 也指向它，`_素材工作区/拆腿.py` 还按这个路径读源图。
 *       它是 642×1024 的**当前角色**立绘，只是和 §3.1 那批 1536×1536 的表情
 *       **不是同一次生成批次**而已。
 */

/**
 * 兜底表：素材还没做的表情，先用已有的顶替。
 * 正式素材齐了之后删掉这个常量。
 */
const FALLBACK = {
  thinking: 'idle',
  teach: 'idle',
  ask: 'idle',
  hint: 'idle',
  correct: 'sorry',
  error: 'sorry'
}

/** 教学节点（DESIGNv0.4 §6.2）→ 表情 */
export const NODE_TO_EXPRESSION = {
  Assess: 'thinking',
  Teach: 'teach',
  Ask: 'ask',
  Hint: 'hint',
  Correct: 'correct',
  Test: 'quiz',
  UpdateProfile: 'happy'
}

/** 运行状态 → 表情（优先级高于教学节点） */
export const STATUS_TO_EXPRESSION = {
  loading: 'thinking',
  error: 'error',
  cancelled: 'sorry',
  reconnecting: 'thinking'
}

/**
 * 解析最终要显示的表情名。
 * 优先级：运行状态 > 教学节点 > idle
 */
export function resolveExpression(status, node) {
  let name = STATUS_TO_EXPRESSION[status] || NODE_TO_EXPRESSION[node] || 'idle'
  if (FALLBACK[name]) name = FALLBACK[name]
  return name
}

/*
 * ⚠️ 原先这里还有一个 `assetUrl(expression)`（表情名 → 图片地址），
 *    它依赖上面已删除的 `ASSETS` 表 —— 随旧图一起删了。
 *    所以下面这张 `NODE_TO_EXPRESSION` / `resolveExpression` 现在**只产出名字、
 *    不再解析素材地址**；整块依旧是**死代码**，只是保持原样以免大面积改文档。
 *    PNG 桌宠实际由 `petAnimations.js` 的 `NODE_TO_ANIMATION → ANIMATIONS[].frames`
 *    驱动，改这里不会有任何画面效果。
 */

/* =========================================================================
 * Live2D 映射
 *
 * 表情序号 0-7 对应模型里的 f00~f07（见 haru_greeter_t03.model3.json）。
 * ⚠️ 下面这组对应关系是**先按语义猜的**，还没逐个看过实际效果。
 *    等你在界面上切一遍，把不对的改掉就行 —— 只改这张表，别的代码不用动。
 * ========================================================================= */

/** 教学节点 → Live2D 动作 + 表情 */
export const NODE_TO_LIVE2D = {
  Assess: { motion: 'Idle', motionIndex: 0, expression: 2 }, // 思考
  Teach: { motion: 'Idle', motionIndex: 1, expression: 0 }, // 讲解
  Ask: { motion: 'Idle', motionIndex: 2, expression: 3 }, // 提问
  Hint: { motion: 'Tap', motionIndex: 1, expression: 4 }, // 提示
  Correct: { motion: 'Tap', motionIndex: 1, expression: 5 }, // 纠错
  Test: { motion: 'Tap', motionIndex: 0, expression: 6 }, // 测验
  UpdateProfile: { motion: 'Tap', motionIndex: 0, expression: 1 } // 高兴
}

/** 运行状态 → Live2D 动作 + 表情（优先级高于教学节点） */
export const STATUS_TO_LIVE2D = {
  loading: { motion: 'Idle', motionIndex: 2, expression: 2 },
  error: { motion: 'Tap', motionIndex: 0, expression: 7 },
  cancelled: { motion: 'Idle', motionIndex: 0, expression: 4 },
  reconnecting: { motion: 'Idle', motionIndex: 2, expression: 2 }
}

/** 解析出当前该播的动作与表情 */
export function resolveLive2D(status, node) {
  return (
    STATUS_TO_LIVE2D[status] ||
    NODE_TO_LIVE2D[node] || { motion: 'Idle', motionIndex: 0, expression: 0 }
  )
}

/** 每个状态在界面上显示的中文说明 */
export const STATUS_LABEL = {
  idle: '待机',
  loading: '加载中',
  streaming: '回复中',
  error: '出错了',
  cancelled: '已取消',
  reconnecting: '重连中'
}
