import { useEffect, useRef } from 'react'
import './live2dSetup' // ⚠️ 必须在 pixi-live2d-display 之前，它要读 window.PIXI
import * as PIXI from 'pixi.js'
import { Live2DModel } from 'pixi-live2d-display/cubism4'

/** 模型路径：放 public/ 下，开发和生产都用相对路径（生产走 file://） */
const MODEL_URL = './live2d/haru/haru_greeter_t03.model3.json'

/**
 * Live2D 桌宠
 *
 * props:
 *   expression  表情序号（0-7），null 表示不切换
 *   motion      动作组名（'Idle' / 'Tap'）
 *   motionIndex 动作组内序号
 *   pulse       每变一次就重播一次动作（用于"点一下"这种重复触发）
 *   talking     是否正在说话 —— 为 true 时驱动口型参数
 *   onStatus    (status, message) 回调，用于把加载状态报给外层
 */
export default function Live2DPet({ expression, motion, motionIndex = 0, pulse = 0, talking = false, onStatus }) {
  const hostRef = useRef(null)
  const appRef = useRef(null)
  const modelRef = useRef(null)
  const talkingRef = useRef(talking)
  const statusRef = useRef(onStatus)

  talkingRef.current = talking
  statusRef.current = onStatus

  // ---- 初始化：建 Pixi 应用 + 加载模型 ----
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let disposed = false

    const app = new PIXI.Application({
      backgroundAlpha: 0, // 透明背景，桌宠才能浮在桌面上
      antialias: true,
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
      resizeTo: host
    })
    appRef.current = app
    host.appendChild(app.view)

    statusRef.current && statusRef.current('loading')

    /** 把模型缩放到刚好填满容器，并且脚底贴住底边 */
    const fit = (model) => {
      if (!model || !host.clientWidth || !host.clientHeight) return
      const w = host.clientWidth
      const h = host.clientHeight
      const scale = Math.min(w / model.width, h / model.height)
      model.scale.set(scale)
      model.x = (w - model.width) / 2
      model.y = h - model.height
    }

    Live2DModel.from(MODEL_URL, { autoInteract: false })
      .then((model) => {
        if (disposed) {
          model.destroy()
          return
        }
        modelRef.current = model
        app.stage.addChild(model)
        fit(model)

        // 待机动作循环播放
        try {
          model.motion('Idle', 0, 3)
        } catch (e) {
          console.warn('[Live2D] Idle 动作播放失败:', e.message)
        }

        // ---- 口型：说话时按节奏开合嘴巴 ----
        // ParamMouthOpenY 是模型自带的 LipSync 参数（见 model3.json 的 Groups）
        app.ticker.add(() => {
          if (!talkingRef.current) return
          try {
            const cm = model.internalModel.coreModel
            const t = performance.now() / 1000
            // 两个不同频率的正弦叠加，看起来不像机械节拍
            const v = (Math.sin(t * 13) * 0.5 + 0.5) * 0.55 + (Math.sin(t * 27) * 0.5 + 0.5) * 0.25
            cm.setParameterValueById('ParamMouthOpenY', v)
          } catch {
            /* 参数不存在就忽略 */
          }
        })

        statusRef.current && statusRef.current('ready')
      })
      .catch((err) => {
        console.error('[Live2D] 模型加载失败:', err)
        statusRef.current && statusRef.current('error', String(err && err.message ? err.message : err))
      })

    // 容器尺寸变化时重新贴合
    const onResize = () => fit(modelRef.current)
    window.addEventListener('resize', onResize)

    return () => {
      disposed = true
      window.removeEventListener('resize', onResize)
      modelRef.current = null
      app.destroy(true, { children: true, texture: true, baseTexture: true })
      appRef.current = null
    }
  }, [])

  // ---- 表情切换 ----
  useEffect(() => {
    const model = modelRef.current
    if (!model || expression === null || expression === undefined) return
    try {
      model.expression(expression)
    } catch (e) {
      console.warn('[Live2D] 表情切换失败:', expression, e.message)
    }
  }, [expression])

  // ---- 动作播放（pulse 变化就重播）----
  useEffect(() => {
    const model = modelRef.current
    if (!model || !motion) return
    try {
      model.motion(motion, motionIndex, 3)
    } catch (e) {
      console.warn('[Live2D] 动作播放失败:', motion, motionIndex, e.message)
    }
  }, [motion, motionIndex, pulse])

  // ---- 点击模型 → 播 Tap 动作 ----
  const onTap = () => {
    const model = modelRef.current
    if (!model) return
    try {
      model.motion('Tap', Math.floor(Math.random() * 2), 3)
    } catch {
      /* 忽略 */
    }
  }

  return <div className="live2d-host" ref={hostRef} onClick={onTap} />
}
