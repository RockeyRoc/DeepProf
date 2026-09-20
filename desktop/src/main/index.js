/**
 * DeepProf 桌宠 —— 主进程
 *
 * 职责（对应 DESIGNv0.4 §4.1 交互层）：
 *   1. 创建桌宠窗口：无边框 / 透明 / 置顶 / 可点击穿透
 *   2. 持有 Mock Runtime（将来换成真实后端连接）
 *   3. 通过 IPC 把统一事件推给渲染进程
 *
 * 安全边界（对应验收标准「渲染进程不持有模型密钥」）：
 *   - contextIsolation: true
 *   - nodeIntegration: false
 *   - preload 只暴露白名单方法，不暴露裸 ipcRenderer
 *   - 任何模型调用都发生在主进程，渲染进程永远拿不到密钥
 *
 * ⚠️ 注意：Mock Runtime 故意写在本文件内，不拆成单独模块。
 *   electron-vite 打包 main 进程时会把相对路径的 require 保留为外部依赖，
 *   导致运行时 Cannot find module。拆文件会让团队后续加主进程模块时重复踩坑。
 */

const {
  app,
  BrowserWindow,
  ipcMain,
  screen,
  Tray,
  Menu,
  nativeImage,
  session,
  globalShortcut
} = require('electron')
const path = require('path')

/** 开发模式：electron-vite dev 会注入这个环境变量 */
const isDev = !!process.env.ELECTRON_RENDERER_URL

/**
 * 内容安全策略。
 * 生产环境收紧到只允许自身资源；开发环境要放开 Vite HMR 需要的
 * 内联脚本与 WebSocket，否则渲染进程白屏。
 */
function applyCsp() {
  // ⚠️ media-src 是给神经语音留的：合成结果以 data:audio/mpeg;base64 返回，
  //    渲染进程 new Audio(...) 播放。不显式放行的话，生产环境会被 CSP 拦掉，
  //    而且是**静默失败**（音频元素直接 error），很难查。
  //    开发环境虽然有 default-src 的 data: 兜着，也显式写出来保持一致。
  const policy = isDev
    ? "default-src 'self' 'unsafe-inline' data: ws: http://localhost:*; img-src 'self' data:; media-src 'self' data:"
    : "default-src 'self'; img-src 'self' data:; media-src 'self' data:; style-src 'self' 'unsafe-inline'; script-src 'self'; connect-src 'self'"

  session.defaultSession.webRequest.onHeadersReceived((details, callback) => {
    callback({
      responseHeaders: {
        ...details.responseHeaders,
        'Content-Security-Policy': [policy]
      }
    })
  })
}

/* =========================================================================
 * 一、Mock 事件流 —— 模拟后端 DeepProf Runtime 吐出的统一事件
 *
 * 为什么放在主进程：
 *   按 DESIGNv0.4 §7.2，真实事件由后端（刘俊鹏的 Runtime）产生，
 *   渲染进程只负责「消费统一事件」。把 Mock 放主进程，
 *   将来换成真后端时渲染层一行都不用改。
 *
 * 事件模型依据 DESIGNv0.4 §5.4：
 *   公共字段 event_id / session_id / trace_id / timestamp / type / payload / source
 * ========================================================================= */

const { EventEmitter } = require('events')

let seq = 0
function uid(prefix) {
  seq += 1
  return `${prefix}_${Date.now().toString(36)}_${seq}`
}

/**
 * 事件序号 —— **v0.4.1 §18.2 把 `sequence` 列为 RuntimeEvent 的必需字段**。
 *
 * 为什么必须有：验收项「断线重连不重复展示」靠它实现 ——
 * 重连时后端可能把没确认的事件重发一遍，前端按 sequence 判断新旧，
 * 丢弃已经展示过的，才不会把同一段回复显示两次。
 * 没有这个字段，那条验收在技术上就做不到（之前一直是缺的）。
 */
let sequence = 0

/** 组装一个符合 §5.4 规范的事件 */
function makeEvent(sessionId, traceId, type, payload = {}, source = 'runtime') {
  return {
    event_id: uid('evt'),
    session_id: sessionId,
    trace_id: traceId,
    sequence: ++sequence, // 单调递增，前端据此去重
    timestamp: new Date().toISOString(),
    type,
    payload,
    source
  }
}

/**
 * 演示用回复文本，按教学节点分段。
 *
 * ⚠️ 这里是 Mock，不是真回答。
 * 真实回答由后端 Runtime 走 RAG + LLM 产生；Mock 只负责把
 * 「事件流 + 状态 + 表情」这条链路演示出来。
 * Assess 段做成函数，是为了让它至少能接住用户实际问的那句话，
 * 免得看起来像程序坏了。
 */
const SCRIPT = {
  Assess: (q) => {
    // ⚠️ 开场【不要】写"真实回答要等后端接上" —— 实测反馈那样看起来像"它答不了"。
    //    声明挪到最后一行（见 runScript 末尾）。
    //    这里用苏格拉底式开场：先接住问题、再问学生的起点。
    const short = String(q || '').trim().slice(0, 24)
    return short
      ? `你问的是「${short}」。

先别急着要结论 —— 我想先确认你的起点：这块你之前接触过吗？大概知道它在解决什么问题吗？`
      : '先说说你的起点：这块你之前接触过吗？知道它大概在解决什么问题吗？'
  },
  // ⚠️ 下面这几句以前**从来没被发出过**。
  //    老版本只发 SCRIPT.Assess，然后就完了 —— 所以不管你问什么，
  //    得到的永远是同一句反问，等于 Mock 根本不产生答案。
  //
  // ⚠️ 措辞上踩过一次坑：不要在开头写「真实回答要等后端接上」——
  //    那样第一眼看到的就是"我答不了"，看起来像坏了（张钧翔实测反馈）。
  //    **声明挪到最后一行**，开场直接进教学。
  Teach:
    '先把三范式串一遍，你就明白它到底在解决什么了。\n\n' +
    '第一范式（1NF）：每个字段都不可再分。比如"联系方式"一列里同时塞了手机号和邮箱，就不满足 1NF —— 得拆成两列。\n\n' +
    '第二范式（2NF）：在 1NF 基础上，非主属性必须【完全】依赖候选键，不能只依赖其中一部分。' +
    '典型反例是「选课表（学号, 课程号, 成绩, 课程名）」—— 课程名只依赖课程号这一半，不依赖学号，这就叫部分依赖。\n\n' +
    '第三范式（3NF）：在 2NF 基础上，非主属性不能【传递】依赖候选键。' +
    '也就是说，不能再出现「A → B → C」这种绕一个弯才依赖到键上的情况。\n\n' +
    '一句话总结：**拆到每个非主属性都直接依赖键、而且只依赖键，就到位了。** 代价是表变多、查询要多一次 JOIN —— 这是拿空间换一致性。',
  Ask: '那我考你一个。手上有张表：\n（学号, 课程号, 教师名）\n其中教师名只依赖课程号、不依赖学号。\n\n它满足 3NF 吗？先别急着答，说说你的判断过程。',
  Hint: '给你个抓手：先找出这张表的**候选键**（是「学号+课程号」），' +
    '再看有没有哪个非主属性是**绕了个弯**才依赖到键上的 —— 如果它其实依赖的是键的一部分，那就是部分依赖；' +
    '如果它依赖的是另一个非主属性，那就是传递依赖。两条都对不上，才算 3NF。',
  Correct: '你答到一半了。这张表满足 2NF，但**不满足 3NF** —— ' +
    '因为存在 课程号 → 教师名 这条传递依赖：教师名绕过了候选键（学号+课程号），直接挂在课程号上。\n\n' +
    '拆法：把教师名挪出去，变成两张表 ——\n' +
    '· 选课表（学号, 课程号）\n' +
    '· 课程表（课程号, 教师名）\n' +
    '这样就干净了。',
  Test: '出个题验证一下：\n\n若有关系 R(学号, 姓名, 课程号, 成绩)，其中「学号→姓名」。\n\n① 它的候选键是什么？\n② 存在哪种依赖问题？\n③ 该怎么拆？',
  UpdateProfile: '好，这次的**传递依赖**和**部分依赖**你都过了一遍。\n\n' +
    '我记了两个点：\n· 判断先找候选键 —— 这一步你容易跳过\n· 传递依赖和部分依赖容易混 —— 前者是"依赖非主属性"，后者是"依赖键的一部分"\n\n下次复习会优先带这两个点。'
}

class MockRuntime extends EventEmitter {
  constructor() {
    super()
    this.sessionId = uid('sess')
    this.traceId = null
    this.timer = null
    this.cancelled = false
  }

  emitEvent(type, payload, source) {
    this.emit('event', makeEvent(this.sessionId, this.traceId, type, payload, source))
  }

  clearTimer() {
    if (this.timer) {
      clearTimeout(this.timer)
      this.timer = null
    }
  }

  /** 取消当前这一轮 */
  cancel() {
    if (!this.traceId) return false
    this.cancelled = true
    this.clearTimer()
    // ⚠️ 2026-09-20：由 agent.failed 改成 agent.cancelled —— **真后端发的是这个**。
    //    后端 runtime/core/agent.py 捕获 asyncio.CancelledError 时发 EventType.AGENT_CANCELLED
    //    （独立事件类型，与 AGENT_FAILED 是两条路径），payload 同样是 {reason:'cancelled'}。
    //    Mock 原先模仿得不准，导致契约里那句「agent.failed 是取消状态的唯一表达方式」对真后端不成立。
    //    前端两个 case 都接（App.jsx 里 case 直落），所以**表现零变化**。
    this.emitEvent('agent.cancelled', { reason: 'cancelled', message: '本轮已取消' }, 'renderer')
    this.emitEvent('session.compacted', { reason: 'cancelled' })
    this.traceId = null
    return true
  }

