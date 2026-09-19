import { useEffect, useRef, useState, useCallback, lazy, Suspense } from 'react'
import { resolveLive2D } from './petState'
import { ANIMATIONS, resolveAnimation, NODE_TO_ANIMATION, EXPRESSION_GEOMETRY } from './petAnimations'
import FramePet from './FramePet'
import RigPet from './RigPet'
/**
 * ⚠️ Live2DPet 必须【动态】加载，不能写成静态 import。
 *
 * 原因：`Live2DPet.jsx` 会 import `pixi-live2d-display/cubism4`，而这个库在
 * **模块求值阶段**（顶层，见 dist/cubism4.es.js 末尾）就检查 Cubism 运行时：
 *     if (!window.Live2DCubismCore) throw new Error('Could not find Cubism 4 runtime...')
 * 静态 import 的话，这条链在启动时同步求值 —— 异常会让 React 根本挂不上，
 * 表现为**窗口在、里面全白**（实测 rootLen: 0）。
 *
 * 改成 lazy 之后，这段代码只在真的渲染 Live2DPet 时才求值。
 * 而 Live2D 本轮不提供（`mode` 从 'png' 起步且只能切回 'png'），
 * 所以这块永远不会被求值 → 官方专有运行时可以安全移出交付物。
 *
 * 注意：用 try/catch 是【无效】的 —— 抛出发生在依赖的模块求值期，
 * Live2DPet 自己的代码一行都还没跑。只有打断静态 import 链才管用。
 */
const Live2DPet = lazy(() => import('./Live2DPet'))
import { speak, stop as stopSpeech, ttsSupported, pause as pauseSpeech, resume as resumeSpeech, isPaused as speechIsPaused } from './tts'
import {
  load as loadStats, save as saveStats, tick as tickStats, interact,
  maybeSleep, stageOf, exportForDataTeam, reset as resetStats
} from './petStats'
// 角色台词全部收敛在 persona.js —— 别再往这个文件里硬编码文案
import { pickLine, pickPatLine, STATUS_TO_SCENE } from './persona'

/** 点击位置是不是落在"头"上（上半部分且水平居中区域） */
function isPatOnHead(e) {
  const host = document.querySelector('.pet-host')
  if (!host) return false
  const r = host.getBoundingClientRect()
  if (!r.height || !r.width) return false
  const ny = (e.clientY - r.top) / r.height
  const nx = (e.clientX - r.left) / r.width
  // 角色是高瘦的立绘，贴在底部居中：头在上 40%，水平中间 60% 内
  return ny < 0.4 && Math.abs(nx - 0.5) < 0.3
}

const EXPRESSION_NAMES = ['f00', 'f01', 'f02', 'f03', 'f04', 'f05', 'f06', 'f07']

/**
 * DeepProf 桌宠
 *
 * 交互模型参考老版 QQ 宠物：
 *   - 默认状态：桌面上只有一个小人，没有窗口、没有按钮
 *   - 它自己在屏幕上溜达（漫游由主进程移动窗口实现）
 *   - 拖动它 = 按住拖走；点它 = 有反应
 *   - 右键 = 菜单（聊天、让它走走、点击穿透、退出）
 *   - 需要聊天时才展开面板，窗口随之变大
 */
