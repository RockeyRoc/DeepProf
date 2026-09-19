import { useEffect, useRef } from 'react'
import './live2dSetup' // ⚠️ 必须在 pixi 之前，install(@pixi/unsafe-eval) 在这里执行
import * as PIXI from 'pixi.js'
import { swayFragment } from './swayShader'
import { buildAlphaMask, hitTest } from './hitTest'

/**
 * 逐帧动画桌宠播放器
 *
 * 为什么需要它：
 *   早期 QQ 企鹅之所以"活"，是因为它有整套逐帧素材（站立 2-4 帧、走路 4-8 帧…），
 *   靠"换图"产生动画。用一张平铺图 + 位移/缩放，观感永远是"一张贴纸在滑动"。
 *
 * 这个组件负责把「一串帧图」按指定帧率播出来。素材到位前，
 * 传单帧数组也能正常工作（退化成静态图 + 形变滤镜）。
 *
 * props:
 *   frames    帧图地址数组（按播放顺序）
 *   fps       帧率
 *   loop      是否循环
 *   pose      姿态：'idle' | 'walk' | 'talk' | 'think' | 'sad'
 *   talking   是否在说话
 *   pulse     变化时强制重播（用于点击反应这种一次性动作）
 *   sway      是否启用形变滤镜（逐帧素材到位后可以关掉，形变会跟帧动画打架）
 *   onTap     点击回调
 */

/**
 * 角色的目标显示高度（像素）—— **上限**。
 *
 * ⚠️ 不加这个上限会出两种事故（都是实测抓到的）：
 *   1. **开/关对话面板的那一瞬间**：窗口先变成 380×620，而 React 还没重渲染类名，
 *      此刻角色区是整个 620 高 → 实测 scale 冲到 0.9984，
 *      **角色被瞬间放大一倍再缩回去**，看起来就是"忽大忽小"。
 *   2. 开对话（角色区 ~300）和关对话（角色区 ~302）算出来尺寸不一致，
 *      切面板时角色会轻微跳一下。
 *
 * 钉死一个上限，角色尺寸就和窗口/面板状态彻底解耦了。
 * （分层 rig 那条路（RigPet）早就这么做了，逐帧这边之前漏了。）
 */
const IDEAL_H = 230

/*
 * 舞台坐标 → 屏幕 CSS 像素的换算系数。
 *
 * ✅ 恒为 1，**不是标定值，也不需要标定**。
 *
 * 曾经这里写的是 0.5，还配了一大段"实测值、根因未查明"的注释 ——
 * 那是在补偿一个**真正的 bug**，而不是什么机器差异：
 *   app 里的 expressions/*.png 是 768 的旧图，而 geometry.json 声明画布 1536、
 *   target_h=1200 —— 缩放按 1200 算、贴图里角色只有 ~600px 高，**正好差 2 倍**。
 *   把 PNG 重新导出成 1536 之后，这个系数自然就是 1 了。
 *
 * ⚠️ 所以：**如果你发现角色大小不对，先查"素材和 geometry 是否对得上"**
 *    （下面 contentSize 里有自检告警），而不是回来改这个系数。
 */
const SCREEN_FACTOR = 1

/** 贴图缓存：同一批帧反复切换时不用重新加载 */
const textureCache = new Map()
function getTexture(url) {
  if (!textureCache.has(url)) {
    const t = PIXI.Texture.from(url)
    // ⚠️ 不要开 mipmap。
    //    试过：mipmap 能把"锯齿感/像素感"压下去（拉普拉斯方差 11.3 → 19.4），
    //    但代价是**整体发虚**——张钧翔实测反馈"像素上来了但人物变模糊了"。
    //    默认的双线性虽然采样粗糙，但线条更实、更像"画"。
    //    结论：宁可略有锯齿，也不要糊。
    textureCache.set(url, t)
  }
  return textureCache.get(url)
}

/**
 * 贴图是不是【真的加载完了】。
 *
 * ⚠️ 判据**不能**只写 `tex.width`。贴图还在加载时，Pixi 给的是 1×1 的空壳
 *    （Texture 构造时 noFrame=true、frame 先占位成 1×1，等 baseTexture 加载完
 *     才回填真实尺寸），于是 `tex.width === 1` 是个**真值**，
 *    `if (tex.width)` 这种判据根本拦不住 —— 拿它当"贴图尺寸"去比对 geometry，
 *    每次启动都会喊"1×1 ≠ 1536×1536"，把真正的素材错误淹掉。
 *
 * baseTexture.valid 是加载完成才置 true 的；尺寸 > 1 是第二道保险。
 */