  /** 断线重连：恢复会话 */
  reconnect() {
    this.clearTimer()
    this.emitEvent('session.resumed', { resumed_from: this.sessionId })
    this.timer = setTimeout(() => {
      this.emitEvent('model.completed', { text: '会话已恢复，可以继续。' })
      this.emitEvent('agent.turn.completed', { status: 'ok' })
      this.traceId = null
    }, 900)
  }

  /**
   * 跑一轮。scenario 演示五种状态：
   *   normal    正常流式回复
   *   loading   一直加载
   *   error     中途失败
   *   reconnect 断线
   * （「取消」由界面上的取消按钮触发 cancel()）
   */
  run(text, scenario = 'normal') {
    this.clearTimer()
    this.cancelled = false
    this.traceId = uid('trace')

    this.emitEvent('session.started', { resumed: false })
    this.emitEvent('agent.turn.started', { input: text })
    this.emitEvent('pedagogy.node.entered', { node: 'Assess' })

    if (scenario === 'reconnect') {
      this.timer = setTimeout(() => {
        this.emitEvent('model.failed', { code: 'CONN_RESET', message: '连接中断' })
        this.emitEvent('agent.failed', { reason: 'network', retryable: true })
        this.traceId = null
      }, 500)
      return
    }

    this.timer = setTimeout(() => {
      this.emitEvent('model.requested', { provider: 'deepseek', model: 'deepseek-chat' })

      // 只停在「加载中」，不发 delta
      if (scenario === 'loading') return

      if (scenario === 'error') {
        this.timer = setTimeout(() => {
          this.emitEvent('model.failed', { code: 'RATE_LIMIT', message: '模型服务限流' })
          this.emitEvent('agent.failed', { reason: 'provider', retryable: true })
          this.traceId = null
        }, 700)
        return
      }

      this.runScript(text)
    }, scenario === 'loading' ? 300 : 500)
  }

  /**
   * 走一遍教学流程：Assess → Teach → Ask → UpdateProfile。
   *
   * ═══ 为什么必须是"走一遍"，而不是只发一句 ═══
   *
   * 老版本只发 SCRIPT.Assess 就结束了，导致两个后果：
   *   1. 不管你问什么，得到的永远是同一句反问 —— 演示起来像坏了
   *   2. 八个教学节点只走得到两个，其余六个在界面上**演示不出来**，
   *      而「把教学动作映射为可解释的表现」正是任务书的验收项
   *
   * ═══ 只提交一次 model.completed ═══
   *
   * 中间节点只发 node.entered + 流式 delta（气泡里能看到它在"边说边想"），
   * **只有最后才发一次 model.completed**，携带拼接后的完整回复。
   * 这样语音只念一次 —— 每个节点都念的话，后一段会打断前一段（speak 会先 cancel），
   * 听感就是断断续续的碎片。
   */
  runScript(input) {
    // 走全六个教学节点，让「教学动作 → 表情」这条映射在演示里都过一遍。
    // 节点之间留 dwell，动画才来得及播（以前没有停顿，动画刚起头就被顶掉）。
    const steps = [
      { node: 'Assess', text: SCRIPT.Assess(input), dwell: 800 },
      { node: 'Teach', text: SCRIPT.Teach, dwell: 900 },
      { node: 'Ask', text: SCRIPT.Ask, dwell: 900 },
      { node: 'Hint', text: SCRIPT.Hint, dwell: 900 },
      { node: 'Correct', text: SCRIPT.Correct, dwell: 900 },
      { node: 'UpdateProfile', text: SCRIPT.UpdateProfile, dwell: 0 }
    ]
    // ⚠️「这是演示数据」的声明放【最后一行】，不挡在前面。
    //    放开头会让第一眼看到的是"我答不了"，像坏了（张钧翔实测反馈）。
    const full =
      steps.map((s) => s.text).join('\n\n') +
      '\n\n——（以上是 Mock 演示数据；真实回答由后端 Runtime 产生，见 DESIGNv0.4 §7.2）'
    let si = 0

    /** 走下一个节点；全走完就收尾。用同一个递归函数，避免出现"收尾分支到不了"的坑 */
    const runNode = () => {
      if (this.cancelled) return

      if (si >= steps.length) {
        const last = steps[steps.length - 1]
        this.emitEvent('pedagogy.node.exited', { node: last.node })
        this.emitEvent('memory.write', { dimension: 'episodic', key: 'db_normalization' })
        // ⚠️ 这几个字段是**为了演示「教学依据条」才加的**，真后端把它们塞在
        //    `pedagogy.result` 的 payload 里（api/routes/sessions.py 的 _result_frame）。
        //    Mock 走的是 model.completed 这条链路，所以捎带发同名同形的字段，
        //    好让那条 UI 在本地也能看到内容。
        //
        //    数值都是**编的**（按本轮确实走过的节点编），不是真测量：
        //      · citations 空 + evidence_sufficient=false —— 诚实反映"Mock 没有 RAG"，
        //        顺便演示"没有证据时不编造引用"这条规则长什么样
        //      · misconceptions 一条是配合默认问题（数据库第三范式）编的
        this.emitEvent('model.completed', {
          text: full,
          turn_count: 1,
          hint_level: 1, // 本轮确实走过了 Hint 节点
          citations: [],
          misconceptions: ['可能把「消除传递依赖」和「拆表」当成同一件事'],
          evidence_sufficient: false,
          next_action: 'end',
          strategy_note: 'mock: 走完全部六个教学节点'
        })
        this.emitEvent('agent.turn.completed', { status: 'ok' })
        this.traceId = null
        return
      }

      const st = steps[si]
      if (si > 0) this.emitEvent('pedagogy.node.exited', { node: steps[si - 1].node })
      this.emitEvent('pedagogy.node.entered', { node: st.node })
      if (si === 0) {
        this.emitEvent('pedagogy.decision', { next: 'Teach', reason: '先确认先验知识' })
        this.emitEvent('memory.read', { query: 'recent_mistakes' })
      }

      // 逐字吐字。节点之间留 dwell 停顿，让动画**来得及播出来** ——
      // 以前节点之间没有停顿，动画刚起头就被下一个节点顶掉了。
      let j = 0
      const tick = () => {
        if (this.cancelled) return
        if (j >= st.text.length) {
          si += 1
          this.timer = setTimeout(runNode, st.dwell)
          return
        }
        j += 4
        this.emitEvent('model.stream.delta', { delta: st.text.slice(0, j), index: j })
        this.timer = setTimeout(tick, 22)
      }
      tick()
    }

    runNode()
  }
}

/* =========================================================================
 * 二、窗口与 IPC
 * ========================================================================= */

/** @type {BrowserWindow | null} */
let win = null
/** @type {Tray | null} */
let tray = null
/** @type {MockRuntime | null} */
let runtime = null

/** 渲染进程订阅事件用的频道名 */
const EVENT_CHANNEL = 'runtime:event'

function createWindow() {
  const { workArea } = screen.getPrimaryDisplay()
  // 默认只有角色那么大 —— 桌宠就该是桌面上一个小人，不是个应用窗口
  const W = PET_W
  const H = PET_H

  win = new BrowserWindow({
    width: W,
    height: H,
    // 默认贴在屏幕右下角
    x: workArea.x + workArea.width - W - 24,
    y: workArea.y + workArea.height - H - 24,
    frame: false, // 无边框
    transparent: true, // 透明背景（桌宠要浮在桌面上）
    resizable: false,
    maximizable: false,
    fullscreenable: false,
    hasShadow: false,
    alwaysOnTop: true, // 置顶
    skipTaskbar: false,
    // 注意：这里不用 show:false + ready-to-show 的写法。
    // Windows 上 transparent:true 时 ready-to-show 经常不触发，
    // 结果就是窗口建了但永远不显示 —— 直接显示更可靠。
    show: true,
    backgroundColor: '#00000000', // 全透明底色
    webPreferences: {
      preload: path.join(__dirname, '../preload/index.js'),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: false
    }
  })

  // 置顶层级拉到最高，避免被其他窗口盖住
  win.setAlwaysOnTop(true, 'screen-saver')

  // 关窗口 = 隐藏，不是退出（《给 Claude 的总指令》P0 要求）。
  // 退出只能走托盘第一项或 Ctrl+Alt+Shift+X —— 这样用户误关窗口不会丢桌宠。
  win.on('close', (e) => {
    if (!reallyQuitting) {
      e.preventDefault()
      win.hide()
    }
  })


  // 兜底：无论 ready-to-show 是否触发，都强制显示一次
  win.once('ready-to-show', () => win.show())
  win.webContents.once('did-finish-load', async () => {
    win.show()
    const [wx, wy] = win.getPosition()
    console.log(`[桌宠] 窗口已显示 @ (${wx}, ${wy})  ${win.getSize().join('x')}`)

    // 诊断：确认 React 是否真的挂载了（窗口透明时白屏很难肉眼发现）
    try {
      const probe = await win.webContents.executeJavaScript(`
        (() => {
          const root = document.getElementById('root')
          const pet = document.querySelector('.pet')
          return {
            rootLen: root ? root.innerHTML.length : -1,
            hasPet: !!pet,
            petSrc: pet ? pet.getAttribute('src') : null,
            petComplete: pet ? pet.complete : null,
            petNatural: pet ? (pet.naturalWidth + 'x' + pet.naturalHeight) : null,
            bodyBg: getComputedStyle(document.body).backgroundColor
          }
        })()
      `)
      console.log('[桌宠] 渲染诊断:', JSON.stringify(probe))
    } catch (err) {
      console.error('[桌宠] 诊断失败:', err.message)
    }

    // 让 Electron 自己抓自己的窗口 —— 分层窗口用 GDI 截图抓不到，只能这么验证
    const snap = async (tag) => {
      try {
        const img = await win.webContents.capturePage()
        const out = path.join(app.getPath('temp'), `deepprof-window${tag}.png`)
        require('fs').writeFileSync(out, img.toPNG())
        console.log(`[桌宠] 窗口自截图已保存${tag}:`, out)
      } catch (err) {
        console.error('[桌宠] 自截图失败:', err.message)
      }
    }

    // ⚠️ 只抓这一张是没用的：此刻 React 刚挂载，Pixi 的贴图还在异步加载，
    //    画布是空的，抓出来一片透明（看不出是"没渲染"还是"渲染成透明"）。
    //    所以再过几秒抓一张 —— 那张才反映真实画面。
    await snap('')
    setTimeout(() => snap('-2s'), 2500)


    // 连拍：桌宠是"溜达"的，只在启动瞬间抓一张会正好错过它在走的时刻。
    // 平时别开（每次启动留一堆文件太吵），要查动画时设 DEEPPROF_SNAP=1 再启动。
    //
    // ⚠️ 2026-09-20 加了间隔/张数可配：原来写死 2 秒 × 10 张，**抓不到眨眼**。
    //    眨眼闭眼只有 110ms，2 秒一采样撞上的概率约 5%。要查眨眼得把间隔压到
    //    100~200ms 连拍十几秒，比如：
    //        DEEPPROF_SNAP=1 DEEPPROF_SNAP_MS=120 DEEPPROF_SNAP_N=120 npm run dev
    if (process.env.DEEPPROF_SNAP) {
      const every = Number(process.env.DEEPPROF_SNAP_MS) || 2000
      const count = Number(process.env.DEEPPROF_SNAP_N) || 10
      for (let i = 1; i <= count; i++) {
        setTimeout(() => snap(`-t${i}`), i * every)
      }
      console.log(
        `[桌宠] 连拍模式已开启（DEEPPROF_SNAP）—— 每 ${every}ms 一张，共 ${count} 张`
      )
    }
  })

  // 开发模式走 vite dev server，生产模式读打包产物
  if (process.env.ELECTRON_RENDERER_URL) {
    win.loadURL(process.env.ELECTRON_RENDERER_URL)
  } else {
    win.loadFile(path.join(__dirname, '../renderer/index.html'))
  }

  // 加载失败要能在终端看到，否则窗口透明 + 白屏 = 什么都发现不了
  win.webContents.on('did-fail-load', (_e, code, desc, url) => {
    console.error('[桌宠] 渲染进程加载失败:', code, desc, url)
  })

  // 渲染进程里的 JS 报错（console.error 及以上）转发到终端
  win.webContents.on('console-message', (_e, level, message, line, sourceId) => {
    if (level >= 2) {
      console.error(`[渲染进程] ${message}    @ ${sourceId}:${line}`)
    }
  })
  win.webContents.on('render-process-gone', (_e, details) => {
    console.error('[桌宠] 渲染进程崩溃:', details)
  })

  win.on('closed', () => {
    win = null
  })
}

