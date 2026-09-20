import { useEffect, useRef } from 'react'
import './live2dSetup' // ⚠️ 必须在 pixi 之前，install(@pixi/unsafe-eval) 在这里执行
import * as PIXI from 'pixi.js'
import { buildAlphaMask, hitTest } from './hitTest'
import idleUrl from './assets/pet/idle.png'
import bodyUrl from './assets/pet/rig/body.png'
import legLeftUrl from './assets/pet/rig/leg_left.png'
import legRightUrl from './assets/pet/rig/leg_right.png'
import rig from './assets/pet/rig/rig.json'

/**
 * 分层 rig 桌宠播放器 —— 单张立绘拆成「身体 / 左腿 / 右腿」三层，代码驱动走路。
 *
 * 和 FramePet 的关系：
 *   FramePet   逐帧换图。素材到位时效果最好，但要 AI 出 4 张一致的走路图，靠运气。
 *   RigPet     三层 PNG + 旋转。只要一张图，腿怎么摆由代码算，零一致性风险。
 *   两条路并存，用右键菜单里的「分层走路 / 逐帧走路」现场对比，哪条好用哪条。
 *
 * 层的 z 序（从下到上）：腿 → 身体。
 * 身体的裙子盖住腿的顶部，所以腿绕髋点转起来不会露出接缝 —— 这是拆腿时
 * 故意让身体层多保留十几像素大腿（inset）换来的，详见 拆腿.py 的说明。
 *
 * props 与 FramePet 保持一致，App.jsx 里可以无缝互换：
 *   pose / dir / pulse / talking / onTap / onHoverChange
 */

/** 髋点、画布尺寸等全部来自 拆腿.py 导出的 rig.json，别手抄坐标 */
const HIP_L = rig.leg_left
const HIP_R = rig.leg_right
const CW = rig.canvas_w // 画布宽（贴图原始尺寸）
const CH = rig.canvas_h // 画布高

/**
 * 角色的显示高度（像素）——**固定值，不跟着窗口和布局变**。
 *
 * ⚠️ 踩过的坑：原来纯按宿主尺寸缩放（min(w/CW, h/CH)），
 *    而宿主的尺寸是会变的 —— `.pet-area{flex:1}` + `.pet-host{height:100%}`，
 *    气泡一出现/消失就会挤动 flex 布局，高度一变角色就跟着放大缩小。
 *    窗口在漫游时也会改尺寸。表现出来就是「**走完会变大**」。
 *    桌宠的体型必须是稳定的，只在宿主真的装不下时才缩小。
 */
const IDEAL_H = 292

/**
 * 走路参数 —— 想调手感改这几个。
 * ⚠️ 单位是【屏幕像素】，不是画布像素。踩过坑：一开始按画布像素写，
 *    而画布被缩放到 0.29 倍，所以 6px 的起伏到屏幕上只有 1.7px，几乎看不见，
 *    结果就是"腿在动、身体僵着"—— 看起来非常诡异。别改单位。
 */
// ⚠️ 摆幅从 9 降到 4：正视视角下腿摆大了很诡异 ——
//    腿是刚体旋转，脚会跟着转；而且正面**根本看不到前后迈步**。
//    正面能读出"在走"的其实是【重心左右移动 + 整体起伏 + 摇摆】，不是腿。
//    把这里设成 0 可以彻底关掉腿部动作，只留起伏和重心移动。
const WALK_SWING_DEG = 4
const WALK_BOB_PX = 7 // 整体上下起伏（屏幕像素）—— 跟着腿一起抬，像在迈步
const WALK_TILT_DEG = 6.5 // 整体左右摇摆
const WALK_SHIFT_PX = 4 // 重心左右移动（屏幕像素）—— 这一项比腿管用得多
const WALK_CYCLES_PER_SEC = 0.72 // 慢一点更稳；原来 0.9 显得慌

const textureCache = new Map()
function getTexture(url) {
  if (!textureCache.has(url)) textureCache.set(url, PIXI.Texture.from(url))
  return textureCache.get(url)
}

