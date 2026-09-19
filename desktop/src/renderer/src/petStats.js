/**
 * 桌宠状态 —— 好感度 / 心情 / 精力
 *
 * ═══ 为什么要有这个 ═══
 *
 * DESIGNv0.4 §16.4 张钧翔的任务书开头写的是：
 *   「桌宠形象集成、情感反馈、**好感度交互**与语音链路。」
 * 「好感度交互」是任务书明写的，之前一行都没做。
 *
 * ═══ 交互设计参考了什么 ═══
 *
 * 参考了网上同类桌宠（DeepSeek 娘桌宠、老妹桌宠、DyberPet、月薪喵等）的通行做法：
 *   · 摸头加好感、有专属台词
 *   · 多维状态（心情/精力）随时间自然衰减
 *   · 待机久了情绪低落、会困
 *   · 好感度分阶段
 *
 * ⚠️ **只借鉴设计思路，不涉及任何第三方素材、形象或代码。**
 *    交互模式（点击有反应、状态衰减）属于通用设计，不受版权保护；
 *    受保护的是具体的美术形象和代码表达。这两个我们 100% 是团队原创。
 *
 * ═══ 关于「经用户授权」═══
 *
 * 任务书的交接要求原文是：「向数据组提交**经用户授权的**偏好更新」。
 * 所以这里有一个 **`consent` 开关，默认【关闭】**。
 * 用户不开，这个文件里的任何数据一个字节都不会离开本机。
 * 这不是装饰 —— 是我们对「经用户授权」这四个字的落实。
 */

const KEY = 'deepprof.petStats.v1'

/** 衰减/恢复速率。数值都是「每小时变化多少」，方便换算和讲解 */
const RATE = {
  moodDecayPerHour: 6, // 心情自然回落
  energyDecayPerHour: 9, // 精力消耗（醒着就掉）
  energyRecoverPerHour: 26, // 睡觉时恢复
  affinityDecayPerHour: 0.4, // 好感度几乎不掉，但冷落太久会慢慢降（最多降到 0）
  // ⚠️ 离线衰减要【封顶得狠一点】。
  //    原来按 12 小时算：精力 9/小时 × 12 = 扣 108，直接归零 ——
  //    实测用户重启后看到的就是"精力条是 0"，像坏了一样。
  //    改成 2 小时：精力最多扣 18，回来还剩 67，是个正常状态。
  //    （"离线期间会掉状态"这个设计意图还在，只是不会掉到死。）
  offlineCapHours: 2
}

/** 一次互动带来的变化 */
const GAIN = {
  pat: { affinity: 1, mood: 4, energy: -1 }, // 摸头
  tap: { affinity: 0.3, mood: 1.5, energy: -0.5 }, // 普通点击
  chat: { affinity: 2, mood: 3, energy: -2 } // 完成一轮对话
}

/** 好感度阶段 —— 不同阶段台词和表现不一样 */
export const STAGES = [
  { min: 0, key: 'stranger', label: '刚认识', note: '还比较客气' },
  { min: 20, key: 'familiar', label: '熟悉', note: '会主动搭话了' },
  { min: 50, key: 'close', label: '亲近', note: '会撒娇、会闹脾气' },
  { min: 80, key: 'attached', label: '依赖', note: '你一走开就低落' }
]

export function stageOf(affinity) {
  let s = STAGES[0]
  for (const x of STAGES) if (affinity >= x.min) s = x
  return s
}

const clamp = (v, lo = 0, hi = 100) => Math.max(lo, Math.min(hi, v))

function fresh() {
  return {
    affinity: 4, // 一开始不是 0 —— 从 0 开始的话"刚认识"阶段太长，看不到变化
    mood: 72,
    energy: 85,
    lastSeen: Date.now(),
    asleep: false,
    consent: false, // ⚠️ 默认关闭：未经授权不上报任何东西
    counts: { pat: 0, tap: 0, chat: 0 },
    born: Date.now()
  }
}

export function load() {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return fresh()
    const s = { ...fresh(), ...JSON.parse(raw) }
    s.counts = { ...fresh().counts, ...(s.counts || {}) }

    // ⚠️ 兼容旧存档：早期版本的离线衰减封顶是 12 小时（9/小时 × 12 = 扣 108），
    //    会把精力+心情直接扣到 0 —— 用户重启后看到"精力条是 0"，像坏了一样。
    //    规则已经改成封顶 2 小时，但**已经存进去的那个 0 改不回来**，
    //    所以加载时发现归零的旧档，直接当新档处理。
    if (s.energy <= 1) return fresh()
    return s
  } catch {
    return fresh() // 存储坏了不该让桌宠起不来
  }
}

export function save(s) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s))
  } catch {
    /* 隐私模式/配额满：静默忽略，不影响使用 */
  }
}

/**
 * 按流逝的时间衰减。
 *
 * 每 tick 调用一次（几十秒一次就够），不要每帧调 —— 没必要。
 * 离线时段一并结算，但**封顶 RATE.offlineCapHours**：掉到死的话，用户重启回来会以为坏了。
 */
export function tick(s, now = Date.now()) {
  const hours = Math.min((now - s.lastSeen) / 3600000, RATE.offlineCapHours)
  if (hours <= 0) return s

  const next = { ...s, lastSeen: now }
  if (s.asleep) {
    next.energy = clamp(s.energy + RATE.energyRecoverPerHour * hours)
    next.mood = clamp(s.mood + RATE.moodDecayPerHour * 0.3 * hours) // 睡饱了心情回升一点
    if (next.energy >= 96) next.asleep = false // 睡饱自己醒
  } else {
    next.mood = clamp(s.mood - RATE.moodDecayPerHour * hours)
    next.energy = clamp(s.energy - RATE.energyDecayPerHour * hours)
    next.affinity = clamp(s.affinity - RATE.affinityDecayPerHour * hours)
  }
  return next
}

/** 记一次互动 */
export function interact(s, kind) {
  const g = GAIN[kind]
  if (!g) return s
  return {
    ...s,
    affinity: clamp(s.affinity + (g.affinity || 0)),
    mood: clamp(s.mood + (g.mood || 0)),
    energy: clamp(s.energy + (g.energy || 0)),
    counts: { ...s.counts, [kind]: (s.counts[kind] || 0) + 1 }
  }
}

/** 精力太低就自己睡了 —— 这是"待机入睡"的触发条件 */
export function maybeSleep(s) {
  if (!s.asleep && s.energy <= 12) return { ...s, asleep: true }
  return s
}

/**
 * 交给数据组的偏好摘要。
 *
 * ⚠️ 只有 `consent === true` 时才应该调用它并把结果发出去。
 *    调用方（App.jsx）负责检查授权 —— 这里不做隐式放行。
 *    里面**不含任何对话内容**，只有互动次数和状态值。
 */
export function exportForDataTeam(s) {
  if (!s.consent) return null
  return {
    schema: 'deepprof.preference.v1',
    affinity: Math.round(s.affinity * 10) / 10,
    mood: Math.round(s.mood),
    energy: Math.round(s.energy),
    stage: stageOf(s.affinity).key,
    counts: { ...s.counts },
    daysSinceBorn: Math.floor((Date.now() - s.born) / 86400000),
    note: '仅互动统计，不含对话内容；由用户显式授权后导出'
  }
}

export function reset() {
  const f = fresh()
  save(f)
  return f
}