/**
 * 托盘图标 —— 用桌宠自己的脸，base64 内嵌。
 *
 * ⚠️ 这里曾经用 1x1 透明图占位，结果托盘里根本看不见，
 * 用户既找不到小人也没法退出 —— 托盘是这个应用唯一的常驻退出入口，
 * 它必须是可见的。base64 内嵌是为了避免打包后路径失效。
 */
const TRAY_ICON_DATA_URL =
  'data:image/png;base64,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'

function createTray() {
  const icon = nativeImage.createFromDataURL(TRAY_ICON_DATA_URL)
  tray = new Tray(icon)
  tray.setToolTip('DeepProf 桌宠 —— 右键可退出')
  tray.setContextMenu(
    Menu.buildFromTemplate([
      // ⚠️ 第一项必须是【退出】—— 这是《给 Claude 的总指令》P0 的明确要求。
      //    托盘是唯一在任何状态下都能退出的入口（窗口可能被拖到屏幕外、
      //    也可能被点穿透，右键点不到）。
      { label: '退出', click: () => quitApp() },
      { type: 'separator' },
      {
        label: '显示 / 隐藏',
        click: () => (win && win.isVisible() ? win.hide() : win && win.show())
      },
      { label: '把小人叫回来（归位）', click: () => bringHome() },
      {
        label: '自动溜达',
        type: 'checkbox',
        checked: roaming,
        click: (item) => {
          roaming = item.checked
          if (!roaming) stopMoving()
        }
      },
      { type: 'separator' },
    ])
  )
}

/* =========================================================================
 * 三、漫游 —— 让桌宠像老版 QQ 宠物那样在桌面上自己走动
 *
 * 原理：窗口就是角色的"身体"。移动窗口 = 角色在屏幕上走。
 * 做法：主进程定时挑一个新落点，分帧平滑移动过去，
 *       同时通知渲染进程播放"走路"动画。
 * ========================================================================= */

/**
 * 漫游是否开启。
 * ⚠️ 默认关闭 —— 上次因为窗口没有被夹在屏幕内，小人跑出了屏幕又关不掉。
 * 现在位置已经做了强制夹取，但先让用户手动确认它稳了，再从托盘里打开自动溜达。
 */
let roaming = false
let roamTimer = null
let moveTimer = null

/**
 * 宠物窗口的尺寸（只有角色，没有面板）。
 *
 * ⚠️ 2026-09-20：宽度由 220 放宽到 330，为的是 Q 版新角色。
 *    旧角色是**修长**的（外接框 731×1201），Q 版是**矮胖**的（1244×1201，宽了 70%）。
 *    窗口只有 220 宽时是"宽度受限"的，Q 版被压到只剩 **212px 高**；
 *    放到 330 之后是 **318px 高**，接近旧角色原本的 361px，桌面上存在感差不多。
 *    再宽（380）能到 367px，但窗口横向占得太多，330 是折中。
 *
 *    改这个值要连带留意：安全夹取（safeSetPosition 用 winW/winH）、
 *    拖动、点击命中区域、以及面板模式的 380×620 切换 —— 都要实机验过。
 */
const PET_W = 330
/**
 * 桌宠模式的窗口高度 = **角色 300 + 气泡 120**。
 *
 * ⚠️ 为什么不是 300（只装角色）：气泡是锚在窗口【顶部】的，角色锚在【底部】。
 *    如果窗口高度刚好等于角色高度，气泡只能盖在角色头上（实测会挡住额头）。
 *    而如果让窗口"气泡出现时临时长高"，拖动时窗口位置/气泡/角色会互相追，
 *    出现"气泡越拖离人物越远"（试过，更糟）。
 *    所以干脆**固定预留**：窗口永远是 360，角色 300 贴底，上面 60 给气泡。
 *    这样窗口一动不动，气泡和角色的相对位置也永远不变。
 */
const PET_H = 420

/**
 * 安全地设置窗口位置 —— 永远夹在屏幕工作区内。
 *
 * 所有移动（漫游、拖动、归位）都必须走这里。
 * 之前直接调 win.setPosition 不做夹取，结果窗口飘到屏幕外（y 变成负数），
 * 用户既看不到也点不到，等于把桌宠弄丢了。
 */
/**
 * 窗口当前【应有的】尺寸。
 *
 * ⚠️ 必须显式维护、并在每次移动时带上 —— 见 safeSetPosition 里的说明。
 * 窗口尺寸有几种状态：宠物模式 PET_W×PET_H、面板模式 380×620。
 */
let winW = PET_W
let winH = PET_H

function safeSetPosition(x, y) {
  if (!win || win.isDestroyed()) return
  const { workArea } = screen.getPrimaryDisplay()

  const maxX = workArea.x + workArea.width - winW
  const maxY = workArea.y + workArea.height - winH

  const nx = Math.min(Math.max(x, workArea.x), Math.max(workArea.x, maxX))
  const ny = Math.min(Math.max(y, workArea.y), Math.max(workArea.y, maxY))

  // ⚠️ 这里必须用 setBounds 并【显式带上尺寸】，不能只 setPosition。
  //
  //    在这台机器上实测（614 条 resize 日志）：只调 setPosition，窗口尺寸
  //    却会一路漂 —— 224x364 → 225x364 → 226x364 → … → 240x372，
  //    **只增不减，拖一次涨几像素**。
  //    原因是 Windows 分数 DPI 缩放（这台是 112.5%）下，Chromium 为了让
  //    透明窗口的物理边界对齐到整数像素，会微调它的 DIP 尺寸。
  //
  //    后果很隐蔽：窗口一变高，锚在【底部】的角色就被往下推，
  //    而锚在【顶部】的气泡不动 —— 看起来就是"气泡离人物越来越远"。
  //    显式给出尺寸，这个漂移就被按住了。
  win.setBounds({ x: Math.round(nx), y: Math.round(ny), width: winW, height: winH })
}

/* ══════════════════════════════════════════════════════════════
 * 动态点击穿透 —— **由主进程统一决策**
 *
 * 为什么要放主进程（这是 P0 第一条，之前一直是坏的）：
 *   原来渲染进程用 `setIgnoreMouseEvents(true, {forward:true})`，
 *   指望系统把 mousemove 转发进页面，再根据命中与否切换。
 *   但这个转发在部分机器上**根本不生效**（本机实测就不行）——
 *   渲染端永远收不到鼠标移动，于是：
 *     · 点空白处的穿透失效（桌宠一直挡着后面的图标）
 *     · 更糟的是角色变成点不动，只能靠看门狗整体关掉自动穿透
 *
 *   改成主进程每 50ms 主动问系统"光标现在在哪"（screen.getCursorScreenPoint），
 *   再拿渲染端上报的命中区自己判断 —— **不依赖任何转发，所以一定可用**。
 *
 * 命中区由渲染进程上报（它才知道角色画在哪、哪块是透明的）：
 *   rect —— 角色在窗口坐标系里的外接矩形
 *   mask —— 64×64 的 alpha 掩码，用来判断"是不是真在角色身上"，
 *           而不是"在角色周围的透明矩形里"
 * ══════════════════════════════════════════════════════════════ */
let hitRegion = null // { rect:{left,top,w,h}, mask:number[], maskSize:number }
let uiOpen = false // 菜单/面板展开中
let forceThrough = false // 用户手动开的强制穿透
let throughTimer = null
let lastIgnore = null