export default function RigPet({
  action = 'idle',
  asleep = false,
  scale = 1, // 用户滚轮调的缩放倍率
  dir = 1,
  pulse = 0,
  talking = false,
  onTap,
  onHoverChange
}) {
  const hostRef = useRef(null)
  const pulseRef = useRef({ start: 0, active: false })

  const actionRef = useRef(action)
  const dirRef = useRef(dir)
  const sleepRef = useRef(asleep)
  const scaleRef = useRef(scale)
  actionRef.current = action
  dirRef.current = dir
  sleepRef.current = asleep
  scaleRef.current = scale

  const rectRef = useRef(null)
  const maskRef = useRef(null)
  const hoverRef = useRef(false)
  const hoverCbRef = useRef(onHoverChange)
  hoverCbRef.current = onHoverChange

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    const app = new PIXI.Application({
      backgroundAlpha: 0,
      antialias: true,
      // ⚠️ 固定 1，不要用 devicePixelRatio。
      //    这个组件每帧要合成【三张】642×1024 的贴图（FramePet 只有一张），
      //    DPR 1.5~2 会让填充开销翻好几倍。而角色源图是 642×1024、
      //    屏幕上只显示约 183×292，**本来就在大幅缩小**，
      //    用 DPR 1 是把多余的像素丢掉，肉眼一点损失都没有。
      //    这是"漫游时掉帧"最主要的一处开销。
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
      resizeTo: host
    })
    // ⚠️ **不要**给 ticker 设 maxFPS。
    //
    // 踩过的坑：为了让漫游时省点开销，这里本来设了 maxFPS = 30。
    // 但主进程的 glideTo() 是**每 16ms（约 60Hz）移动一次窗口**的 ——
    // 窗口一直在动、角色却每 33ms 才更新一次，两个节奏对不上，
    // 看起来就是**一直在抽搐**。省下来的那点开销远不值这个观感。
    //
    // 真正的开销优化是上面的 resolution: 1（每帧少画好几倍的像素），
    // 那个才是"漫游掉帧"的主因。帧率交给系统按刷新率跑。
    host.appendChild(app.view)

    /* ── 窗口不可见时暂停渲染循环（性能红线）──
     * 这个组件每帧合成三张贴图，后台空转的开销比 FramePet 更大，更该停。
     * ──────────────────────────────────────────── */
    const onVis = () => (document.hidden ? app.ticker.stop() : app.ticker.start())
    document.addEventListener('visibilitychange', onVis)
    onVis()

    // ── 三层：腿在下、身体在上 ──
    const root = new PIXI.Container()
    // 枢轴放在画布底边中点 → 和 FramePet 的 anchor(0.5, 1) 同一套定位逻辑，
    // 两个组件切换时角色的站位不会跳
    root.pivot.set(CW / 2, CH)
    app.stage.addChild(root)

    const legL = new PIXI.Sprite(getTexture(legLeftUrl))
    const legR = new PIXI.Sprite(getTexture(legRightUrl))
    const body = new PIXI.Sprite(getTexture(bodyUrl))

    /**
     * 让精灵绕画布上的某一点旋转。
     * PIXI 的 sprite.pivot 是「相对贴图左上角」的旋转中心，
     * 而 sprite.position 决定这个中心落在父容器坐标系的哪里 ——
     * 所以两者设成同一个值，精灵就停在原位、绕那个点转。
     */
    const pivotAt = (sprite, p) => {
      sprite.pivot.set(p.x, p.y)
      sprite.position.set(p.x, p.y)
    }
    pivotAt(legL, HIP_L)
    pivotAt(legR, HIP_R)
    body.position.set(0, 0)

    root.addChild(legL, legR, body)
    // 让身体层后加 → 自然盖在腿上，不用手动设 zIndex

    /**
     * 适配缩放在【每帧】算，而不是挂载时算一次存起来。
     *
     * ⚠️ 踩过的坑：挂载那一刻 .pet-host 可能还没量出尺寸（clientWidth=0），
     *    算一次存起来的话会存下默认值 1，之后所有帧都按 1 渲染 ——
     *    角色以原始 642×1024 画出来，窗口里只看得到最下面一截小腿。
     *    每帧重算就没有这个时序问题（FramePet 本来也是这么做的）。
     */
    const fitScale = () => {
      const w = host.clientWidth
      const h = host.clientHeight
      if (!w || !h) return 0
      // 先钉死在固定高度上；只有宿主真的装不下时才缩小。
      // 这样气泡挤出几像素、窗口在漫游时变尺寸，都不会让角色忽大忽小。
      return Math.min(IDEAL_H / CH, w / CW, h / CH) * scaleRef.current
    }

    // 命中检测用【原始 idle.png】的掩码：
    // 走路时腿会挪位置，但整体剪影变化很小，用静立剪影做掩码足够了，
    // 而且不用每帧重建掩码。
    buildAlphaMask(idleUrl).then((m) => {
      if (m) maskRef.current = m
    })

    const t0 = performance.now()
    app.ticker.add(() => {
      const now = performance.now()
      const t = (now - t0) / 1000
      const A = actionRef.current

      /**
       * ── 教学动作 → 可解释的表现 ──
       *
       * 任务书 §16.4 第二条要求「把教学动作映射为可解释的表现」。
       * 原来这套映射只存在于 petAnimations.js 的配置表里 —— 而且
       * App.jsx 传给渲染组件的只有 walk/idle 两个值，教学节点根本没传到小人身上，
       * 所以切节点时视觉上毫无变化，等于没做。
       *
       * 现在按教学节点给**各自不同的动作**：不用另外生成 8 张表情图，
       * 靠腿部角度 + 身体起伏 + 倾斜就能把六种教学动作区分开。
       *
       * 腿部角度的正负：
       *   左腿 +角度 = 脚往右（内）；右腿 +角度 = 脚往右（外）
       *   → 并拢 = 左正右负；分开 = 左负右正
       */
      // bodyBob   —— 只动身体层，脚不动（呼吸、说话、思考这类）
      // rootBob   —— 连腿一起动（走路时整个人一起起伏，像在迈步）
      // 两者单位都是【屏幕像素】，别写成画布像素，否则会被缩放吃掉看不见
      let angL = 0
      let angR = 0
      let bodyBob = 0
      let rootBob = 0
      let rootShift = 0
      let tilt = 0

      if (sleepRef.current) {
        // 睡着了：整个人往下沉一点、呼吸变得又慢又深、腿收拢。
        // 精力见底会自动进入这个状态（见 petStats.js 的 maybeSleep）。
        bodyBob = 7 + Math.sin(t * 0.9) * 3
        angL = 1.2
        angR = -1.2
      } else switch (A) {
        case 'walk': {
          const ph = t * WALK_CYCLES_PER_SEC
          const s2 = Math.sin(2 * Math.PI * ph)
          // 腿：两腿【同向】轮流出脚（左腿先迈、复位，右腿再迈）。
          // 别改成反向 —— 反向是"双脚开合"，看起来像开合跳而不是走路。
          angL = WALK_SWING_DEG * Math.max(0, s2)
          angR = WALK_SWING_DEG * Math.max(0, -s2)
          // 真正让正面视角"读出在走"的是下面这三项，不是腿
          rootBob = -Math.abs(s2) * WALK_BOB_PX
          rootShift = s2 * WALK_SHIFT_PX
          tilt = s2 * WALK_TILT_DEG
          break
        }
        case 'talk': // 通用说话：快节奏起伏
          bodyBob = Math.sin(t * 12) * 3
          break

        // ── 三个教学动作要有【能分辨】的差别 ──
        // 原来 Teach/Ask/Hint 共用 talk，画面上完全一样，
        // 「把教学动作映射为可解释的表现」就成了空话。
        case 'teach': // 讲解：稳稳地讲，节奏均匀
          bodyBob = Math.sin(t * 7) * 2.5
          tilt = Math.sin(t * 1.1) * 1.6
          break
        case 'ask': // 提问：歪着头，慢，是"等你回答"的姿态
          tilt = 5.5 + Math.sin(t * 0.7) * 0.8
          bodyBob = Math.sin(t * 1.4) * 1.6
          break
        case 'correct': // Correct —— 指出答错（温和鼓励）
          // ⚠️ 这个分支以前【不存在】。NODE_TO_ANIMATION 把 Correct 映射到 'correct'，
          //    但 switch 里没有它 → 落到 default（idle 呼吸）——
          //    **逐帧模式正常，一切到分层模式就静默退化**。
          //    和当初 Reflect 那个坑一模一样，只是这次漏在播放器而不是映射表里。
          //    对不上的东西不会报错、不会崩，只是看起来"没反应"，极难发现。
          bodyBob = Math.sin(t * 2.6) * 3
          angL = 1.2
          angR = -1.2
          break
        case 'hint': // 提示：小幅快点，像在鼓励你
          bodyBob = -Math.abs(Math.sin(t * 3.4)) * 4
          angL = -1.2
          angR = 1.2
          break
        case 'think': // Assess / 加载中 / 重连中 —— 思考：慢慢左右轻晃
          tilt = Math.sin(t * 1.6) * 3.2
          bodyBob = Math.sin(t * 1.6) * 2
          break
        case 'sad': // Correct / 出错 / 取消 —— 沉下去，腿并拢，显得收敛
          bodyBob = 5 + Math.sin(t * 1.1) * 1.5
          angL = 1.5
          angR = -1.5
          break
        case 'quiz': // Test —— 精神饱满：站开一点，小幅快弹
          angL = -1.2
          angR = 1.2
          bodyBob = -Math.abs(Math.sin(t * 4.2)) * 4
          break
        case 'happy': // UpdateProfile —— 高兴：蹦起来，落地时腿微开
        {
          const h = Math.abs(Math.sin(t * 3.2))
          bodyBob = -h * 12
          angL = -h * 3
          angR = h * 3
          break
        }
        default:
          // idle —— 安静呼吸。腿完全不动，此时三层叠起来逐像素等于原立绘
          bodyBob = Math.sin(t * 1.85) * 2.5
      }

      // ── 点击弹跳 ──
      const b = pulseRef.current
      let bounce = 1
      let hop = 0
      if (b.active) {
        const e = (now - b.start) / 620
        if (e >= 1) {
          b.active = false
        } else {
          bounce = 1 + Math.sin(e * Math.PI * 2) * 0.06 * (1 - e)
          hop = Math.sin(e * Math.PI) * 15
        }
      }

      // 朝向：往左走时整体水平翻转
      const d = dirRef.current || 1
      const s = fitScale()
      if (!s) return // 宿主还没量出尺寸，这一帧先不画

      legL.angle = angL
      legR.angle = angR
      // bodyBob 是屏幕像素，而 body 在 root 内部（会被 scale 缩），所以要除回去
      body.y = bodyBob / s

      root.scale.set(s * d * bounce, s * bounce)
      root.rotation = ((tilt * Math.PI) / 180) * d
      // rootBob / rootShift 是屏幕像素，直接加在窗口坐标上，不用除以 scale
      root.position.set(
        host.clientWidth / 2 + rootShift * d,
        host.clientHeight + rootBob + hop
      )

      // 角色当前画在窗口里的矩形，供命中检测换算归一化坐标
      const dw = CW * s
      const dh = CH * s
      rectRef.current = {
        left: root.position.x - dw / 2,
        top: root.position.y - dh,
        w: dw,
        h: dh
      }
    })

    // 不需要监听 resize —— 缩放在 ticker 里每帧重算，窗口一变形下一帧就跟上了
    return () => {
      document.removeEventListener('visibilitychange', onVis)
      app.destroy(true, { children: true })
    }
  }, [])

  useEffect(() => {
    if (pulse > 0) pulseRef.current = { start: performance.now(), active: true }
  }, [pulse])

  // 动态命中检测：压在角色身上就收事件，落在透明区就穿透（同 FramePet）
  useEffect(() => {
    const onMove = (e) => {
      const rect = rectRef.current
      let hit = true
      if (rect && rect.w > 0 && rect.h > 0) {
        hit = hitTest(
          maskRef.current,
          (e.clientX - rect.left) / rect.w,
          (e.clientY - rect.top) / rect.h
        )
      }
      if (hit !== hoverRef.current) {
        hoverRef.current = hit
        hoverCbRef.current && hoverCbRef.current(hit)
      }
    }
    const onLeave = () => {
      if (hoverRef.current) {
        hoverRef.current = false
        hoverCbRef.current && hoverCbRef.current(false)
      }
    }
    window.addEventListener('mousemove', onMove)
    window.addEventListener('mouseleave', onLeave)
    return () => {
      window.removeEventListener('mousemove', onMove)
      window.removeEventListener('mouseleave', onLeave)
    }
  }, [])

  /* ── 上报命中区给主进程（穿透由主进程统一决策）──────────────────
   *
   * 为什么由渲染端上报：只有这里知道角色画在窗口的哪块、哪块是透明的。
   * 主进程拿不到这些，也就没法判断"光标是不是真在角色身上"。
   *
   * 只发两样东西：
   *   rect —— 角色在窗口坐标系里的外接矩形（每帧都在变，但幅度极小）
   *   mask —— 64×64 的 alpha 掩码，用来区分"在角色身上"和"在角色周围的透明矩形里"
   *
   * 每 300ms 发一次就够：光标判定是 50ms 一次的主进程轮询在做，
   * 这里只是给它喂数据，不需要跟渲染帧同频。
   * ────────────────────────────────────────────────────────────── */
  useEffect(() => {
    const send = () => {
      const rect = rectRef.current
      const mask = maskRef.current
      if (!rect || !mask || !rect.w || !rect.h) return
      try {
        window.deepprof.win.setHitRegion({
          rect: { left: Math.round(rect.left), top: Math.round(rect.top), w: Math.round(rect.w), h: Math.round(rect.h) },
          mask: Array.from(mask.alpha),
          maskSize: mask.size
        })
      } catch {
        /* 上报失败不影响主流程 */
      }
    }
    send()
    const id = setInterval(send, 300)
    return () => clearInterval(id)
  }, [])

  return <div className="pet-host" ref={hostRef} onClick={onTap} />
}
