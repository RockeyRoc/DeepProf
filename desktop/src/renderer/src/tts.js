/**
 * TTS —— 语音合成（任务书 §16.4 第三条：接入 TTS 与可中断播放）
 *
 * ═══ 为什么用 Web Speech API，而不是 Edge-TTS / Whisper ═══
 *
 * 项目最终选型是 Edge-TTS（合成）+ Whisper（识别），但那两个都要后端服务。
 * 任务书的验收项是「**流式文本与语音可停止**」——这是一个**前端行为**，
 * 不该等后端就绪才能演示。
 *
 * Chromium 内置的 Web Speech API 正好满足：
 *   · 零依赖、零密钥、零网络（走系统语音）
 *   · cancel() 天然支持中断 —— "可中断播放"直接就有了
 *   · 渲染进程不持有任何模型密钥（验收项之一，天然满足）
 *
 * 后端链路就绪后，把下面的 window.speechSynthesis 换成 preload 暴露的 IPC 调用即可，
 * 上层（App.jsx）一行都不用改 —— 接口形状是照着"将来要换"设计的。
 *
 * ═══ 两个 Chromium 的坑，都绕过去了 ═══
 *
 * 1. getVoices() 第一次调用经常返回**空数组** —— 语音列表是异步填充的，
 *    必须监听 voiceschanged。这里做了 Promise 缓存 + 超时兜底。
 * 2. **长文本会被静默截断**（大约 15 秒后就没声了，也不报错）。
 *    所以要按句子切块排队。切块顺带让"停止"更跟手 —— 不用等一整段念完。
 */

const synth = typeof window !== 'undefined' ? window.speechSynthesis : null

/**
 * 打断计数。每次 stop() 都会 +1。
 *
 * ⚠️ 这是"可中断"能正确工作的关键：cancel() 之后，被取消的那些 utterance
 * 仍然会回调 onend / onerror（Chromium 行为）。没有这个计数，
 * 旧回调会把 UI 的"正在朗读"状态错误地改掉 —— 表现就是停止之后按钮卡在
 * "停止朗读"上，或者念完旧的一段把新一段的状态带跑。
 */
let generation = 0
let voicesPromise = null

export function ttsSupported() {
  return !!(synth && typeof window.SpeechSynthesisUtterance === 'function')
}

/** 语音列表是异步填充的，缓存成 Promise 避免每次 speak 都等 */
function loadVoices() {
  if (voicesPromise) return voicesPromise
  voicesPromise = new Promise((resolve) => {
    if (!ttsSupported()) return resolve([])

    const done = () => resolve(synth.getVoices() || [])
    if ((synth.getVoices() || []).length) return done()

    let settled = false
    const once = () => {
      if (settled) return
      settled = true
      done()
    }
    synth.addEventListener?.('voiceschanged', once, { once: true })
    // 兜底：某些环境（含部分 Electron 版本）不触发 voiceschanged
    setTimeout(once, 1000)
  })
  return voicesPromise
}

/** 挑一个中文音色；挑不到就让系统按 lang 自己选 */
function pickVoice(voices) {
  if (!voices || !voices.length) return null
  const zh = voices.filter((v) => /^zh/i.test(v.lang) || /Chinese|中文|普通话/i.test(v.name))
  if (!zh.length) return voices.find((v) => v.default) || voices[0]

  // ⚠️ 音色顺序是**【语调】的一部分，别随便改**。
  //    这三个都是 Windows 的老 SAPI 音源，但气质差别很大：
  //      Yaoyao   —— 年轻女声，偏轻，最贴合"学伴"人设 → 首选
  //      Xiaoxiao —— 若有（新版 Windows 自然音），比 SAPI 自然得多 → 次选
  //      Huihui   —— 成熟女声，稳但很平，像客服播报 → 兜底
  //      Kangkang —— 男声，和角色形象不符 → 不用
  const order = [/Yaoyao/i, /Xiaoxiao/i, /Huihui/i]
  for (const re of order) {
    const hit = zh.find((v) => re.test(v.name))
    if (hit) return hit
  }
  return zh.find((v) => v.default) || zh[0]
}