/** 光标现在是不是压在角色身上 */
function cursorOverPet() {
  if (!win || win.isDestroyed()) return true
  const r = hitRegion
  // 还没上报过命中区：保守放行（宁可接收事件，也不能让角色点不动）
  if (!r || !r.rect || !r.rect.w || !r.rect.h) return true

  const p = screen.getCursorScreenPoint()
  const [wx, wy] = win.getPosition()
  const nx = (p.x - wx - r.rect.left) / r.rect.w
  const ny = (p.y - wy - r.rect.top) / r.rect.h
  if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return false // 在矩形外

  const m = r.mask
  const size = r.maskSize || 0
  if (!m || !size) return true // 没有掩码就按矩形算
  const mx = Math.min(size - 1, Math.max(0, Math.floor(nx * size)))
  const my = Math.min(size - 1, Math.max(0, Math.floor(ny * size)))
  return (m[my * size + mx] || 0) >= 24
}

function startThroughWatch() {
  if (throughTimer) clearInterval(throughTimer)
  throughTimer = setInterval(() => {
    if (!win || win.isDestroyed()) return
    // 菜单/面板展开时不穿透（否则菜单项和按钮点不动，实测踩过）
    // 用户手动开强制穿透时也不自动切
    const ignore = forceThrough ? false : uiOpen ? false : !cursorOverPet()
    if (ignore !== lastIgnore) {
      lastIgnore = ignore
      win.setIgnoreMouseEvents(ignore, { forward: true })
    }
  }, 50)
}

/**
 * 真正退出应用。
 *
 * ⚠️ 窗口的 close 事件被改成了"隐藏"，所以**不能再用 app.quit() 直接退**——
 *    所有退出路径都得先把这个标记立起来，否则会被 close 拦下来变成隐藏。
 *    退出入口有三个：托盘第一项、Ctrl+Alt+Shift+X、以及 app 的退出流程。
 */
let reallyQuitting = false
function quitApp() {
  reallyQuitting = true
  app.quit()
}

/** 把窗口拉回屏幕底边（兜底：任何时候都能找回来） */
function bringHome() {
  if (!win || win.isDestroyed()) return;
  const { workArea } = screen.getPrimaryDisplay();
  const [w, h] = win.getSize();
  stopMoving();
  safeSetPosition(workArea.x + workArea.width - w - 40, workArea.y + workArea.height - h);
}
/** 停掉正在进行的漫游移动 */
function stopMoving() {
  if (moveTimer) {
    clearInterval(moveTimer);
    moveTimer = null;
  }
  if (win && !win.isDestroyed()) {
    win.webContents.send("pet:walk", { walking: false, dir: lastDir });
  }
}
/** 最近一次移动的方向，用来决定角色朝哪边 */
let lastDir = 1;
/**
 * 平滑移动到目标位置。
 * 用分帧小步移动，而不是一次 setPosition —— 否则看起来是瞬移。
 */