const textureReady = (tex) =>
  !!tex && !!tex.baseTexture && tex.baseTexture.valid === true && tex.width > 1 && tex.height > 1

export default function FramePet({
  frames,
  fps = 8,
  loop = true,
  holds = null,
  pose = 'idle',
  talking = false,
  dir = 1,
  pulse = 0,
  sway = true,
  geometry = null,
  scale = 1, // 用户滚轮调的缩放倍率，乘在适配缩放上 // 角色在画布里的外接框（归一化脚本产出），null 时按整张画布
  overlay = null, // { frames:[url…], holds:[ms…], trigger:N } —— 独立时序的叠加动画（眨眼用）
  onTap,
  onHoverChange
}) {
  const hostRef = useRef(null)
  const appRef = useRef(null)
  const spriteRef = useRef(null)
  const filterRef = useRef(null)

  // 用 ref 存动画状态，避免每帧重渲染
  const animRef = useRef({ frames: [], fps, loop, holds, index: 0, acc: 0 })
  const poseRef = useRef(pose)
  const talkingRef = useRef(talking)
  const swayRef = useRef(sway)
  const dirRef = useRef(dir)
  const bounceRef = useRef({ start: 0, active: false })
  const scaleRef = useRef(scale)

  // 命中检测相关：角色当前画在窗口里的矩形 + 当前的 alpha 掩码
  /** 让外面那个"帧列表变了"的 effect 能调到 Pixi 里的 showFrame（它在挂载 effect 的闭包里） */
  const showFrameRef = useRef(null)
  /**
   * 叠加动画（overlay）—— 叠在基础动作【上面】播的一条独立帧序列。
   *
   * 用途是**眨眼**：眨眼不该抢掉待机，而是叠在待机上闪一下。
   * 非循环的 overlay 播完最后一帧就自然结束，之后落回基础动作的当前帧。
   *
   * ⚠️ 只有传了 overlay 的动画才走这条逻辑；没传的动画（教学表情、走路…）
   *    行为完全不变 —— 这点很重要，别为了眨眼把别人的动画改坏。
   */
  const overlayRef = useRef({ frames: [], holds: null, index: 0, acc: 0, active: false })
  /** 当前已经贴到精灵上的图 —— 跨渲染期保存，overlay 起播时要用它决定从第几帧开始 */
  const lastUrlRef = useRef(null)
  const showActiveFrameRef = useRef(null)
  const rectRef = useRef(null)
  const maskRef = useRef(null)
  const masksRef = useRef({}) // url -> alpha 掩码
  const hoverRef = useRef(false)
  const hoverCbRef = useRef(onHoverChange)
  hoverCbRef.current = onHoverChange

  // 「素材 vs geometry」自检的两本账（见 checkMaterialSize）：
  //   已喊过的图 —— ticker 每帧都会查一次，不记账就会把终端刷爆
  //   已挂过 loaded 回调的 baseTexture —— 切帧时同一张图会反复经过 applyTexture
  const sizeWarnedRef = useRef(new Set())
  const sizeHookedRef = useRef(new Set())

  poseRef.current = pose
  talkingRef.current = talking
  swayRef.current = sway
  dirRef.current = dir
  // 滚轮缩放走 ref：它在 ticker 里每帧读，用 state 会引发整组件重渲染
  scaleRef.current = scale

  // 帧列表变化时更新（放在渲染期同步，避免切动作时闪一帧旧图）
  const frameKey = Array.isArray(frames) ? frames.join('|') : String(frames || '')
  if (animRef.current.key !== frameKey) {
    const list = Array.isArray(frames) ? frames : frames ? [frames] : []
    animRef.current = { key: frameKey, frames: list, fps, loop, holds, index: 0, acc: 0 }
  }

  // overlay 变化时重载并从头播（比如眨眼：每次 trigger 加一，就重播一遍）
  //
  // ⚠️ 必须先判 `overlay` 再算 oKey。踩过的坑：overlay 为 null 时
  //    oFrames=[]、oKey 会拼成 "#0" —— 这是个**真值**，于是进了下面的分支
  //    去读 `overlay.holds`，抛 TypeError。
  //    后果特别严重：抛在渲染期 → React 卸载整棵树 → **桌宠窗口变成纯空白**，
  //    而 App.jsx 只在待机时传 overlay（别的动作传 null），
  //    所以"一切换动作人就没了"。加 overlay 的时候务必连 null 分支一起测。
  if (overlay) {
    const oFrames = overlay.frames || []
    const oKey = oFrames.join('|') + '#' + (overlay.trigger || 0)
    if (oKey && overlayRef.current.key !== oKey) {
      // 起播帧的挑选：眨眼序列是 [睁眼, 闭眼]，而睁眼那张与当前待机图**是同一张**。
      // 如果屏幕上现在正好显示的就是它，直接从第 1 帧（闭眼）开始 ——
      // 否则会先"显示已经显示着的那张" 90ms，白占掉一段本该是闭眼的时间。
      // 找不到能跳过的就从头来（通用写法，换个 overlay 也成立）。
      const startIdx = oFrames.length > 1 && oFrames[0] === lastUrlRef.current ? 1 : 0
      overlayRef.current = {
        key: oKey,
        frames: oFrames,
        holds: overlay.holds || null,
        index: startIdx,
        acc: 0,
        active: oFrames.length > 0
      }
    }
  }

  // ---- 初始化 Pixi ----
  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    const app = new PIXI.Application({
      backgroundAlpha: 0,
      antialias: true,
      // ⚠️ 必须跟随 devicePixelRatio，**不要写死 1**。
      //    写死 1 的时候，在 150% 缩放的屏幕上画布只按 1× 渲染，
      //    再被系统拉到 1.5× —— 角色看起来一层"磨砂"感（张钧翔实测反馈）。
      //    角色源图虽然大（1536 画布 / 角色 1200px），但屏幕上要铺到
      //    300 设备像素，DPR 下才是逐像素对齐的。
      resolution: window.devicePixelRatio || 1,
      autoDensity: true
      // ⚠️ 这里【不要】用 Pixi 的 `resizeTo: host`。
      //    它会把渲染器的【逻辑尺寸】设成 host × resolution ——
      //    实测 150% 缩放下是 333×633，而画布的 CSS 尺寸仍是 host（222×422），
      //    于是整个舞台被压缩了 222/333 ≈ 0.67 倍。
      //    **这就是角色一直"只渲染出一半大"的根因。**
      //    手动 resize 到宿主的 CSS 尺寸才是对的，见下面。
    })
    appRef.current = app
    host.appendChild(app.view)

    /**
     * 把渲染器尺寸对齐到宿主的【CSS 尺寸】（不是 CSS 尺寸 × DPR）。
     * 这样舞台坐标就和屏幕 CSS 像素 1:1，不用任何换算系数。
     */
    const resizeRenderer = () => {
      const w = host.clientWidth
      const h = host.clientHeight
      if (w && h) app.renderer.resize(w, h)
    }
    resizeRenderer()

    /* ── 窗口不可见时暂停渲染循环（性能红线）──────────────────────
     * 反面教材：Desktop Mate 隐藏后在后台仍占 15% CPU 被骂穿。
     * Chromium 本来就会在窗口隐藏时停发 requestAnimationFrame，
     * 但显式停 ticker 更稳妥，也覆盖"最小化""被完全遮挡"这些边界。
     * ──────────────────────────────────────────────────────────── */
    const onVis = () => (document.hidden ? app.ticker.stop() : app.ticker.start())
    document.addEventListener('visibilitychange', onVis)
    onVis()

    // 形变滤镜（帧动画到位后可以关掉）
    const filter = new PIXI.Filter(undefined, swayFragment, {
      uTime: 0,
      uAmp: 0.012,
      uTalking: 0
    })
    filterRef.current = filter

    const sprite = new PIXI.Sprite(PIXI.Texture.EMPTY)
    sprite.filters = [filter]
    app.stage.addChild(sprite)
    spriteRef.current = sprite

    /* ══════════════════════════════════════════════════════════
     * 尺寸与锚点：按【角色实际占的那块】算，不是按整张画布
     *
     * ⚠️ 踩过的坑：归一化产出的画布是 **768×768 的方形**，角色只占其中
     *    81% 高（脚底上方留 62px、头顶上方留 86px，是给走路起伏用的）。
     *    而桌宠窗口是 **220×300 的窄高形** —— 按整张画布缩放会变成
     *    "宽度受限"，角色白白小掉四成，肉眼一看就是"怎么变小了"。
     *
     * 所以由 `_几何.json`（归一化脚本产出）告诉我们角色框在哪，
     * 用它来定锚点和缩放。没有 geometry 时退回旧行为（按整张画布）。
     * ══════════════════════════════════════════════════════════ */
    const geo = geometry || null

    /**
     * 锚点 = 【画布水平中心】 + 【角色脚底】。
     *
     * ⚠️ 横向锚点是画布中心（0.5），**不是角色外接框的中心**。
     *    因为归一化是把【头部中心】对齐到画布中心的 —— 而角色有尾巴时，
     *    尾巴会把外接框推向一侧，框中心和头心差很远：
     *    实测表情那批的框中心在 784.5、走路那批在 732.5，**差 52px**。
     *    用框中心当锚点，走路和待机切换时角色会横向跳半个人头。
     */
    const applyAnchor = () => {
      const tex = sprite.texture
      if (!tex || !tex.width) return
      if (!geo) {
        sprite.anchor.set(0.5, 1)
        return
      }
      // ⚠️ 分母必须用 geo.canvas_h，**不要**改用贴图的实际高度。
      //    锚点要的是"脚底在画布里的【比例】"，而 char_y1 是写在 geometry 自己那套
      //    画布坐标里的 —— 两边同源，比例才成立。比例是**尺度无关**的：
      //    素材与 geometry 尺寸不一致时它照样对（1024 空间里 942/1024 = 1536 空间里
      //    1413/1536 = 0.92）；换成贴图高度反而会错成 942/1536 = 0.61，角色浮空。
      const ch = geo.canvas_h || tex.height
      sprite.anchor.set(0.5, geo.char_y1 / ch)
    }

    /**
     * 缩放用的"角色尺寸"。
     *
     * ⚠️ 高度用 **target_h（归一化时的目标高度）**，而不是外接框的实际高度。
     *    因为外接框把【尾巴】也算进去了 —— 尾巴伸得远不远，各批不一样：
     *    实测表情那批框高 1201、走路那批 1227。按框高算，两批的缩放系数就差 2%，
     *    切换 idle↔walk 时角色会**忽大忽小**。
     *    而归一化保证的是"角色本体高 = target_h"，所有批一致 —— 用它就没有这个问题。
     */
    const contentSize = (tex) => {
      if (!geo) return { w: tex.width, h: tex.height }

      // ⚠️ 素材和 geometry 对不上时（见上面 checkMaterialSize），
      //    按【贴图实际尺寸 ÷ 声明画布尺寸】把角色框等比缩回来 ——
      //    不能硬拿 geometry 里的数字去算缩放，否则角色就按错误的尺寸渲染了
      //    （768 的图配 1536 的声明 = 恒为一半大）。
      //    对得上时 k 恒为 1，行为跟以前一模一样。
      //    实测（2026-09-19）：1024 画布重出的 geometry 配 1536 的 PNG，
      //    角色渲染高度 345 设备像素 —— 与正常态的 344 一致；不兜的话是 509（1.5 倍）。
      //
      //    前提：geometry 的 canvas_w/h 与 char 框、target_h 必须是**同一套坐标系**
      //    （归一化帧.py 产出的必然如此）。若有人只手改 canvas_w/h 而不同步改
      //    char 框和 target_h，那份 geometry 本身就自相矛盾，兜底会过度补偿 ——
      //    它只是"别让角色按错误尺寸渲染"的临时安全网，**不是**素材不一致的解法。
      const k = textureReady(tex) && geo.canvas_w ? tex.width / geo.canvas_w : 1
      return {
        w: (geo.char_x1 - geo.char_x0) * k,
        h: (geo.target_h || geo.char_y1 - geo.char_y0) * k
      }
    }

    /**
     * 【自检】geometry 声明的画布尺寸必须和贴图实际尺寸一致。
     *
     * 不一致 = 素材被重新导出过、但 geometry 没跟着更新（或反过来）。
     * 2026-09-19 踩过这个坑：表情 PNG 是 768、geometry 写 1536，
     * 缩放按 1536 算而贴图只有 768 → **角色恒为一半大**，
     * 不报错、不崩溃，只是"看着不对"，几个人查了一晚上。
     * 所以这里主动喊一声，别再让下一个人踩。
     *
     * ⚠️ 判定的时机是**贴图真正加载完之后**（判据见 textureReady）：
     *    加载完成前 Pixi 给的是 1×1 的空壳，tex.width === 1 是个**真值**，
     *    照判必然误报 —— 每次启动刷一屏"素材与 geometry 对不上"，
     *    真出问题时反而没人会注意到（这条告警是唯一能拦住那个致命 bug 的防线）。
     *    调用点两处：applyTexture（挂 baseTexture 的 loaded）+ ticker（每帧补一次）。
     */
    const checkMaterialSize = (url, tex) => {
      if (!geo || !geo.canvas_w || !geo.canvas_h) return
      if (!textureReady(tex)) return // 空壳，量不出真实尺寸，这一帧不判

      if (tex.width === geo.canvas_w && tex.height === geo.canvas_h) {
        sizeWarnedRef.current.delete(url) // 又对上了 —— 下次再坏还要能喊
        return
      }
      if (sizeWarnedRef.current.has(url)) return // 同一张图只喊一次，不刷屏
      sizeWarnedRef.current.add(url)

      // 说清【哪个文件、多大、会怎么错】。
      // 相对大小：contentSize 是按"贴图实际尺寸 ÷ 声明画布尺寸"折算的，
      // 贴图比声明的大 → 不兜的话角色会被放大，反之偏小。
      const k = tex.width / geo.canvas_w
      const how = k < 1 ? `偏小（约 ${Math.round(k * 100)}%）` : `偏大（约 ${Math.round(k * 100)}%）`
      // url 形如 …/assets/pet/expressions/idle.png —— 该批的 geometry 一般就在同目录
      // （注意：眨眼/说话那两批是**共用**表情那批的 geometry.json 的）
      const geoPath = String(url).replace(/[^/\\]+$/, 'geometry.json')
      console.error(
        `[桌宠] ⚠️ 素材与 geometry 对不上：贴图 ${tex.width}x${tex.height}（${url}）` +
          `，geometry 声明画布 ${geo.canvas_w}x${geo.canvas_h}（${geoPath}）。` +
          ` 不修的话缩放会按错误比例算 → 角色会${how}；` +
          ` 现在已按贴图实际尺寸临时兜住，但素材必须重出：` +
          `请重跑 归一化帧.py，把 PNG 和它对应的 geometry.json 一起重新拷贝。`
      )
    }

    /** 缩放并贴底居中 */
    const fit = () => {
      const tex = sprite.texture
      if (!tex || !tex.width || !host.clientWidth) return
      applyAnchor()
      const c = contentSize(tex)
      sprite.scale.set(
        Math.min(
          IDEAL_H / (c.h * SCREEN_FACTOR),
          host.clientWidth / (c.w * SCREEN_FACTOR),
          host.clientHeight / (c.h * SCREEN_FACTOR)
        ) * scaleRef.current
      )
      sprite.x = host.clientWidth / 2
      sprite.y = host.clientHeight
    }

    /**
     * 当前该显示哪张图 —— **唯一**决定 sprite.texture 的地方。
     *
     * ⚠️ 曾经踩过的坑：这里原来有两条路径（showFrame 和 showActiveFrame）都在写
     *    sprite.texture，而且同一帧里都会跑 —— 两张图每帧互相覆盖，
     *    屏幕上看就是角色**高频闪烁**。
     *    规则：**任何时候只允许一条写贴图的路径**。overlay 活动时它就是唯一来源，
     *    基础动画只推进 a.index，不动贴图。
     */
    const wantedUrl = () => {
      const o = overlayRef.current
      if (o.active && o.frames.length) return o.frames[o.index] || null
      const a = animRef.current
      if (!a.frames.length) return null
      const i = ((a.index % a.frames.length) + a.frames.length) % a.frames.length
      return a.frames[i]
    }

    /** 把 wantedUrl 应用到精灵 —— 内容没变就什么都不做（避免每帧重复赋值） */
    const applyTexture = (force) => {
      const url = wantedUrl()
      if (!url) return
      if (!force && url === lastUrlRef.current) return
      lastUrlRef.current = url
      const tex = getTexture(url)
      sprite.texture = tex
      // 命中检测用当前这一帧的掩码
      maskRef.current = masksRef.current[url] || null
      // 素材尺寸自检：贴图已经加载完就直接判；否则挂到 baseTexture 的 loaded 上
      // （就是下面 fit 用的那套写法）。⚠️ 千万别在这里裸判 —— 此刻 tex 还是
      // 1×1 的空壳，判了必误报，见 checkMaterialSize 的说明。
      if (textureReady(tex)) {
        checkMaterialSize(url, tex)
      } else if (!sizeHookedRef.current.has(tex.baseTexture)) {
        sizeHookedRef.current.add(tex.baseTexture) // 同一张图只挂一次，别攒监听
        tex.baseTexture.once('loaded', () => checkMaterialSize(url, tex))
      }
      if (tex.width) fit()
      else tex.baseTexture.once('loaded', fit)
    }

    /** 兼容旧调用点（外部通过 showFrameRef 切帧） */
    const showFrame = (i) => {
      animRef.current.index = i
      applyTexture(true)
    }

    // 预加载所有帧，避免播放时闪烁
    animRef.current.frames.forEach((u) => getTexture(u))
    overlayRef.current.frames.forEach((u) => getTexture(u))
    applyTexture(true)
    showFrameRef.current = showFrame
    showActiveFrameRef.current = () => applyTexture(false)

    // ---- 每帧驱动 ----
    const t0 = performance.now()
    let last = t0

    app.ticker.add(() => {
      const now = performance.now()
      const dt = (now - last) / 1000
      last = now
      const t = (now - t0) / 1000

      // --- 帧推进 ---
      const a = animRef.current
      if (a.frames.length > 1 && a.fps > 0) {
        a.acc += dt
        // 默认等间隔 1/fps；给了 holds（每帧停多少毫秒）就按帧算。
        //
        // ⚠️ 眨眼需要这个：睁眼要停好几秒、闭眼只闪百来毫秒，
        //    等间隔做不到（1/fps 对两帧都是一样的时长）。
        //    不传 holds 的动画走原来的分支，行为一模一样。
        const stepFor = (i) =>
          a.holds && a.holds[i] != null ? a.holds[i] / 1000 : 1 / a.fps
        let guard = 0
        while (a.acc >= stepFor(a.index) && guard++ < 8) {
          a.acc -= stepFor(a.index)
          a.index += 1
          if (a.index >= a.frames.length) {
            if (a.loop) a.index = 0
            else a.index = a.frames.length - 1
          }
        }
      }

      // --- overlay 推进（眨眼）---
      // ⚠️ 这里只推进索引，**不碰贴图** —— 贴图统一在下面 applyTexture 一次。
      //    以前这里是"推进 + 立刻写贴图"，而基础动画那支也会写，两边打架 → 闪烁。
      const o = overlayRef.current
      if (o.active && o.frames.length) {
        o.acc += dt
        const oStep = (i) => (o.holds && o.holds[i] != null ? o.holds[i] / 1000 : 0.12)
        let og = 0
        while (o.acc >= oStep(o.index) && og++ < 8) {
          o.acc -= oStep(o.index)
          if (o.index >= o.frames.length - 1) {
            o.active = false // 播完，交还控制权给基础动画
            break
          }
          o.index += 1
        }
      }

      // 唯一一次贴图应用：内容没变就直接返回，不会每帧重复赋值
      applyTexture(false)

      // --- 形变滤镜 ---
      if (swayRef.current) {
        if (!sprite.filters || !sprite.filters.length) sprite.filters = [filter]
        filter.uniforms.uTime = t
        filter.uniforms.uTalking = talkingRef.current ? 1 : 0
      } else if (sprite.filters && sprite.filters.length) {
        // ⚠️ 关掉形变必须【把滤镜摘掉】，不能只把 uTime 归零。
        //
        //    看 swayShader 里的 wave：
        //      sin(uv.y*5.0 - t*1.7)*0.6 + sin(uv.y*11.0 + t*2.6 + uv.x*3.0)*0.4
        //    uTime=0 只是让它【不再摆动】，但这两个 sin 照样非零 ——
        //    整张图仍被按非整数偏移重新采样一遍，**必然发糊**。
        //
        //    实测：这就是角色"像素低、像蒙了一层磨砂"的主因。
        //    之前只归零 uTime，等于只关了一半。
        sprite.filters = []
      }

      // --- 整体姿态（帧动画之外的辅助律动）---
      const tex = sprite.texture
      if (!tex || !tex.width || !host.clientWidth) return
      // 素材尺寸自检的兜底：贴图是异步加载的，挂在上面的 loaded 回调万一没赶上
      // （或者窗口隐藏、ticker 停过），这里每帧补一次。checkMaterialSize 内部
      // 会跳过空壳、并对同一张图只喊一次，所以这里跑不出噪音。
      const curUrl = lastUrlRef.current
      if (curUrl && textureCache.get(curUrl) === tex) checkMaterialSize(curUrl, tex)
      // 按【角色那块】算，不是整张画布 —— 见上面 applyAnchor 的说明
      const content = contentSize(tex)
      const base =
        Math.min(
          IDEAL_H / (content.h * SCREEN_FACTOR),
          host.clientWidth / (content.w * SCREEN_FACTOR),
          host.clientHeight / (content.h * SCREEN_FACTOR)
        ) * scaleRef.current

      const p = poseRef.current
      // ⚠️ scaleY 是【增量】，初值必须是 0，不是 1。
      //    它在下面按 `1 + scaleY` 用，写成 1 会变成 ×2（纵向拉长一倍），
      //    横向那边是 `1 - scaleY*0.7`，会压扁到 0.3 —— 整个人扭成一条。
      //    历史上初值是 1 却没事，是因为每个分支都会给它重新赋值；
      //    一旦某个分支不赋值就会踩这个坑（2026-09-19 踩过）。
      let scaleY = 0
      let bob = 0
      let tilt = 0

      if (p === 'walk') {
        // ⚠️ 只有在【没有真走路帧】时才用这套"企鹅摇摆"顶替。
        //
        //    它是单帧素材时代的替代品：靠 ±7.5° 倾斜 + 上下起伏硬撑出"在走"。
        //    现在走路已经是 4 张真帧了 —— 帧本身就在表达步态，
        //    再叠这层摇摆就是**两套动作打架**：实测表现为"前后晃、站不稳"。
        //    有帧就交给帧，别插手。
        if (a.frames.length <= 1) {
          const phase = t * 7.2
          bob = -Math.abs(Math.sin(phase)) * 9
          tilt = Math.sin(phase) * 7.5
          scaleY = Math.sin(phase * 2) * 0.018
        }
      } else if (p === 'talk') {
        // ⚠️ 别再用 scaleY 做"说话起伏"。见下面 idle 分支的说明。
        bob = Math.sin(t * 8) * 2
      } else if (p === 'think') {
        tilt = Math.sin(t * 1.6) * 2.2
      } else if (p === 'sad') {
        bob = 3 + Math.sin(t * 1.2) * 1.4
      } else {
        // ⚠️ 待机**只做整体上下位移，不做纵向缩放**。
        //
        //    踩过的坑：原来这里是 `scaleY = sin(...) * 0.007` ——
        //    缩放是绕【脚底锚点】做的，等于把整个人（含脚）纵向拽长拽短。
        //    肉眼看不是"在呼吸"，而是"被上下拉扯"。
        //    真正的呼吸应该只动胸口，那需要待机 2 帧素材（第③批），
        //    代码缩放模仿不来。所以这里退回最朴素的上下浮动。
        bob = Math.sin(t * 1.4) * 2
      }

      // 点击弹跳
      const b = bounceRef.current
      let bounce = 1
      if (b.active) {
        const e = (now - b.start) / 620
        if (e >= 1) {
          b.active = false
        } else {
          bounce = 1 + Math.sin(e * Math.PI * 2) * 0.06 * (1 - e)
          bob -= Math.sin(e * Math.PI) * 15
        }
      }

      // 横向缩放乘上朝向：往左走时整张图水平翻转，角色就"转身"了
      const d = dirRef.current || 1
      sprite.scale.set(base * (1 - scaleY * 0.7) * bounce * d, base * (1 + scaleY) * bounce)
      sprite.rotation = (tilt * Math.PI) / 180
      sprite.x = host.clientWidth / 2
      sprite.y = host.clientHeight + bob

      // 记录角色当前画在窗口里的矩形，供命中检测换算归一化坐标
      // 锚点已经不一定是 (0.5, 1) 了（有 geometry 时是角色的脚底中心），
      // 而且往左走时 scale.x 是负的，所以要按锚点实算并取两边的最小值
      const dw = tex.width * sprite.scale.x
      const dh = tex.height * Math.abs(sprite.scale.y)
      const xa = sprite.x - sprite.anchor.x * dw
      const xb = sprite.x + (1 - sprite.anchor.x) * dw
      rectRef.current = {
        left: Math.min(xa, xb),
        top: sprite.y - sprite.anchor.y * dh,
        w: Math.abs(dw),
        h: dh
      }
    })

    const onResize = () => {
      resizeRenderer()
      fit()
    }
    window.addEventListener('resize', onResize)

    return () => {
      window.removeEventListener('resize', onResize)
      document.removeEventListener('visibilitychange', onVis)
      spriteRef.current = null
      filterRef.current = null
      app.destroy(true, { children: true })
      appRef.current = null
    }
  }, [])

  // ---- 点击弹跳 ----
  useEffect(() => {
    if (pulse > 0) bounceRef.current = { start: performance.now(), active: true }
  }, [pulse])

  // ---- 帧列表变了 → 立刻切到新列表的第一帧 ----
  //
  // ⚠️ 必须显式做这件事，不能指望 ticker。踩过的坑：
  //    ticker 里推进帧的分支写的是 `if (a.frames.length > 1)` ——
  //    而现在每个动作都是【单帧】（表情立绘一张一个动作），
  //    frames.length === 1，那个分支**根本不会执行**，
  //    于是 showFrame 只在挂载时被调过一次，贴图**永远停在最初那张**。
  //    表现出来就是"点了教学动作没反应" —— 节点换了、配置换了，但脸上没变。
  //    等以后走路/待机那批多帧素材到位，这段也不冲突（多帧时它切到第 0 帧，
  //    ticker 随后接管推进）。
  useEffect(() => {
    if (showFrameRef.current) showFrameRef.current(0)
  }, [frameKey])

  // ---- 为所有帧建立 alpha 掩码（命中检测用）----
  useEffect(() => {
    let cancelled = false
    const list = animRef.current.frames || []
    if (!list.length) return
    Promise.all(list.map((u) => buildAlphaMask(u))).then((masks) => {
      if (cancelled) return
      list.forEach((u, i) => {
        if (masks[i]) masksRef.current[u] = masks[i]
      })
      const first = list[0]
      if (!maskRef.current && masksRef.current[first]) maskRef.current = masksRef.current[first]
    })
    return () => {
      cancelled = true
    }
  }, [frameKey])

  /**
   * 动态命中检测 —— 决定"接收鼠标事件"还是"穿透"。
   *
   * 窗口开着 setIgnoreMouseEvents(true, { forward: true }) 时，
   * 仍然能收到 mousemove，所以这个循环能一直跑：
   *   鼠标压在角色身上 → 关掉穿透（能点能拖）
   *   鼠标在角色旁边的透明区 → 打开穿透（点到后面的桌面图标）
   */
  useEffect(() => {
    const onMove = (e) => {
      const rect = rectRef.current
      let hit = false

      if (rect && rect.w > 0 && rect.h > 0) {
        const nx = (e.clientX - rect.left) / rect.w
        const ny = (e.clientY - rect.top) / rect.h
        hit = hitTest(maskRef.current, nx, ny)
      } else {
        // 还没算好矩形时保守放行
        hit = true
      }

      if (hit !== hoverRef.current) {
        hoverRef.current = hit
        hoverCbRef.current && hoverCbRef.current(hit)
      }
    }

    // 鼠标离开窗口 = 肯定不在角色身上
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