/**
 * 语调参数 —— 这三个值合起来决定"听起来冷不冷"。
 *
 * 【句间停顿】是其中最关键的一个，比换音色还管用。
 *   原先所有句子一次性 synth.speak() 排队，Chromium 会一句紧接一句地念完，
 *   中间没有换气 —— 这是"机械播报感"最大的来源。
 *   改成念完一句等一小会儿再念下一句，立刻就像人在说话了。
 */
const SPEECH_STYLE = {
  rate: 0.98, // 比默认(1.0)略慢 —— 语速快会显得急、显得冷
  pitch: 1.05, // 略高一点点，贴合年轻女声；再高就假了
  gapMs: 170 // 句与句之间的换气停顿
}

/** 按句子切块 —— 绕开长文本被截断的坑，同时让停止更跟手 */
function chunk(text, max = 60) {
  const s = String(text || '').replace(/\s+/g, ' ').trim()
  if (!s) return []
  // 用 match 而不是 split 的 lookbehind —— 兼容性更稳
  const parts = s.match(/[^。！？!?；;，,、\n]+[。！？!?；;\n]?/g) || [s]
  const out = []
  let buf = ''
  for (const p of parts) {
    if (buf && (buf + p).length > max) {
      out.push(buf)
      buf = p
    } else {
      buf += p
    }
  }
  if (buf.trim()) out.push(buf)
  return out
}

/* ══════════════════════════════════════════════════════════
 * 神经语音通道（edge-tts，经主进程）
 *
 * 合成在主进程做（渲染进程不该有起子进程的能力，也不该碰文件系统）。
 * 这里只负责把返回的音频播出来，以及**播不了就老实返回 false** 让上层兜底。
 * ══════════════════════════════════════════════════════════ */

/** 当前正在播的音频元素（神经语音走它） */
let audioEl = null
/** 上一次实际用的引擎，给界面/诊断看 */
let lastEngine = null

/** 合成结果缓存：同一句反复念不用重新合成（磨蹭一次一秒多，重念很明显） */
const edgeCache = new Map()
const EDGE_CACHE_MAX = 24

/**
 * 清空合成缓存。
 *
 * ⚠️ **换音色后必须调用**：缓存是按**文本**存的，不按音色。
 *    不清的话，用户换了音色去试听同一句（比如"听听看"），
 *    播出来的还是**旧音色** —— 看起来就像"换音色没生效"。
 */
export function clearCache() {
  edgeCache.clear()
}

async function tryEdge(text, my, onState) {
  const api = typeof window !== 'undefined' && window.deepprof && window.deepprof.tts
  if (!api || typeof api.synthesize !== 'function') return false

  const key = String(text)
  let res = edgeCache.get(key)
  if (!res) {
    try {
      res = await api.synthesize(key)
    } catch {
      return false // 主进程没接上/脚本报错 → 交给系统合成音
    }
    if (res && res.ok && res.audio) {
      if (edgeCache.size >= EDGE_CACHE_MAX) edgeCache.delete(edgeCache.keys().next().value)
      edgeCache.set(key, res)
    }
  }

  if (my !== generation) return true // 已被打断，但引擎确实可用
  if (!res || !res.ok || !res.audio) return false

  try {
    const a = new Audio(res.audio)
    audioEl = a
    a.onended = () => {
      if (my === generation) onState && onState('end')
    }
    a.onerror = () => {
      if (my === generation) onState && onState('error')
    }
    await a.play()
    if (my !== generation) {
      a.pause()
      return true
    }
    lastEngine = 'edge-tts'
    onState && onState('start')
    return true
  } catch {
    audioEl = null
    return false // 浏览器不给播（自动播放策略等）→ 兜底
  }
}

/** 上一次实际用的是哪个引擎 —— 给诊断和界面用 */
export function engineName() {
  return lastEngine
}

/**
 * 朗读一段文本。**会先打断正在念的内容**（这就是"可中断播放"）。
 *
 * @param text    要念的文本
 * @param onState 状态回调：'start' | 'end' | 'error' | 'unsupported'
 */