function glideTo(targetX, targetY, durationMs = 1600) {
  if (!win || win.isDestroyed()) return;
  stopMoving();
  const [sx, sy] = win.getPosition();
  const steps = Math.max(1, Math.round(durationMs / 16));
  let i = 0;
  lastDir = targetX >= sx ? 1 : -1;
  win.webContents.send("pet:walk", { walking: true, dir: lastDir });
  moveTimer = setInterval(() => {
    if (!win || win.isDestroyed()) {
      stopMoving();
      return;
    }
    i++;
    const t = i / steps;
    // ⚠️ 用【匀速】直线插值，不要用 easeInOutSine。
    //
    //    踩过的坑：原来这里是 `e = -(cos(PI*t)-1)/2`（起步慢、中间快、收尾慢），
    //    而腿是【恒定帧率】在迈 —— 窗口速度一直在变、步频却不变，
    //    两边的相对速度对不上，表现就是"脚在打滑、走路时快时慢"。
    //    匀速滑动才能让脚和地面的相对速度稳定。
    const e = t;
    const x = Math.round(sx + (targetX - sx) * e);
    const y = Math.round(sy + (targetY - sy) * e);
    safeSetPosition(x, y);
    if (i >= steps) {
      clearInterval(moveTimer);
      moveTimer = null;
      if (win && !win.isDestroyed()) {
        win.webContents.send("pet:walk", { walking: false, dir: lastDir });
      }
    }
  }, 16);
}
/** 挑一个屏幕内的新落点，然后走过去 */
function wanderOnce(force = false) {
  // force=true 是手动触发的「随便走走」，不受漫游开关限制
  if (!win || win.isDestroyed()) return
  if (!roaming && !force) return;
  const { workArea } = screen.getPrimaryDisplay();
  const [w, h] = win.getSize();
  const maxX = Math.max(workArea.x, workArea.x + workArea.width - w);
  const targetX = Math.round(workArea.x + Math.random() * (maxX - workArea.x));
  const targetY = Math.round(workArea.y + workArea.height - h);
  // ⚠️ 时长按【距离】算，不要随机。
  //    原来写的是 `1200 + Math.random() * 1400` —— 走得远、花的时间却可能更短，
  //    于是每次走路的速度都不一样。配上【恒定步频】的腿，就是"时快时慢、脚在打滑"。
  //    匀速 = 距离 ÷ 速度。
  const dist = Math.abs(targetX - win.getPosition()[0]);
  const SPEED = 260; // 屏幕像素 / 秒
  const ms = Math.max(600, Math.round((dist / SPEED) * 1000));
  console.log(
    `[桌宠] 溜达到 (${targetX}, ${targetY})  距离 ${Math.round(dist)}px  用时 ${ms}ms  ` +
      `当前尺寸 ${w}x${h}  屏幕工作区 ${workArea.width}x${workArea.height}`
  );
  glideTo(targetX, targetY, ms);
}
/** 每隔一段时间自动溜达一次（漫游开关打开时才走） */
function startRoaming() {
  if (roamTimer) clearInterval(roamTimer);
  roamTimer = setInterval(() => {
    if (!roaming || chatOpen) return;
    wanderOnce();
  }, 9e3);
}
let chatOpen = false;
/** 注册所有 IPC 处理函数 */
function setupIpc() {

  // ---- 会话 ----
  //
  // ⚠️ 三条都先判 runtime 是否装配好。以前这里是裸调用，一旦装配缺失
  //    （历史上真发生过：runtime 从未被赋值），渲染端只会收到一句
  //    "Cannot read properties of null"，界面上则是"点了没反应"，
  //    排查时完全看不出是主进程少了一行装配。这里让它**响亮地报错**。
  const noRuntime = { ok: false, error: 'runtime_not_ready' }

  ipcMain.handle('session:send', (_e, text, scenario) => {
    if (!runtime) return noRuntime
    runtime.run(String(text || ''), scenario || 'normal')
    return { ok: true }
  })

  ipcMain.handle('session:cancel', () => ({ ok: runtime ? runtime.cancel() : false }))
  ipcMain.handle('session:reconnect', () => {
    if (!runtime) return noRuntime
    runtime.reconnect()
    return { ok: true }
  })

  // ---- 窗口 ----
  // 点击穿透：让鼠标能点到桌宠"身后"的桌面图标
  /**
   * 手动开关"强制穿透"。
   *
   * ⚠️ 这里不再直接调 setIgnoreMouseEvents —— 穿透的最终决策权在主进程的
   *    轮询循环里（见 startThroughWatch）。这里只是改一个意图标记。
   */
  ipcMain.handle('win:set-ignore-mouse', (_e, ignore) => {
    forceThrough = !!ignore
    return { ok: true }
  })

  /** 渲染端上报命中区：角色当前画在窗口里的矩形 + 64×64 的 alpha 掩码 */
  ipcMain.handle('win:set-hit-region', (_e, r) => {
    hitRegion = r && r.rect ? r : null
    return { ok: true }
  })

  /** 菜单/聊天面板是否展开 —— 展开时不能穿透，否则菜单项和按钮点不动 */
  ipcMain.handle('win:set-ui-open', (_e, v) => {
    uiOpen = !!v
    return { ok: true }
  })

  /** 置顶开关（右键菜单里那个） */
  ipcMain.handle('win:set-always-on-top', (_e, on) => {
    if (!win || win.isDestroyed()) return { ok: false }
    win.setAlwaysOnTop(!!on, 'screen-saver')
    return { ok: true }
  })

  /**
   * 导出「经用户授权的偏好更新」。
   *
   * 任务书 §16.4 的交接要求是「向数据组提交**经用户授权的**偏好更新」。
   * 后端端点还没有，所以先落成文件 —— 这样"授权 → 导出"这条链现在就能走通、
   * 能演示，等数据组定了端点再把这一个函数换成网络发送，上层不用动。
   *
   * ⚠️ 授权判断在渲染进程（`petStats.js` 的 `exportForDataTeam()` 里也有一次），
   *    主进程不再二次判断 —— 但**渲染进程不传数据过来就什么都不会写**。
   */
  ipcMain.handle('pref:export', (_e, data) => {
    if (!data) return { ok: false, reason: '没有可导出的内容（未授权？）' }
    try {
      const fs = require('fs')
      const dir = path.join(app.getPath('desktop'), 'DeepProf_偏好更新')
      fs.mkdirSync(dir, { recursive: true })
      const stamp = new Date().toISOString().slice(0, 19).replace(/[:T]/g, '-')
      const out = path.join(dir, `偏好更新_${stamp}.json`)
      fs.writeFileSync(out, JSON.stringify(data, null, 2), 'utf8')
      console.log('[桌宠] 偏好更新已导出:', out)
      return { ok: true, path: out }
    } catch (err) {
      return { ok: false, reason: String(err.message || err).slice(0, 200) }
    }
  })

  ipcMain.handle('win:quit', () => {
    quitApp()
    return { ok: true }
  })

  // 用户拖动：按增量移动窗口。
  // 不用 CSS 的 -webkit-app-region: drag，因为那样元素就收不到 click 事件了，
  // 而我们需要"点一下"和"拖一下"两种操作共存。
  ipcMain.handle('win:move-by', (_e, dx, dy) => {
    if (!win || win.isDestroyed()) return { ok: false }
    const [x, y] = win.getPosition()
    safeSetPosition(x + dx, y + dy)
    return { ok: true }
  })

  /**
   * 拖动开始/结束：期间暂停漫游，免得跟用户抢窗口。
   *
   * ⚠️ 踩过的坑：原来是 `roaming = !dragging` —— 本意是"拖动时暂停、松手恢复"，
   *    但**恢复的是固定值 true**，而不是拖之前的状态。
   *    结果：**只要拖过小人一次，漫游就自动打开了**，哪怕用户从没开过。
   *    而漫游的要求是**默认关**（见《给 Claude 的总指令》北极星）。
   *    现在改成记住拖之前的值、松手原样还回去。
   */
  let roamingBeforeDrag = null
  ipcMain.handle('win:drag-state', (_e, dragging) => {
    const d = !!dragging
    if (d) {
      if (roamingBeforeDrag === null) roamingBeforeDrag = roaming
      roaming = false
      stopMoving()
    } else {
      // 没记录过就保持现状，别凭空把漫游打开
      roaming = roamingBeforeDrag === null ? roaming : roamingBeforeDrag
      roamingBeforeDrag = null
    }
    return { ok: true }
  })

  // 展开 / 收起聊天面板 —— 窗口跟着变大变小
  ipcMain.handle('win:set-chat-open', (_e, open) => {
    if (!win || win.isDestroyed()) return { ok: false }
    const next = !!open
    // ⚠️ 状态没变就【直接返回】，不要重算窗口位置。
    //    踩过的坑：双击小人会调 toggleChat(true)，而面板已经开着时
    //    这段会再算一次 `y = y - (620 - PET_H)` —— **每双击一次窗口就往上跳 200px**。
    //    表现就是"对话开着时点人物，窗口自己往上跑"。
    if (next === chatOpen) return { ok: true }
    chatOpen = next
    open = next
    const { workArea } = screen.getPrimaryDisplay()
    const [x, y] = win.getPosition()

    if (open) {
      const w = 380
      const h = 620
      // 保证展开后仍在屏幕内
      const nx = Math.min(x, workArea.x + workArea.width - w)
      const ny = Math.max(workArea.y, y - (h - PET_H))
      win.setBounds({ x: nx, y: ny, width: w, height: h })
    } else {
      win.setBounds({ x, y: y + (620 - PET_H), width: PET_W, height: PET_H })
    }
    // 同步"应有的尺寸"，safeSetPosition 移动窗口时要靠它
    winW = open ? 380 : PET_W
    winH = open ? 620 : PET_H
    return { ok: true }
  })

  // 手动开关漫游
  ipcMain.handle('win:set-roaming', (_e, on) => {
    roaming = !!on
    if (!roaming) stopMoving()
    return { ok: true }
  })

  // 立刻走一次
  ipcMain.handle('win:wander', () => {
    // ⚠️ 要传 force=true：菜单里的「随便走走」是【手动指令】，
    //    不该被漫游开关挡住。漫游开关管的是"自动溜达"，
    //    而 wanderOnce() 里 `if (!roaming) return` 会让手动点的那下毫无反应
    //    （菜单项却照常可点，用户只会觉得"点了没用"）。
    wanderOnce(true)
    return { ok: true }
  })

  // 把小人叫回屏幕底边 —— 兜底，任何时候都能找回来
  ipcMain.handle('win:home', () => {
    bringHome()
    return { ok: true }
  })

  /* ── 语音合成（神经语音，edge-tts）────────────────────────────────
   *
   * 为什么在主进程：渲染进程不该有起子进程、读写文件的能力
   * （contextIsolation + nodeIntegration:false 就是为了这个）。
   *
   * ⚠️ **失败一律不抛异常**，返回 { ok:false, reason }。
   *    前端据此退回 Chromium 内置的系统合成音 —— 对方机器上没装 Python、
   *    没装 edge-tts、或者没网，桌宠都必须还能说话，不能干脆哑掉。
   * ──────────────────────────────────────────────────────────────── */

  /**
   * 合成脚本；随工程走，不依赖全局安装。
   *
   * ⚠️ **打包后不能指向 __dirname**。生产构建里主进程代码在 `app.asar` 内，
   *    `__dirname` 就落在 asar 的虚拟路径里 —— 而 **Python 子进程读不了 asar**，
   *    `execFile` 会直接 ENOENT，装出来的 exe 语音**整个坏掉**（且是静默降级，
   *    只退回系统音，不容易发现是路径问题）。
   *
   *    所以 `tools/tts/` 通过 electron-builder 的 `extraResources` 复制到
   *    `<安装目录>/resources/tools/tts/`（asar 外），这里按 `process.resourcesPath` 找。
   */
  const sayPyPath = () => {
    const packaged = path.join(process.resourcesPath || '', 'tools', 'tts', 'say.py')
    if (app.isPackaged && require('fs').existsSync(packaged)) return packaged
    // 开发期（或 extraResources 没生效时的兜底）走源码目录
    return path.join(__dirname, '../../tools/tts/say.py')
  }
  /** 用哪个 python。允许用环境变量覆盖（有的机器是 py 或 python3） */
  const PY = process.env.DEEPPROF_PYTHON || 'python'

  /**
   * 跑一次 say.py。
   * 文本走**临时文件**而不是命令行参数：回复里可能有引号、换行，
   * 走命令行迟早被转义问题坑到。
   */
  const runSay = (args, timeout = 20000) =>
    new Promise((resolve) => {
      const { execFile } = require('child_process')
      const fs = require('fs')
      execFile(
        PY,
        [sayPyPath(), ...args],
        { timeout, windowsHide: true, encoding: 'utf8' },
        (err, stdout, stderr) => {
          const out = String(stdout || '').trim()
          if (err && !out.startsWith('ERR')) {
            // 连脚本都没跑起来（python 不在 PATH / 脚本路径不对）
            resolve({ ok: false, reason: (err.message || 'spawn failed').slice(0, 200) })
            return
          }
          if (out.startsWith('OK')) resolve({ ok: true, detail: out.slice(2).trim() })
          else resolve({ ok: false, reason: out.replace(/^ERR\s*/, '') || 'unknown' })
        }
      )
    })

  /** 自检：神经语音到底能不能用（界面上的"语音"诊断按钮会用到） */
  ipcMain.handle('tts:selftest', async () => {
    const r = await runSay(['--selftest'], 15000)
    if (!r.ok) console.warn('[桌宠] 神经语音不可用：', r.reason)
    else console.log('[桌宠] 神经语音可用')
    return r
  })

  ipcMain.handle('tts:synthesize', async (_e, text) => {
    const t = String(text || '').trim()
    if (!t) return { ok: false, reason: 'empty text' }
    // 太长的先截断：桌宠是一次对话，不是听书；也避免合成卡太久
    const clipped = t.length > 600 ? t.slice(0, 600) : t

    const fs = require('fs')
    const os = require('os')
    const stamp = `${Date.now()}_${Math.floor(Math.random() * 1e6)}`
    const txtPath = path.join(os.tmpdir(), `deepprof-tts-${stamp}.txt`)
    const mp3Path = path.join(os.tmpdir(), `deepprof-tts-${stamp}.mp3`)

    try {
      fs.writeFileSync(txtPath, clipped, 'utf8')
      const r = await runSay(['--text-file', txtPath, '--out', mp3Path])
      if (!r.ok) return { ok: false, reason: r.reason }

      const buf = fs.readFileSync(mp3Path)
      if (!buf.length) return { ok: false, reason: 'empty audio' }

      // 返回 data URL：渲染进程直接 new Audio(...) 就能播，不用碰文件系统。
      // ⚠️ 需要 CSP 里有 media-src data:，见 applyCsp()
      return { ok: true, engine: 'edge-tts', audio: `data:audio/mpeg;base64,${buf.toString('base64')}` }
    } catch (err) {
      return { ok: false, reason: String(err.message || err).slice(0, 200) }
    } finally {
      // 临时文件用完就删，别在用户机器上堆垃圾
      try {
        fs.unlinkSync(txtPath)
      } catch {}
      try {
        fs.unlinkSync(mp3Path)
      } catch {}
    }
  })
}

/* ══════════════════════════════════════════════════════════════
 * 单实例锁 —— **只允许跑一个桌宠**
 *
 * ⚠️ 为什么要加（今天踩得最狠的一个坑）：
 *    反复 npm run dev / 双击启动，会留下多个实例。它们的默认位置几乎重合，
 *    屏幕上看着就是"一个小人"，但**旧实例跑的是旧代码**。
 *    于是"我改了怎么没生效""拖动就变大"这类现象，全是叠影造成的假象 ——
 *    排查时会把时间全浪费在不存在的问题上。
 *
 *    加了锁之后：第二个实例直接退出，并把已有窗口叫回屏幕底边。
 * ══════════════════════════════════════════════════════════════ */
if (!app.requestSingleInstanceLock()) {
  console.log('[桌宠] 已经有一个实例在跑了，这次启动直接退出')
  app.quit()
} else {
  app.on('second-instance', () => {
    if (win && !win.isDestroyed()) {
      win.show()
      bringHome()
    }
  })
}

/* =========================================================================
 * MockRuntime 的装配与事件转发
 *
 * ⚠️ 这一段【曾经缺失过】——`let runtime = null` 从来没有被赋过值，
 *    而 session:send / cancel / reconnect 三个 handler 直接解引用它，
 *    结果点「正常回复」「重连」「取消」全都静默失败：
 *    渲染端 promise 直接 reject（Cannot read properties of null），
 *    事件日志始终是 0，界面上表现为"按钮点了没反应"。
 *
 *    编译产物 out/main/index.js 当时也带着同样的空洞，所以 dev 和 start 两种
 *    启动方式都坏。恢复时务必确认**两件事同时存在**：实例化 + event 转发。
 *    只加实例化不加转发，按钮会"看起来好了"但界面依然一动不动。
 * ========================================================================= */
