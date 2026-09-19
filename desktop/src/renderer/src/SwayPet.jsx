import { useEffect, useRef } from 'react'
import './live2dSetup' // ⚠️ 必须在 pixi 之前，install(@pixi/unsafe-eval) 在这里执行
import * as PIXI from 'pixi.js'
import { swayFragment } from './swayShader'

/** 贴图缓存：同一个表情反复切换时不用重新加载 */
const textureCache = new Map()
function getTexture(url) {
  if (!textureCache.has(url)) {
    textureCache.set(url, PIXI.Texture.from(url))
  }
  return textureCache.get(url)
}

/**
 * 会摆动的桌宠（PNG + 加权波浪形变）
 *
 * 和纯 CSS 动画的区别：CSS 只能整张图平移/缩放，
 * 这里是用着色器按位置加权做形变 —— 裙摆和头发会飘，脸保持不动。
 *
 * props:
 *   src      当前表情的图片地址
 *   talking  是否在说话（加强摆动幅度）
 *   pose     姿态：'idle' | 'talk' | 'think' | 'sad'
 *   pulse    变化即触发一次弹跳
 *   onTap    点击回调
 */
export default function SwayPet({ src, talking = false, pose = 'idle', pulse = 0, onTap }) {
  const hostRef = useRef(null)
  const appRef = useRef(null)
  const spriteRef = useRef(null)
  const filterRef = useRef(null)
  const talkingRef = useRef(talking)
  const poseRef = useRef(pose)
  const bounceRef = useRef({ start: 0, active: false })

  talkingRef.current = talking
  poseRef.current = pose

  // ---- 初始化 Pixi ----
  useEffect(() => {
    const host = hostRef.current
    if (!host) return
    let disposed = false

    const app = new PIXI.Application({
      backgroundAlpha: 0,
      antialias: true,
      resolution: window.devicePixelRatio || 1,
      autoDensity: true,
      resizeTo: host
    })
    appRef.current = app
    host.appendChild(app.view)

    // 摆动滤镜（顶点着色器用 Pixi 默认的，只提供片元着色器）
    const filter = new PIXI.Filter(undefined, swayFragment, {
      uTime: 0,
      uAmp: 0.013,
      uTalking: 0
    })
    filterRef.current = filter

    const sprite = new PIXI.Sprite(PIXI.Texture.EMPTY)
    sprite.anchor.set(0.5, 1) // 以脚底中心为锚点，呼吸/弹跳更自然
    sprite.filters = [filter]
    app.stage.addChild(sprite)
    spriteRef.current = sprite

    /** 缩放并贴底居中 */
    const fit = () => {
      const tex = sprite.texture
      if (!tex || !tex.width || !host.clientWidth) return
      const w = host.clientWidth
      const h = host.clientHeight
      const scale = Math.min(w / tex.width, h / tex.height)
      sprite.scale.set(scale)
      sprite.x = w / 2
      sprite.y = h
    }
    fit()

    // 贴图加载完成后重新贴合
    const onLoaded = () => fit()
    PIXI.Texture.addToCache // 触发一次引用，避免被打包器摇掉（无害）
    sprite.texture.baseTexture.on?.('loaded', onLoaded)

    // ---- 每帧驱动 ----
    const t0 = performance.now()
    app.ticker.add(() => {
      const t = (performance.now() - t0) / 1000
      const p = poseRef.current

      // 波浪
      filter.uniforms.uTime = t
      filter.uniforms.uTalking = talkingRef.current ? 1 : 0

      // 呼吸 / 姿态
      const tex = sprite.texture
      if (!tex || !tex.width) return
      const baseScale = Math.min(host.clientWidth / tex.width, host.clientHeight / tex.height)

      let breathY = Math.sin(t * 1.85) * 0.006        // 呼吸缩放
      let bob = Math.sin(t * 1.85) * 3                // 上下浮动
      let tilt = Math.sin(t * 0.9) * 0.6              // 轻微摇摆（度）

      if (p === 'walk') {
        // 走路：上下颠簸 + 左右轻摆，频率快、幅度大
        bob = -Math.abs(Math.sin(t * 8.5)) * 9
        tilt = Math.sin(t * 8.5) * 3.2
        breathY = Math.sin(t * 8.5) * 0.01
      } else if (p === 'talk') {
        breathY = Math.sin(t * 12) * 0.012
        bob = Math.sin(t * 12) * 2
      } else if (p === 'think') {
        tilt = Math.sin(t * 1.6) * 2.4
      } else if (p === 'sad') {
        breathY = -0.01
        bob = 3 + Math.sin(t * 1.2) * 1.5
        tilt = Math.sin(t * 0.7) * 0.8
      }

      // 点击弹跳
      const b = bounceRef.current
      let bounceScale = 1
      if (b.active) {
        const e = (performance.now() - b.start) / 620
        if (e >= 1) {
          b.active = false
        } else {
          // 先压扁再弹起
          bounceScale = 1 + Math.sin(e * Math.PI * 2) * 0.06 * (1 - e)
          bob -= Math.sin(e * Math.PI) * 16
        }
      }

      sprite.scale.set(baseScale * (1 + breathY) * bounceScale, baseScale * (1 - breathY * 0.7))
      sprite.rotation = (tilt * Math.PI) / 180
      sprite.x = host.clientWidth / 2
      sprite.y = host.clientHeight + bob
    })

    const onResize = () => fit()
    window.addEventListener('resize', onResize)

    return () => {
      disposed = true
      window.removeEventListener('resize', onResize)
      spriteRef.current = null
      filterRef.current = null
      app.destroy(true, { children: true })
      appRef.current = null
    }
  }, [])

  // ---- 换表情 ----
  useEffect(() => {
    const sprite = spriteRef.current
    const host = hostRef.current
    if (!sprite || !host || !src) return

    const tex = getTexture(src)
    sprite.texture = tex

    const apply = () => {
      if (!tex.width || !host.clientWidth) return
      const scale = Math.min(host.clientWidth / tex.width, host.clientHeight / tex.height)
      sprite.scale.set(scale)
      sprite.x = host.clientWidth / 2
      sprite.y = host.clientHeight
    }
    if (tex.width) apply()
    else tex.baseTexture.once('loaded', apply)
  }, [src])

  // ---- 点击弹跳 ----
  useEffect(() => {
    if (pulse > 0) bounceRef.current = { start: performance.now(), active: true }
  }, [pulse])

  return <div className="live2d-host" ref={hostRef} onClick={onTap} />
}