export async function speak(text, onState) {
  stop() // 打断上一段
  const my = ++generation

  // ① 先试【神经语音】：合成质量高一个数量级，是项目本来的选型
  if (await tryEdge(text, my, onState)) return
  if (my !== generation) return // 等待合成期间被打断了

  // ② 退回 Chromium 内置的【系统合成音】
  //    兜底不是可选项：对方机器上可能没装 Python、没装 edge-tts、或者没网。
  //    那种情况下桌宠必须还能说话，只是音色差一点 —— 不能干脆哑掉。
  if (!ttsSupported()) {
    onState && onState('unsupported')
    return
  }

  const voices = await loadVoices()
  if (my !== generation) return // 等待语音列表期间被新的 speak/stop 打断了

  const parts = chunk(text)
  if (!parts.length) return

  const voice = pickVoice(voices)
  onState && onState('start')

  /**
   * 逐句念，句间留停顿 —— 见 SPEECH_STYLE 的说明。
   *
   * 用链式而不是一次性全排，是为了能插入停顿。
   * 每次回调都先查 generation：被打断之后，还没轮到的那句会直接放弃，
   * 而且挂起的 setTimeout 也会因为 gen 对不上而空转一次就退出，不用额外清理。
   */
  const speakAt = (i) => {
    if (my !== generation) return

    if (i >= parts.length) {
      onState && onState('end')
      return
    }

    const u = new SpeechSynthesisUtterance(parts[i])
    if (voice) u.voice = voice
    u.lang = voice ? voice.lang : 'zh-CN'
    u.rate = SPEECH_STYLE.rate
    u.pitch = SPEECH_STYLE.pitch

    u.onend = () => {
      if (my !== generation) return
      if (i === parts.length - 1) {
        onState && onState('end')
        return
      }
      setTimeout(() => speakAt(i + 1), SPEECH_STYLE.gapMs)
    }
    u.onerror = (e) => {
      // 'interrupted'/'canceled' 是我们自己打断的，不算错误
      if (my !== generation) return
      if (e.error === 'interrupted' || e.error === 'canceled') return
      onState && onState('error')
    }

    synth.speak(u)
  }

  speakAt(0)
}

/**
 * 立即停止朗读（可中断播放的核心）。
 * ⚠️ 必须**两个引擎都停** —— 神经语音走的是 <audio>，系统合成音走的是
 *    speechSynthesis，只停一个的话另一个会继续念，表现就是"点了停止还在响"。
 */
export function stop() {
  generation++
  if (audioEl) {
    try {
      audioEl.pause()
      audioEl.currentTime = 0
    } catch {
      /* 忽略 */
    }
    audioEl = null
  }
  if (ttsSupported()) {
    try {
      synth.cancel()
    } catch {
      /* 忽略 */
    }
  }
}

export function isSpeaking() {
  if (audioEl) return !audioEl.paused && !audioEl.ended
  return ttsSupported() && !!synth.speaking
}

/**
 * 暂停 / 继续。
 *
 * ⚠️ Chromium 的已知问题：pause() 之后如果立刻 resume()，有时会卡住不继续
 * （speaking 一直是 true 但没声音）。所以 UI 上把"停止"作为主操作，
 * 暂停当附加功能。真卡住了就点停止 —— 停止是 cancel()，不受这个 bug 影响。
 */
export function pause() {
  if (audioEl && !audioEl.paused) {
    audioEl.pause()
    return
  }
  if (ttsSupported() && synth.speaking && !synth.paused) synth.pause()
}

export function resume() {
  if (audioEl && audioEl.paused) {
    audioEl.play().catch(() => {})
    return
  }
  if (ttsSupported() && synth.paused) synth.resume()
}

export function isPaused() {
  if (audioEl) return audioEl.paused
  return ttsSupported() && !!synth.paused
}

/** 给诊断用：当前能用的中文音色 */
export async function voiceReport() {
  if (!ttsSupported()) return { supported: false, voices: [] }
  const voices = await loadVoices()
  return {
    supported: true,
    total: voices.length,
    zh: voices.filter((v) => /^zh/i.test(v.lang)).map((v) => `${v.name} (${v.lang})`),
    picked: pickVoice(voices)?.name || null
  }
}