function initRuntime() {
  runtime = new MockRuntime()

  // MockRuntime 是 EventEmitter，这里把它的 'event' 转发到渲染进程。
  // 这是【唯一】的事件出口 —— 见 docs/输入输出.md §〇：
  // 渲染进程只有 runtime:event 一条入口，不得另开专用推送通道。
  runtime.on('event', (evt) => {
    if (win && !win.isDestroyed()) win.webContents.send(EVENT_CHANNEL, evt)
  })
}

app.whenReady().then(() => {
  applyCsp()
  createWindow()
  startThroughWatch() // 动态点击穿透：主进程每 50ms 问一次系统光标位置

  createTray()
  initRuntime() // ⚠️ 必须在 setupIpc() 之前 —— 三个 session handler 都依赖它
  setupIpc()
  startRoaming()

  // 紧急出口：不管窗口跑到哪、状态多奇怪，按快捷键都能把小人叫回来或直接退出。
  // 上次小人跑出屏幕又没法关，就是因为唯一的出口（托盘）是坏的。
  // 注册失败不致命（可能被别的程序占了），托盘 + 右键菜单仍然是可靠的出口。
  const ACCEL_HOME = 'Control+Alt+Shift+D'
  const ACCEL_QUIT = 'Control+Alt+Shift+X'
  const okHome = globalShortcut.register(ACCEL_HOME, () => bringHome())
  const okQuit = globalShortcut.register(ACCEL_QUIT, () => quitApp())
  console.log(
    `[桌宠] 快捷键 ${ACCEL_HOME}(归位) ${okHome ? '✅' : '❌'}  ${ACCEL_QUIT}(退出) ${okQuit ? '✅' : '❌'}`
  )

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow()
  })
})

app.on('window-all-closed', () => {
  if (process.platform !== 'darwin') app.quit()
})

app.on('will-quit', () => {
  globalShortcut.unregisterAll()
})

/* =========================================================================
 * 四、性能实测探针（交付测量用；平时完全不参与运行）
 *
 * ── 为什么内联在这个文件里，而不是拆成 bench.js ──
 *   同文件头部的警告：electron-vite 打包主进程时会把相对路径的 require 原样保留，
 *   拆出去运行时就是 Cannot find module。这份探针只为交付时量一次数，
 *   不值得为它去冒打包踩坑的风险。
 *
 * ── 为什么用环境变量开关，而不是另写一个测试入口 ──
 *   要测的是**这个应用**的数字：真实窗口、真实渲染进程、真实 Pixi 管线。
 *   另起一个测试专用入口测出来的数不是交付物的数，评审时站不住。
 *   所以做法是「同一份代码 + 环境变量」——不设变量时 runBench() 立刻 return，
 *   连一个定时器都不会多建。
 *
 * ── 开关 ──
 *   DEEPPROF_BENCH_OUT=<文件路径>   启用，跑完把原始数据写成 JSON
 *   DEEPPROF_BENCH_EXIT=1           跑完自动退出（给外部 harness 用）
 * ========================================================================= */

/** 性能探针是否启用 */
const BENCH_ON = !!process.env.DEEPPROF_BENCH_OUT

/**
 * 主进程的真实出生时刻（epoch ms）。
 * process.uptime() 是从**进程被创建**开始算的，所以在模块加载瞬间反推回去，
 * 拿到的就是 Electron 主进程的出生时间 —— 这比"harness 按下回车的时刻"更接近
 * 冷启动的真正起点（不含 npm / electron-vite 外壳的开销）。
 */
const BENCH_PROC_START = Date.now() - process.uptime() * 1000

/** 全文件共用的启动时间轴锚点 */
const BENCH_T = {
  process_start: BENCH_PROC_START,
  module_load: Date.now(),
  app_ready: null,
  window_shown: null, // did-finish-load，也就是项目自己打印「窗口已显示」的那一刻
  react_mounted: null
}

/**
 * 主进程侧的统一时间读数。
 * 用 timeOrigin + now() 而不是 Date.now() —— 渲染进程里同样是这套算法，
 * 两边算出来的才是同一根时间轴上的数，可以直接相减；Date.now() 只有整数毫秒。
 */
function benchNow() {
  return performance.timeOrigin + performance.now()
}

function benchLog(key, value) {
  const v = value !== null && typeof value === 'object' ? JSON.stringify(value) : String(value)
  console.log(`[bench] ${key} = ${v}`)
}

/**
 * 注入渲染进程的探针。
 *
 * 为什么是这套办法：
 *   Pixi 的 Application 实例藏在 RigPet 的 useEffect 闭包里（const app = new PIXI.Application…），
 *   没有挂到 window 上，主进程够不着，所以 app.ticker.FPS 读不到。
 *   但"渲染有没有在跑"不需要问 Pixi —— 它每帧要画就必须调用 WebGL 的绘制入口，
 *   把 gl 的原型方法包一层，就能数出它**真实提交了多少次绘制**。
 *   这是硬证据，比读一个自报的 FPS 数字更可信。
 *
 * 同时装一个 MutationObserver：
 *   用来测「主进程发出事件 → 界面 DOM 真的变了 → 下一帧画出来」这条链路。
 *   只监听 childList / characterData，不监听 attributes ——
 *   Pixi 的 canvas 会被 resizeTo 改样式属性，那是噪声，不是我们要的事件响应。
 */