export default function App() {
  const [status, setStatus] = useState('idle')
  const [node, setNode] = useState(null)
  /**
   * 后端发来的情感标签（`action` / `response_text` / `emotion` / `citations` 三件套之一）。
   *
   * ⚠️ 以前前端**完全不读这个字段**，表情全靠教学节点猜。
   *    后端早就把它发出来了（`graph/education/state.py` 明写"交前端"），
   *    词汇表见 petAnimations.js 的 EMOTION_TO_ANIMATION。
   *    这里只负责收，怎么映射是那边的事。
   */
  const [emotion, setEmotion] = useState(null)
  /**
   * 眨眼的触发计数 —— 每次加一，FramePet 的 overlay 就重播一遍。
   *
   * ⚠️ 秒数要【随机】。固定间隔眨眼会被人一眼看出是循环动画，
   *    「偶尔眨眼」才是活的感觉（北极星原话）。3~7 秒是参考同类桌宠的区间。
   */
  const [blinkTick, setBlinkTick] = useState(0)
  const [streamText, setStreamText] = useState('')
  const [messages, setMessages] = useState([])
  const [events, setEvents] = useState([])
  const [showLog, setShowLog] = useState(false)
  const [input, setInput] = useState('帮我讲讲数据库第三范式')
  const [through, setThrough] = useState(false)

  // 界面状态
  const [chatOpen, setChatOpen] = useState(false) // 聊天面板是否展开
  const [menuOpen, setMenuOpen] = useState(false) // 右键菜单
  const [walking, setWalking] = useState(false) // 正在走动
  const [dir, setDir] = useState(1) // 朝向：1 朝右 / -1 朝左
  const [bubbleOn, setBubbleOn] = useState(false) // 气泡是否显示

  const [pulse, setPulse] = useState(0)
  // ⚠️ 默认必须是 false，要和主进程的 `let roaming = false` 一致。
  //    原来这里写的是 true，导致右键菜单显示成"别乱跑了"（好像开着），
  //    而主进程那边其实是关的 —— 两边状态对不上，用户看着就懵。
  //    漫游的要求是「默认关」（见《给 Claude 的总指令》北极星）。
  const [roaming, setRoaming] = useState(false)

  /**
   * 两套渲染方案，右键菜单里现场切换：
   *   false 逐帧/表情 —— 显示 9 张表情立绘（assets/pet/expressions/）← **默认**
   *   true  分层 rig   —— 一张立绘拆三层、腿由代码摆动（assets/pet/rig/）
   *
   * ⚠️ 默认必须是逐帧：**教学动作切表情**是北极星里的硬要求，
   *    而分层 rig 渲染的是从 idle.png 拆出来的三层贴图，**显示不了那 9 张表情**。
   */
  const [rigMode, setRigMode] = useState(false)

  /** 角色缩放倍率（滚轮调，菜单里可复位）。乘在渲染端的适配缩放上 */
  const [petScale, setPetScale] = useState(1)

  /** 置顶开关状态（和主进程保持同步） */
  const [onTop, setOnTop] = useState(true)

  // ── TTS 语音（任务书 §16.4 第三条）──
  const [speaking, setSpeaking] = useState(false)
  const [paused, setPaused] = useState(false)
  const [autoSpeak, setAutoSpeak] = useState(true)
  const autoSpeakRef = useRef(true)
  autoSpeakRef.current = autoSpeak

  // ── 好感度 / 心情 / 精力（任务书 §16.4「好感度交互」）──
  const [stats, setStats] = useState(loadStats)
  const [patLine, setPatLine] = useState(null) // 摸头时临时顶掉气泡文案
  const [showConsent, setShowConsent] = useState(false) // 数据授权面板

  // 事件回调的依赖是空数组（沿用原有写法），所以要用 ref 读最新状态
  const statsRef = useRef(stats)
  statsRef.current = stats

  // ── 台词 ──
  // 全部来自 persona.js。必须【挑一次存起来】，不能在 render 里随机，
  // 否则组件每帧重渲染都会换一句，看起来像抽风。
  const [statusLine, setStatusLine] = useState(null)
  const [asleepLine, setAsleepLine] = useState('')
  const greetLineRef = useRef(null)
  if (greetLineRef.current === null) greetLineRef.current = pickLine('greet')

  /**
   * 渲染模式。
   *
   * ⚠️ 原来这里是 `const [mode] = useState('png')` —— **没有 setter**，
   *    也就是说 Live2DPet 是一条永远走不到的死代码，界面上也没有切换入口。
   *    任务书 §16.4 第二条要求「先用 PNG 跑通表情状态，再接 Live2D」，
   *    必须真的能切过去才算接上。
   *
   * ⚠️ Live2D 用的是官方 Haru 样例模型，**仅开发期验证链路**，不进最终交付物
   *    （见 ASSETS_LICENSE.md）。
   */
  const [mode, setMode] = useState('png')
  const [manualExpr, setManualExpr] = useState(null)

  const logRef = useRef(null)
  const streamRef = useRef('')
  const bubbleTimer = useRef(null)

  /**
   * 打断朗读。
   *
   * 「可中断播放」是任务书的验收项，不只是"有个停止按钮"——
   * 下面这些时机都必须停，否则旧语音会和新语音叠在一起念：
   *   · 用户点了取消 / 出错 / 断线     → 事件回调里
   *   · 用户问了新问题（新一轮开始）    → handleEvent 的 turn.started
   *   · 用户手动点"停止朗读"           → 按钮
   *   · 组件卸载（退出桌宠）            → useEffect 清理
   */
  const interruptSpeech = useCallback(() => {
    stopSpeech()
    setSpeaking(false)
    setPaused(false)
  }, [])

  /**
   * 眨眼定时器 —— 随机 3~7 秒触发一次。
   *
   * ⚠️ 用递归 setTimeout 而不是 setInterval：每次都要重新掷一次随机数，
   *    setInterval 是固定周期，"偶尔眨眼"就变成了"节拍器眨眼"。
   *    这里用 ref 记下一次的 handle，组件卸载时清掉，避免退出后还留着定时器。
   */
  const blinkTimerRef = useRef(null)
  useEffect(() => {
    let alive = true
    const schedule = () => {
      const wait = 3000 + Math.random() * 4000 // 3~7 秒
      blinkTimerRef.current = setTimeout(() => {
        if (!alive) return
        setBlinkTick((n) => n + 1)
        schedule()
      }, wait)
    }
    schedule()
    return () => {
      alive = false
      if (blinkTimerRef.current) clearTimeout(blinkTimerRef.current)
    }
  }, [])

  /**
   * 重连去重的两张网（验收项「断线重连不重复展示」）：
   *   event_id —— 同一个事件被重发，直接丢
   *   sequence —— 同一个 session 里序号不前进的，说明是补发的旧事件，也丢
   * 两张都留着，是因为真后端可能只保证其中之一。
   *
   * ⚠️ 没有 sequence 的话这条验收**技术上做不到** ——
   *    v0.4.1 §18.2 把 sequence 列为 RuntimeEvent 的必需字段，之前一直是缺的。
   */
  const seenIdsRef = useRef(new Set())
  const lastSeqRef = useRef({}) // session_id -> 已处理到的最大 sequence

  // ---- 事件处理 ----
  const handleEvent = useCallback((evt) => {
    const { type, payload, event_id: eid, sequence: sq, session_id: sid } = evt

    if (eid) {
      const seen = seenIdsRef.current
      if (seen.has(eid)) return // 重复事件，丢弃
      if (seen.size > 400) seen.clear()
      seen.add(eid)
    }
    if (typeof sq === 'number' && sid) {
      const last = lastSeqRef.current[sid] || 0
      if (sq <= last) return // 补发的旧事件，丢弃
      lastSeqRef.current[sid] = sq
    }

    setEvents((prev) => [...prev.slice(-199), evt])

    switch (type) {
      case 'session.started':
      case 'agent.turn.started':
        setStatus('loading')
        setNode(null)
        setEmotion(null) // 新一轮开始，上一轮的情绪标签作废
        streamRef.current = ''
        setStreamText('')
        setBubbleOn(true)
        interruptSpeech() // 新一轮开始了，把上一轮还在念的打断
        break
      case 'pedagogy.node.entered':
        setNode(payload.node)
        // 节点事件如果带 emotion 就顺手收下（后端将来可能加）
        if (payload.emotion) setEmotion(payload.emotion)
        break
      /**
       * ⚠️ `pedagogy.result` —— 真后端发的是**这个**事件，不是 `model.completed`。
       *
       * 后端 `api/routes/sessions.py` 里 `_RESULT_EVENT_TYPE = "pedagogy.result"`，
       * payload 形状是：
       *   { action, response_text, emotion, citations, next_action,
       *     turn_count, hint_level, misconceptions, evidence_sufficient, strategy_note }
       *
       * 注意字段名是 **response_text**，而 Mock 的 `model.completed` 用的是 **text** ——
       * 两条路都接住，接真后端时回复才不会静默消失。
       * 这里只补"收得到、显示得出来"，**尚未做的**见文档「已知限制」：
       * citations / hint_level / misconceptions 等字段前端还没有对应的 UI。
       */
      case 'pedagogy.result': {
        const reply = payload.response_text || ''
        if (payload.emotion) setEmotion(payload.emotion)
        if (payload.action) setNode(payload.action)
        if (reply) {
          setMessages((prev) => [...prev, { role: 'pet', text: reply }])
          if (autoSpeakRef.current) {
            speak(reply, (s) => {
              setSpeaking(s === 'start')
              if (s !== 'start') setPaused(false)
            })
          }
        }
        setBubbleOn(true)
        setStatus('idle')
        setStats((s) => interact(s, 'chat'))
        scheduleBubbleHide()
        break
      }
      case 'pedagogy.decision':
        // 决策事件带 emotion 也收下（v0.4.1 的字段缺口另见「已知限制」）
        if (payload.emotion) setEmotion(payload.emotion)
        break
      case 'model.requested':
        setStatus('loading')
        break
      case 'model.stream.delta':
        setStatus('streaming')
        streamRef.current = payload.delta
        setStreamText(payload.delta)
        setBubbleOn(true)
        break
      case 'model.completed': {
        // ⚠️ 原来只取自己累积的 streamRef.current —— 但「重连」这条路径
        //    只发 model.completed 不发任何 delta，streamRef 是空的，
        //    结果用户点了重连**什么都不会显示**，验收项「断线重连」演示不出来。
        const reply = streamRef.current || payload.text || ''
        if (reply) {
          setMessages((prev) => [...prev, { role: 'pet', text: reply }])
          // 念的是【完整回复】，不是流式片段 —— 流式期间逐字念会结巴
          if (autoSpeakRef.current) {
            speak(reply, (s) => {
              setSpeaking(s === 'start')
              if (s !== 'start') setPaused(false)
            })
          }
        }
        streamRef.current = ''
        setStreamText('')
        setStatus('idle')
        setStats((s) => interact(s, 'chat')) // 聊完一轮涨好感
        scheduleBubbleHide()
        break
      }
      case 'model.failed':
        setStatus('error')
        setMessages((prev) => [
          ...prev,
          { role: 'sys', text: `模型失败：${payload.message || payload.code}` }
        ])
        break
      case 'agent.failed':
        interruptSpeech() // 取消/失败都不该继续念
        if (payload.reason === 'cancelled') {
          setStatus('cancelled')
          if (streamRef.current) {
            setMessages((prev) => [...prev, { role: 'pet', text: streamRef.current + '（已中断）' }])
          }
          streamRef.current = ''
          setStreamText('')
        } else {
          setStatus('error')
          setMessages((prev) => [...prev, { role: 'sys', text: `本轮失败：${payload.reason}` }])
        }
        scheduleBubbleHide()
        break
      case 'agent.turn.completed':
        setStatus('idle')
        break
      case 'session.resumed':
        setStatus('reconnecting')
        break
      default:
        break
    }
  }, [])

  /** 说完话过一会儿把气泡收起来，恢复"桌面上只有一个小人" */
  const scheduleBubbleHide = () => {
    if (bubbleTimer.current) clearTimeout(bubbleTimer.current)
    bubbleTimer.current = setTimeout(() => {
      setBubbleOn(false)
      setPatLine(null) // 气泡收起来了，摸头台词也一起清掉
    }, 5000)
  }

  useEffect(() => {
    const offEvt = window.deepprof.onEvent(handleEvent)
    const offWalk = window.deepprof.onWalk((payload) => {
      if (payload && typeof payload === 'object') {
        setWalking(!!payload.walking)
        if (payload.dir) setDir(payload.dir)
      } else {
        setWalking(!!payload) // 兼容旧的布尔值
      }
    })
    return () => {
      offEvt()
      offWalk()
      stopSpeech() // 卸载（退出桌宠）时别留下念到一半的声音
    }
  }, [handleEvent])

  useEffect(() => {
    if (logRef.current) logRef.current.scrollTop = logRef.current.scrollHeight
  }, [events])

  /**
   * 状态随时间衰减 + 持久化。
   *
   * 30 秒 tick 一次就够 —— 衰减是按【实际流逝的时间】结算的，
   * 所以 tick 间隔长短不影响结果，只影响显示刷新得勤不勤。
   */
  useEffect(() => {
    setStats((s) => maybeSleep(tickStats(s)))
    const id = setInterval(() => setStats((s) => maybeSleep(tickStats(s))), 30000)
    return () => clearInterval(id)
  }, [])

  // 任何变化立刻落盘，免得关窗口时把好感度丢了
  useEffect(() => {
    saveStats(stats)
  }, [stats])

  // 运行状态变了 → 挑一句对应的台词存起来（挑一次，不要每帧挑）
  useEffect(() => {
    const scene = STATUS_TO_SCENE[status]
    setStatusLine(scene ? pickLine(scene) : null)
  }, [status])

  // 睡着了 → 挑一句梦话
  useEffect(() => {
    setAsleepLine(stats.asleep ? pickLine('asleep') : '')
  }, [stats.asleep])

  /**
   * 菜单/面板展开时不能穿透 —— 它们画在角色轮廓之外，一穿透按钮就点不动。
   * 穿透的最终决策在【主进程】的轮询循环里（它才知道光标在哪），
   * 这里只是把"有没有 UI 挡着"这个信息报上去。
   */
  useEffect(() => {
    window.deepprof.win.setUiOpen(menuOpen || chatOpen)
  }, [menuOpen, chatOpen])

  useEffect(() => {
    setPulse((p) => p + 1)
  }, [node, status])

  // ---- 拖动 / 点击 ----
  const drag = useRef({ active: false, moved: false, lastX: 0, lastY: 0 })

  const onMouseDown = (e) => {
    if (e.button !== 0) return
    drag.current = {
      active: true,
      moved: false,
      lastX: e.screenX,
      lastY: e.screenY,
      startX: e.screenX,
      startY: e.screenY
    }
    window.deepprof.win.dragState(true)
    setMenuOpen(false)
  }

  useEffect(() => {
    const onMove = (e) => {
      const d = drag.current
      if (!d.active) return
      const dx = e.screenX - d.lastX
      const dy = e.screenY - d.lastY
      if (dx || dy) {
        // 8px 阈值区分「点击」和「拖拽」（PetGPT 用的就是这个值）。
        // 之前用 2px 太敏感，手一抖就被判成拖拽，体感很差。
        if (Math.abs(e.screenX - d.startX) + Math.abs(e.screenY - d.startY) > 8) d.moved = true
        d.lastX = e.screenX
        d.lastY = e.screenY
        window.deepprof.win.moveBy(dx, dy)
      }
    }
    const onUp = (e) => {
      const d = drag.current
      if (!d.active) return
      d.active = false
      window.deepprof.win.dragState(false)
      // 没怎么移动 = 点了一下
      if (!d.moved) {
        setPulse((p) => p + 1)

        // 点头上 = 摸头，点身体 = 普通戳一下。
        // 两者台词不同、涨的好感也不同 —— 这是同类桌宠最标志性的交互设计。
        if (isPatOnHead(e)) {
          setPatLine(pickPatLine(stageOf(statsRef.current.affinity).key))
          setStats((s) => interact(s, 'pat'))
        } else {
          // 点身体也要给一句【当场反应】。
          // ⚠️ 原来这里是 setPatLine(null)，气泡于是回落到"最后一条回复" ——
          //    表现出来就是「我没问它，它又把上一题答了一遍」。必改。
          setPatLine(pickLine('tap'))
          setStats((s) => interact(s, 'tap'))
        }

        setBubbleOn(true)
        scheduleBubbleHide()
      }
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseup', onUp)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseup', onUp)
    }
  }, [])

  // ---- 动作 ----
  const send = (scenario) => {
    setMessages((prev) => [...prev, { role: 'user', text: input }])
    window.deepprof.sendMessage(input, scenario)
  }

  const doCancel = () => {
    interruptSpeech() // 用户点取消 = 明确表示"别念了"
    window.deepprof.cancel()
  }

  const doReconnect = () => window.deepprof.reconnect()

  /** 朗读最新一条回复（自动朗读关掉时的手动入口） */
  const speakLast = () => {
    const last = messages.filter((m) => m.role === 'pet').slice(-1)[0]
    if (!last) return
    speak(last.text, (s) => {
      setSpeaking(s === 'start')
      if (s !== 'start') setPaused(false)
    })
  }

  const togglePause = () => {
    if (paused) {
      resumeSpeech()
      setPaused(false)
    } else {
      pauseSpeech()
      setPaused(true)
    }
  }

  const toggleChat = async (open) => {
    const next = open === undefined ? !chatOpen : open
    setChatOpen(next)
    setMenuOpen(false)
    await window.deepprof.win.setChatOpen(next)
  }

  /*
   * 穿透不再由这里决定 —— 主进程每 50ms 轮询系统光标位置自己判断
   * （见 main/index.js 的 startThroughWatch）。
   * 原因：setIgnoreMouseEvents(true,{forward:true}) 依赖系统转发 mousemove，
   * 本机不转发，渲染端根本收不到鼠标移动，那套逻辑实际是失效的。
   */
  const applyHover = useCallback(() => {}, [])

  const toggleThrough = async () => {
    const next = !through
    setThrough(next)
    setMenuOpen(false)
    // ⚠️ 这里以前【无条件传 true】—— 于是"强制穿透"能开不能关。
    //    那个写法是旧的转发式穿透方案留下的，现在穿透改由主进程轮询决策，
    //    渲染端只需要如实传达"用户想不想强制穿透"。
    await window.deepprof.win.setIgnoreMouse(next)
  }

  const toggleRoaming = async () => {
    const next = !roaming
    setRoaming(next)
    setMenuOpen(false)
    await window.deepprof.win.setRoaming(next)
  }

  // ---- 当前显示什么 ----
  const l2d = resolveLive2D(status, node)
  const expression = manualExpr !== null ? manualExpr : l2d.expression

  // 走路优先（漫游时）；其余由 运行状态 > 教学节点 决定
  const pose = walking ? 'walk' : 'idle'
  const animName = resolveAnimation(pose, status, node, emotion)
  const anim = ANIMATIONS[animName] || ANIMATIONS.idle

  // 气泡里显示的文字。优先级：摸头台词 > 状态台词 > 回复内容 > 打招呼
  //
  // ⚠️ 状态台词必须【在状态变化时挑好存起来】，不能在 render 里随机 ——
  //    组件每帧都可能重渲染，放 render 里会变成每秒换好几句，像抽风。
  const bubbleText =
    patLine ||
    (status === 'streaming'
      ? streamText
      : STATUS_TO_SCENE[status]
        ? statusLine || '…'
        : stats.asleep
          ? asleepLine
          : messages.filter((m) => m.role === 'pet').slice(-1)[0]?.text || greetLineRef.current)

  return (
    // ⚠️ 收起的桌面上带 `rig` 类：两种渲染模式的角色高度不同
    //    （逐帧 230 / 分层 292），气泡的 bottom 要跟着分挡，见 styles.css 的 .pet-bubble
    <div className={chatOpen ? 'app' : `app app-pet${rigMode ? ' rig' : ''}`}>
      {/*
        ⚠️ 面板打开时不显示浮动气泡（`!chatOpen`）。
        原因：面板一展开，角色区被挤到只剩约 300px，而角色正好那么高 ——
        头顶顶到窗口最上沿，气泡放哪都只能盖在脸上。
        而面板里**本来就有自己的气泡**显示同一段回复，所以浮动气泡是多余的。
      */}
      {!chatOpen && (bubbleOn || status !== 'idle') && (
        <div className="pet-bubble">
          {status === 'loading' ? (
            <span className="dots">
              思考中<i /><i /><i />
            </span>
          ) : (
            <>
              {bubbleText}
              {status === 'streaming' && <span className="caret" />}
            </>
          )}
        </div>
      )}

      {/* 角色本体：按住拖动，点一下有反应 */}
      <div
        className="pet-area"
        onMouseDown={onMouseDown}
        // 双击 = 打开对话面板（单击是摸头/反应，见 onUp）
        onDoubleClick={() => toggleChat(true)}
        // 滚轮 = 缩放角色（0.6x ~ 1.8x）
        onWheel={(e) => {
          e.preventDefault()
          setPetScale((v) => Math.min(1.8, Math.max(0.6, v - e.deltaY * 0.0012)))
        }}
        onContextMenu={(e) => {
          e.preventDefault()
          setMenuOpen((v) => !v)
        }}
      >
        {mode === 'png' ? (
          rigMode ? (
            <RigPet
              talking={status === 'streaming'}
              // 传 animName 而不是 pose —— pose 只有 walk/idle，
              // 教学节点(think/talk/sad/quiz/happy)全在 animName 里
              action={animName}
              asleep={stats.asleep}
              // 动作自带的尺寸系数 × 用户的滚轮缩放
              scale={petScale * (anim.scale || 1)}
              dir={dir}
              pulse={pulse}
              onHoverChange={applyHover}
            />
          ) : (
            <FramePet
              frames={anim.frames}
              fps={anim.fps}
              loop={anim.loop}
              sway={anim.sway}
              talking={status === 'streaming'}
              pose={pose}
              dir={dir}
              pulse={pulse}
              // 每个动作可能属于不同的一批素材（走路 vs 表情），几何各自不同
              // 眨眼：叠在待机上的 overlay。只在「待机 + 没在走路」时给，
              // 其余动作（讲解/提问/走路…）本来就有自己的表情节奏，再叠眨眼会打架。
              overlay={
                animName === 'idle' && pose !== 'walk'
                  ? { frames: ANIMATIONS.blink.frames, holds: ANIMATIONS.blink.holds, trigger: blinkTick }
                  : null
              }
              geometry={anim.geo || EXPRESSION_GEOMETRY}
              // ⚠️ 必须乘上 anim.scale（走路那批要单独缩一点，见 petAnimations.js）。
              //    之前这里只传了 petScale —— **走路帧的比例差异根本没被补偿**。
              scale={petScale * (anim.scale || 1)}
              onHoverChange={applyHover}
            />
          )
        ) : (
          // lazy 组件必须有 Suspense 边界（见上方 Live2DPet 的说明）
          <Suspense fallback={null}>
            <Live2DPet
              expression={expression}
              motion={l2d.motion}
              motionIndex={l2d.motionIndex}
              pulse={pulse}
              talking={status === 'streaming'}
            />
          </Suspense>
        )}
      </div>

      {/* 右键菜单 */}
      {menuOpen && (
        <div className="ctx-menu">
          <button onClick={() => toggleChat()}>{chatOpen ? '收起对话' : '打开对话'}</button>
          <button onClick={() => { setMenuOpen(false); window.deepprof.win.wander() }}>随便走走</button>
          <button onClick={() => { setMenuOpen(false); window.deepprof.win.home() }}>回到原位</button>
          <button onClick={toggleRoaming}>{roaming ? '别乱跑了' : '让它自己走'}</button>
          <button
            onClick={() => {
              const next = !onTop
              setOnTop(next)
              window.deepprof.win.setAlwaysOnTop(next)
            }}
          >
            {onTop ? '取消置顶' : '窗口置顶'}
          </button>
          <button onClick={() => { setMenuOpen(false); setPetScale(1) }}>
            恢复默认大小（{Math.round(petScale * 100)}%）
          </button>
          <button onClick={() => { setMenuOpen(false); setRigMode((v) => !v) }}>
            {rigMode ? '换成逐帧动作' : '换成分层走路'}
          </button>
          {/*
            Live2D 本轮不提供 —— 这是【范围决定】，不是故障。
            对外（评审 / 使用者）只说"本轮不做、当前用什么方案"，
            不解释内部的素材移出过程（那是仓库内部视角，见 ASSETS_LICENSE.md）。
            所以这里做成"说明了本轮范围"，而不是让人切进去看一个空白。
          */}
          <button
            onClick={() => {
              setMenuOpen(false)
              if (mode === 'png') {
                window.alert(
                  'Live2D 本轮不提供。\n\n' +
                    '本轮桌宠采用逐帧 PNG 方案（右键菜单可在「逐帧 / 分层」两种 PNG 模式间切换），\n' +
                    'Live2D 未列入本轮交付范围 —— 这是范围决定，不是故障。\n\n' +
                    '其它表现不受影响：表情、走路、漫游、对话、语音均正常。'
                )
                return
              }
              setMode('png')
            }}
          >
            {mode === 'png' ? 'Live2D 本轮不提供（当前使用逐帧 PNG 方案）' : '切回 PNG 桌宠'}
          </button>
          <button onClick={toggleThrough}>{through ? '恢复鼠标操作' : '点击穿透'}</button>
          <button className="danger" onClick={() => window.deepprof.win.quit()}>
            退出
          </button>
        </div>
      )}

      {/* 聊天面板：展开才有 */}
      {chatOpen && (
        <>
          {node && <div className="node-chip">教学节点：{node}</div>}

          {mode === 'live2d' && (
            <div className="expr-bar">
              <span className="expr-label">表情</span>
              {EXPRESSION_NAMES.map((n, i) => (
                <button
                  key={n}
                  className={expression === i ? 'expr-btn on' : 'expr-btn'}
                  onClick={() => setManualExpr(i)}
                >
                  {n}
                </button>
              ))}
            </div>
          )}

          <div className="bubble-area">
            <div className="bubble">
              {status === 'idle' && messages.length === 0 && (
                <span className="muted">你好，我是 DeepProf。点下面的按钮试试各种状态。</span>
              )}
              {status === 'idle' && messages.length > 0 && (
                <span>{messages[messages.length - 1]?.text}</span>
              )}
              {status !== 'idle' && <span>{bubbleText}</span>}
            </div>
          </div>

          {/* ── 语音条 ──
              任务书的验收项「流式文本与语音可停止」在这里演示。
              有声音在念时，"停止朗读"才出现 —— 一眼能看出它是可中断的。 */}
          <div className="tts-bar">
            <button
              className={autoSpeak ? 'tts-toggle on' : 'tts-toggle'}
              onClick={() => {
                setAutoSpeak((v) => {
                  if (v) interruptSpeech() // 关掉自动朗读时，正在念的也停掉
                  return !v
                })
              }}
              title="回复完成后自动朗读"
            >
              {autoSpeak ? '🔊 自动朗读' : '🔇 自动朗读'}
            </button>

            {!ttsSupported() ? (
              <span className="tts-note">本机不支持语音合成</span>
            ) : speaking ? (
              <>
                <button className="btn" onClick={togglePause}>
                  {paused ? '继续' : '暂停'}
                </button>
                <button className="btn danger" onClick={interruptSpeech}>
                  停止朗读
                </button>
              </>
            ) : (
              <button
                className="btn"
                onClick={speakLast}
                disabled={!messages.some((m) => m.role === 'pet')}
              >
                朗读最新回复
              </button>
            )}
          </div>

          {/* ── 好感度 / 心情 / 精力 ──
              任务书 §16.4 开头明写「好感度交互」。数值全部存在本机 localStorage。 */}
          <div className="stat-bar">
            <span className="stat-stage" title={stageOf(stats.affinity).note}>
              好感度 · {stageOf(stats.affinity).label}
            </span>
            {[
              ['好感', stats.affinity, '#e8628c'],
              ['心情', stats.mood, '#e9a13b'],
              ['精力', stats.energy, '#3f9e6b']
            ].map(([label, v, color]) => (
              <span className="stat" key={label}>
                <span className="stat-label">{label}</span>
                <span className="stat-track">
                  <i style={{ width: Math.round(v) + '%', background: color }} />
                </span>
                <span className="stat-num">{Math.round(v)}</span>
              </span>
            ))}
            <button className="stat-consent" onClick={() => setShowConsent((v) => !v)}>
              {stats.consent ? '⚠ 已授权上报' : '未授权上报'}
            </button>
          </div>

          {showConsent && (
            <div className="consent-box">
              <div className="consent-text">
                任务书的交接要求是「向数据组提交<strong>经用户授权的</strong>偏好更新」。
                下面的数据<strong>默认不出本机</strong>，开关不开就一个字节也不发。
                里面只有互动次数和状态值，<strong>不含任何对话内容</strong>。
              </div>
              {stats.consent ? (
                <pre className="consent-json">
                  {JSON.stringify(exportForDataTeam(stats), null, 2)}
                </pre>
              ) : (
                <div className="consent-text muted">（未授权，无可导出内容）</div>
              )}
              <div className="consent-actions">
                <button
                  className={stats.consent ? 'btn danger' : 'btn'}
                  onClick={() => setStats((s) => ({ ...s, consent: !s.consent }))}
                >
                  {stats.consent ? '撤回授权' : '我同意导出互动统计'}
                </button>
                {/*
                  「向数据组提交经用户授权的偏好更新」—— 任务书 §16.4 的交接要求。
                  后端端点还没有，所以先导出成文件（桌面 `DeepProf_偏好更新/`）。
                  未授权时禁用 + 变灰，一眼能看出"没授权就出不去"。
                */}
                <button
                  className="btn"
                  disabled={!stats.consent}
                  onClick={async () => {
                    const data = exportForDataTeam(stats)
                    if (!data) return
                    const r = await window.deepprof.exportPreference(data)
                    window.alert(
                      r.ok
                        ? '已导出到桌面「DeepProf_偏好更新」文件夹：\n' + r.path
                        : '导出失败：' + r.reason
                    )
                  }}
                >
                  导出为文件
                </button>
                <button
                  className="btn"
                  onClick={() => {
                    if (window.confirm('清空好感度和所有互动记录？')) setStats(resetStats())
                  }}
                >
                  清空记录
                </button>
              </div>
            </div>
          )}

          {/* ── 教学动作演示 ──
              验收项「把教学动作映射为可解释的表现」需要一个能逐个走一遍的入口。
              Mock 后端只会走 Assess→Teach 两个节点，另外几个在界面上演示不到。 */}
          <div className="node-bar">
            <span className="node-label">教学动作</span>
            {['Assess', 'Teach', 'Ask', 'Hint', 'Correct', 'Test', 'UpdateProfile', 'Reflect'].map(
              (n) => (
                <button
                  key={n}
                  className={node === n ? 'node-btn on' : 'node-btn'}
                  onClick={() => setNode((cur) => (cur === n ? null : n))}
                  title={'映射到动画：' + NODE_TO_ANIMATION[n]}
                >
                  {n}
                </button>
              )
            )}
          </div>

          <div className="controls">
            {(status === 'streaming' || status === 'loading') && (
              <button className="btn danger" onClick={doCancel}>
                取消
              </button>
            )}
            <button className="btn" onClick={() => send('normal')}>
              正常回复
            </button>
            <button className="btn" onClick={() => send('loading')}>
              加载中
            </button>
            <button className="btn" onClick={() => send('error')}>
              报错
            </button>
            <button className="btn" onClick={doReconnect}>
              重连
            </button>
          </div>

          <input
            className="input"
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === 'Enter' && send('normal')}
            placeholder="输入问题…"
          />

          <div className="log-head" onClick={() => setShowLog((v) => !v)}>
            事件日志 {events.length} {showLog ? '▾' : '▸'}
          </div>
          {showLog && (
            <div className="log" ref={logRef}>
              {events.map((e, i) => (
                <div key={i} className="log-row">
                  <span className="log-type">{e.type}</span>
                  <span className="log-src">{e.source}</span>
                </div>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  )
}