const BENCH_INJECT = `
(() => {
  if (window.__bench) return 'already'
  // 四个绘制入口分开记数，不合并 —— 合并了就说不清"每帧几笔"，
  // 而每帧几笔正好能反过来说明 Pixi 有没有在真的重画（而不是画面冻住了计数器在空转）
  const S = { draws: 0, clear: 0, de: 0, da: 0, dei: 0, dai: 0 }
  window.__bench = S

  const wrap = (proto, name, key) => {
    if (!proto || typeof proto[name] !== 'function') return
    const orig = proto[name]
    proto[name] = function (...args) {
      S.draws++
      S[key]++
      return orig.apply(this, args)
    }
  }
  const protos = [
    window.WebGLRenderingContext && window.WebGLRenderingContext.prototype,
    window.WebGL2RenderingContext && window.WebGL2RenderingContext.prototype
  ].filter(Boolean)
  protos.forEach((p) => {
    // 覆盖 Pixi 可能走的全部绘制入口，漏一个就会把帧率数少
    wrap(p, 'drawElements', 'de')
    wrap(p, 'drawArrays', 'da')
    wrap(p, 'drawElementsInstanced', 'dei')
    wrap(p, 'drawArraysInstanced', 'dai')
    if (p.clear) {
      const origClear = p.clear
      p.clear = function (...args) { S.clear++; return origClear.apply(this, args) }
    }
    // 注：一开始还想顺带数 bindTexture / useProgram 来解释"为什么 3 层精灵
    // 只花了 1 次绘制"，但两个计数器实测都读回 0 —— 说明 Pixi 没走这两个入口
    // （或者走了缓存引用绕开了原型）。数不准的东西就不放进数据包，免得被追问。
  })

  /** 按秒采样：这一秒里 rAF 回调了几次、GL 画了几笔 */
  window.__benchSample = (ms) => new Promise((resolve) => {
    const b = { draws: S.draws, clear: S.clear, de: S.de, da: S.da, dei: S.dei, dai: S.dai }
    const perSecond = []
    let frames = 0
    let lastF = 0
    let last = b
    const t0 = performance.now()
    const loop = () => { frames++; requestAnimationFrame(loop) }
    requestAnimationFrame(loop)
    const iv = setInterval(() => {
      perSecond.push({
        t: +((performance.now() - t0) / 1000).toFixed(2),
        raf: frames - lastF,
        draws: S.draws - last.draws,
        clears: S.clear - last.clear
      })
      lastF = frames
      last = { draws: S.draws, clear: S.clear }
    }, 1000)
    setTimeout(() => {
      clearInterval(iv)
      const dur = (performance.now() - t0) / 1000
      const canvas = document.querySelector('canvas')
      const tk = window.PIXI && window.PIXI.Ticker ? window.PIXI.Ticker.shared : null
      resolve({
        seconds: +dur.toFixed(3),
        raf_frames: frames,
        raf_fps: +(frames / dur).toFixed(2),
        draw_calls: S.draws - b.draws,
        draw_calls_per_sec: +((S.draws - b.draws) / dur).toFixed(1),
        draws_per_frame: +((S.draws - b.draws) / frames).toFixed(3),
        gl_clears: S.clear - b.clear,
        gl_clears_per_sec: +((S.clear - b.clear) / dur).toFixed(1),
        break_down: {
          drawElements: S.de - b.de,
          drawArrays: S.da - b.da,
          drawElementsInstanced: S.dei - b.dei,
          drawArraysInstanced: S.dai - b.dai
        },
        per_second: perSecond,
        /**
         * ⚠️ 这两个字段是留给 README 说明「为什么 app.ticker.FPS 没测到」的。
         * 本项目用的是 Application 自建的 ticker（RigPet.jsx 里 new PIXI.Application），
         * ⚠️ 本段是喂给 executeJavaScript 的字符串，注释里【不能出现反引号】，会截断模板字符串
         * 不是 Ticker.shared；shared 这个从来没 started，
         * 它的 FPS 只是构造时的默认值 60，**不是测出来的**，别当数据用。
         */
        pixi_shared_ticker: tk ? { started: tk.started, FPS: tk.FPS, is_default_value: !tk.started } : null,
        canvas: canvas ? { w: canvas.width, h: canvas.height, css_w: canvas.clientWidth, css_h: canvas.clientHeight } : null,
        device_pixel_ratio: window.devicePixelRatio,
        react_root_html_len: (document.getElementById('root') || {}).innerHTML?.length ?? -1
      })
    }, ms)
  })

  /**
   * 装一次事件延迟探针。
   * 返回两个 promise：
   *   __benchP1  第一次 DOM 变化（以及紧随其后的那一帧）的时刻
   *   __benchP2  countMs 之内的 DOM 变化总次数 —— 用来量流式文本的刷新密度
   */
  /**
   * IPC 往返耗时 —— 完全在渲染进程自己的时钟里量。
   *
   * 为什么要有这个数：
   *   跨进程的端到端延迟必须做时钟校正，校正本身有误差；
   *   而这个数（渲染层发起 → 主进程处理 → 渲染层拿到回包）首尾都在同一个进程，
   *   一把尺子量到底，**不需要任何校正**，是整个"延迟"话题里最经得起追问的一个数。
   *
   * 顺带说明：这里没有去旁路订阅 onEvent 来掐"事件到达渲染层"的时刻。
   * 试过，加第二个订阅者之后 Electron 会在跑几十秒后无声退出（探针本身的副作用），
   * 所以退回到不碰事件通道的做法。
   */
  window.__benchRoundTrip = async (n) => {
    const out = []
    for (let i = 0; i < n; i++) {
      const t0 = performance.now()
      try { await window.deepprof.sendMessage('__bench_roundtrip__', 'loading') } catch (e) {}
      out.push(+(performance.now() - t0).toFixed(2))
      await new Promise((r) => setTimeout(r, 120))
    }
    return out
  }

  /**
   * 长任务观测器（>50ms 的主线程阻塞）。
   *
   * 为什么要它：实测发现**第 1 轮**的端到端延迟稳定地是 ~500ms，
   * 而第 2~8 轮只有 ~1ms。光看延迟数字只能猜"是不是哪里卡了"，
   * 有了 longtask 就能直接指出"这几毫秒里主线程被谁占住了"——
   * 数据里带一条自证，比在报告里写一句"可能是首次开销"强得多。
   */
  window.__benchLong = []
  if (!window.__benchLongObs) {
    try {
      window.__benchLongObs = new PerformanceObserver((list) => {
        for (const e of list.getEntries()) {
          window.__benchLong.push({
            start_rel_ms: +e.startTime.toFixed(1),
            dur_ms: +e.duration.toFixed(1),
            name: e.name,
            container: e.attribution && e.attribution[0] ? e.attribution[0].containerType : null
          })
        }
      })
      window.__benchLongObs.observe({ entryTypes: ['longtask'] })
    } catch (e) { window.__benchLong = null }
  }

  window.__benchArm = (countMs) => {
    window.__benchLong = window.__benchLong ? [] : null
    const win = countMs || 3000
    let count = 0
    let firstMut = null
    let firstRaf = null
    let resolveFirst
    let resolveDone
    const p1 = new Promise((r) => { resolveFirst = r })
    const p2 = new Promise((r) => { resolveDone = r })
    const obs = new MutationObserver(() => {
      count++
      if (firstMut === null) {
        firstMut = performance.timeOrigin + performance.now()
        // 顺手把此刻气泡里的字抓下来：这是**证据**，用来证明"测到的这次 DOM 变化
        // 确实是我们发的事件引起的"。没有它，万一某个无关的 DOM 抖动先发生，
        // 量出来的延迟就是假的 —— 而我们自己看不出来。
        const bubble = document.querySelector('.pet-bubble')
        const evidence = {
          t_mut: firstMut,
          bubble_present: !!bubble,
          bubble_text: bubble ? bubble.textContent.trim().slice(0, 24) : null,
          log_rows: document.querySelectorAll('.log-row').length
        }
        requestAnimationFrame(() => {
          firstRaf = performance.timeOrigin + performance.now()
          resolveFirst(Object.assign({ t_raf: firstRaf }, evidence))
        })
      }
    })
    obs.observe(document.body, { childList: true, subtree: true, characterData: true })
    // 保险丝：万一这一轮什么 DOM 都没动，不能让主进程一直挂着
    const fuse = setTimeout(() => { resolveFirst(null); }, win)
    setTimeout(() => {
      obs.disconnect()
      clearTimeout(fuse)
      resolveDone({ mutations: count, window_ms: win })
    }, win)
    window.__benchP1 = p1
    window.__benchP2 = p2
    return 'armed'
  }
  window.__benchAwaitP1 = () => (window.__benchP1 ? window.__benchP1 : Promise.resolve(null))
  window.__benchAwaitP2 = () => (window.__benchP2 ? window.__benchP2 : Promise.resolve(null))
  return 'ok'
})()
`

/**
 * 跑完一整套实测。只有 DEEPPROF_BENCH_OUT 存在时才做任何事。
 *
 * 时间轴安排（相对 did-finish-load）：
 *   0s    取第一份内存快照（此时 React 刚挂载，Pixi 贴图还在异步加载）
 *   0s    注入探针
 *   8s    取第二份内存快照 + 采 5 秒帧率
 *   14s   开始事件延迟的 6 轮测量
 *   …     写结果、可选退出
 * 之所以错开：主进程自带的启动自检（截图 @0s/2.5s）会真实占 CPU，
 * 跟它们抢在同一时刻测出来的帧率不是应用稳态的数字。
 */
async function runBench() {
  if (!BENCH_ON) return
  BENCH_T.app_ready = benchNow()

  const sleep = (ms) => new Promise((r) => setTimeout(r, ms))
  const result = {
    meta: {
      generated_at: new Date().toISOString(),
      host: {
        platform: process.platform,
        arch: process.arch,
        os: require('os').release(),
        cpus: require('os').cpus().length,
        cpu_model: require('os').cpus()[0] ? require('os').cpus()[0].model : null,
        total_mem_gb: +(require('os').totalmem() / 1024 / 1024 / 1024).toFixed(2)
      },
      electron: process.versions.electron,
      chrome: process.versions.chrome,
      node: process.versions.node,
      display: null,
      mode: process.env.ELECTRON_RENDERER_URL ? 'dev (vite dev server)' : 'prod (out/ 打包产物)',
      // 被测源码的指纹。⚠️ 测量期间团队其他人还在改渲染层代码，
      // 不记指纹的话，"这数是什么版本测的"将永远说不清。
      source_hashes: (() => {
        try {
          const fs = require('fs')
          const crypto = require('crypto')
          const root = path.join(__dirname, '..', '..', 'src')
          const walk = (dir, acc) => {
            for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
              const fp = path.join(dir, e.name)
              if (e.isDirectory()) walk(fp, acc)
              else if (/\.(js|jsx|css|json)$/.test(e.name)) {
                acc[e.name] = crypto.createHash('sha256').update(fs.readFileSync(fp)).digest('hex').slice(0, 12)
              }
            }
            return acc
          }
          return walk(root, {})
        } catch (e) { return { error: e.message } }
      })()
    },
    timeline: null,
    memory: [],
    fps: null,
    latency: [],
    stream_mutations: [],
    gpu: null,
    notes: []
  }

  try {
    const d = screen.getPrimaryDisplay()
    result.meta.display = {
      size: d.size,
      work_area: d.workAreaSize,
      scale_factor: d.scaleFactor,
      // 刷新率是判读帧率的参照系：rAF 只能跑到屏幕刷新率，跑满了就是满帧
      display_frequency_hz: d.displayFrequency || null
    }
  } catch (err) {
    result.notes.push('读取显示器信息失败: ' + err.message)
  }

  // 探针要在渲染进程侧监听 did-finish-load —— 此刻窗口已经建好、正在加载，
  // 事件还没触发（它是异步的），所以这里挂完全来得及
  if (win && !win.isDestroyed()) {
    win.webContents.once('did-finish-load', () => {
      BENCH_T.window_shown = benchNow()
    })
  }

  // 等窗口显示（最多 60 秒，够慢机器用了）
  const waitStart = Date.now()
  while (!BENCH_T.window_shown && Date.now() - waitStart < 60000) await sleep(50)
  if (!BENCH_T.window_shown) {
    result.notes.push('60 秒内没有等到 did-finish-load，启动时间未能记录')
  }

  const rel = (t) => (t === null ? null : +(t - BENCH_T.process_start).toFixed(1))
  result.timeline = {
    process_start_epoch: +BENCH_T.process_start.toFixed(1),
    module_load_after_start_ms: +(BENCH_T.module_load - BENCH_T.process_start).toFixed(1),
    app_ready_after_start_ms: rel(BENCH_T.app_ready),
    // 主进程"从自己出生到窗口显示"的全过程，不含 npm / electron-vite 外壳
    window_shown_after_start_ms: rel(BENCH_T.window_shown)
  }

  // 立刻把启动时间打到 stdout：冷启动 harness 只看这一行就能收工，
  // 不用等后面那套 60 秒的稳态测量跑完，也就不用为了量启动而白等一分钟
  console.log(
    '[bench] window_shown_ms = ' + rel(BENCH_T.window_shown) +
    '  process_start_epoch = ' + result.timeline.process_start_epoch
  )

  /**
   * 内存快照。
   * 用 app.getAppMetrics() 而不是外部 Get-Process —— 它自带进程角色标注
   * （Browser = 主进程 / Tab = 渲染进程 / GPU / Utility），
   * 正是"主进程和渲染进程分别测"需要的口径；外部只能拿到一堆同名的 electron 进程。
   * 单位：Electron 给的是 KB，这里统一换成 MB。
   */
  const snapMem = (tag) => {
    try {
      const m = app.getAppMetrics()
      const processes = m.map((p) => ({
        pid: p.pid,
        type: p.type,
        working_set_mb: +(p.memory.workingSetSize / 1024).toFixed(1),
        peak_working_set_mb: +(p.memory.peakWorkingSetSize / 1024).toFixed(1)
      }))
      result.memory.push({
        tag,
        at_ms: +(benchNow() - BENCH_T.process_start).toFixed(1),
        processes,
        total_working_set_mb: +processes.reduce((a, p) => a + p.working_set_mb, 0).toFixed(1)
      })
    } catch (err) {
      result.notes.push('内存快照失败(' + tag + '): ' + err.message)
    }
  }

  const runJS = (code) => win.webContents.executeJavaScript(code)
  // 每一步都留痕：探针跑挂了的时候，"卡在哪一步"比"报了什么错"更有用
  const step = (s) => console.log('[bench] step: ' + s)

  // ── 0s：第一份内存快照 + 注入探针 ──
  step('snapMem t0')
  snapMem('t0_窗口刚显示')
  let injected = null
  try {
    step('inject')
    injected = await runJS(BENCH_INJECT)
    step('inject ok: ' + injected)
  } catch (err) {
    result.notes.push('探针注入失败: ' + err.message)
    step('inject failed: ' + err.message)
  }

  // React 挂载完成的判据：root 里有内容
  step('mount-check')
  try {
    const mounted = await runJS(`(document.getElementById('root')||{}).innerHTML?.length || 0`)
    BENCH_T.react_mounted = benchNow()
    result.timeline.react_mounted_after_start_ms = rel(BENCH_T.react_mounted)
    result.timeline.root_html_len_at_mount_check = mounted
  } catch (err) {
    result.notes.push('React 挂载探测失败: ' + err.message)
  }

  step('mount-check done')
  result.probe_injected = injected
  result.gpu = (() => {
    try { return app.getGPUFeatureStatus() } catch (e) { return null }
  })()

  // ── 8s：稳态内存 + 帧率 ──
  // IPC 往返（渲染层自测，无跨进程校正）
  step('ipc roundtrip')
  try {
    result.ipc_roundtrip = await runJS('window.__benchRoundTrip(12)')
    step('ipc roundtrip done')
  } catch (err) {
    result.notes.push('IPC 往返测量失败: ' + err.message)
  }

  step('sleep 8s')
  await sleep(8000)
  snapMem('t8s_稳态')
  step('fps sample start')
  try {
    result.fps = await runJS('window.__benchSample(5000)')
    step('fps sample done')
  } catch (err) {
    result.notes.push('帧率采样失败: ' + err.message)
  }

  /**
   * 主进程时钟 ↔ 渲染进程时钟的固定偏差（NTP 式对时）。
   *
   * ⚠️ 这是实测踩到的坑，不校正的话延迟会算出**负数**：
   *   两边的 performance.timeOrigin 各自在进程启动时由系统时钟换算而来，
   *   主进程和渲染进程的启动时刻差了 400ms 左右，换算出来的起点就带上了
   *   1~4ms 的固定偏差。不校正就跨进程相减，等于拿两把没对齐的尺子量长度。
   *
   * 做法：主进程记 t1 → 渲染进程报自己的时刻 tR → 主进程记 t2。
   *       tR - (t1+t2)/2 就是两把尺子的零点差（假设来回链路基本对称）。
   * 多采几次取中位数，把调度抖动滤掉。
   */
  async function calibrateClock() {
    const samples = []
    for (let i = 0; i < 7; i++) {
      const t1 = benchNow()
      const tR = await runJS('performance.timeOrigin + performance.now()')
      const t2 = benchNow()
      samples.push(tR - (t1 + t2) / 2)
      await sleep(15)
    }
    samples.sort((a, b) => a - b)
    return +samples[Math.floor(samples.length / 2)].toFixed(3)
  }

  // ── 事件延迟：6 轮 ──
  await sleep(1500)
  snapMem('t15s_延迟测量前')
  const QUESTIONS = [
    '帮我讲讲数据库第三范式',
    '什么是函数依赖',
    '候选键怎么找',
    '什么是传递依赖',
    'BCNF 和 3NF 差在哪',
    '为什么要做范式分解',
    '什么时候该反范式',
    '范式越高是不是越好'
  ]
  for (let i = 0; i < QUESTIONS.length; i++) {
    try {
      // 上一轮的朗读还在响的话先掐掉 —— 语音合成会真占 CPU，会污染下一轮的读数
      await runJS('(() => { try { speechSynthesis.cancel() } catch (e) {} return 1 })()')
      // 每轮都重新对一次时：机器负载变化会让偏差漂，一轮一对最稳
      step('calibrate round ' + (i+1))
      const skew = await calibrateClock()
      step('armed')
      await runJS('window.__benchArm(3000)')
      // 主进程侧的发送时刻。runtime.run() 里 session.started 是同步 emit、
      // 同步 webContents.send 出去的，所以这个时间戳跟"真正发出去"差不到 1ms
      const t0 = benchNow()
      runtime.run(QUESTIONS[i], 'normal')
      step('await p1')
      const first = await runJS('window.__benchAwaitP1()')
      step('p1 ok')
      const count = await runJS('window.__benchAwaitP2()')
      // 这一轮里渲染进程主线程被占住超过 50ms 的片段
      let longs = null
      try { longs = await runJS('window.__benchLong') } catch (e) { longs = null }
      // 校正：渲染进程的时刻 - 零点偏差 = 换算到主进程时间轴上的时刻
      const domMs = first ? +(first.t_mut - skew - t0).toFixed(2) : null
      const frameMs = first ? +(first.t_raf - skew - t0).toFixed(2) : null
      result.latency.push({
        round: i + 1,
        question: QUESTIONS[i],
        clock_skew_ms: skew,
        t_send: +t0.toFixed(2),
        // 原始读数也留着：万一有人怀疑校正算错了，能拿原始数自己复算一遍
        raw_t_dom_mutation: first ? +first.t_mut.toFixed(2) : null,
        raw_t_first_frame: first ? +first.t_raf.toFixed(2) : null,
        send_to_dom_ms: domMs,
        send_to_frame_ms: frameMs,
        // 证据字段：这一轮测到的 DOM 变化，界面上到底变成了什么
        evidence_bubble_present: first ? first.bubble_present : null,
        evidence_bubble_text: first ? first.bubble_text : null,
        long_tasks: longs
      })
      if (count) result.stream_mutations.push({ round: i + 1, ...count })
    } catch (err) {
      result.notes.push(`延迟测量第 ${i + 1} 轮失败: ` + err.message)
    }
    // 一轮流式回复大约 1 秒跑完，留足时间让它回到 idle 再测下一轮，
    // 否则"上一轮还没结束"会把下一轮的 DOM 变化吃掉
    await sleep(6000)
  }

  // ── 流式回复期间再采一次帧率 ──
  // 这是桌宠最重的时刻：React 每 30ms 改一次气泡文字，同时 TTS 在合成语音。
  // 稳态空闲帧率好看不能说明问题，要在这个负载下也不掉帧才算数。
  try {
    await runJS('(() => { try { speechSynthesis.cancel() } catch (e) {} return 1 })()')
    step('streaming fps start')
    const streaming = runJS('window.__benchSample(4000)')
    runtime.run('性能采样：讲一下数据库范式', 'normal')
    result.fps_streaming = await streaming
  } catch (err) {
    result.notes.push('流式期间帧率采样失败: ' + err.message)
  }

  snapMem('t60s_测量结束')

  // 结果文件落在 out/ 外面，避免下次 build 把数据洗掉
  try {
    require('fs').writeFileSync(process.env.DEEPPROF_BENCH_OUT, JSON.stringify(result, null, 2), 'utf8')
    console.log('[bench] RESULT_WRITTEN ' + process.env.DEEPPROF_BENCH_OUT)
  } catch (err) {
    console.error('[bench] 结果写入失败: ' + err.message)
  }

  // 摘要打到 stdout，外部 harness 不用解析 JSON 也能立刻看到关键数
  const tl = result.timeline
  benchLog('timeline', { app_ready_ms: tl.app_ready_after_start_ms, window_shown_ms: tl.window_shown_after_start_ms })
  if (result.fps) {
    benchLog('fps_idle', {
      raf_fps: result.fps.raf_fps,
      draws_per_sec: result.fps.draw_calls_per_sec,
      draws_per_frame: result.fps.draws_per_frame,
      clears_per_sec: result.fps.gl_clears_per_sec
    })
  }
  if (result.fps_streaming) {
    benchLog('fps_streaming', {
      raf_fps: result.fps_streaming.raf_fps,
      draws_per_frame: result.fps_streaming.draws_per_frame
    })
  }
  const ok = result.latency.filter((l) => l.send_to_dom_ms !== null)
  if (ok.length) {
    const med = (xs) => {
      const s = xs.slice().sort((a, b) => a - b)
      return s[Math.floor(s.length / 2)]
    }
    const e2e = ok.map((l) => l.send_to_dom_ms)
    benchLog('latency_ms', {
      n: ok.length,
      end_to_end_min: Math.min(...e2e),
      end_to_end_median: med(e2e),
      end_to_end_max: Math.max(...e2e),
      clock_skew_samples: ok.map((l) => l.clock_skew_ms),
      ipc_roundtrip_median: result.ipc_roundtrip ? med(result.ipc_roundtrip) : null
    })
  }
  console.log('[bench] DONE')

  if (process.env.DEEPPROF_BENCH_EXIT) {
    await sleep(300)
    app.quit()
  }
}

// 追加在文件末尾：此时上面那个 whenReady 已经先把窗口、托盘、IPC 都建好了，
// 这里的回调排在它后面执行，能直接拿到 win 和 runtime
app.whenReady().then(() => {
  runBench().catch((err) => {
    console.error('[bench] 探针异常: ' + (err && err.stack ? err.stack : err))
    if (process.env.DEEPPROF_BENCH_OUT) {
      try {
        require('fs').writeFileSync(
          process.env.DEEPPROF_BENCH_OUT,
          JSON.stringify({ error: String(err && err.message), stack: String(err && err.stack) }, null, 2),
          'utf8'
        )
      } catch (e) { /* 写不进去就只留 stdout */ }
    }
    if (process.env.DEEPPROF_BENCH_EXIT) app.quit()
  })
})
