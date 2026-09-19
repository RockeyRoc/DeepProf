/**
 * 命中检测 —— 判断鼠标是不是真的落在「角色身上」。
 *
 * 为什么不能只看"鼠标在不在窗口内"：
 *   桌宠窗口是透明无边框的，窗口大小 = 角色外接矩形。
 *   角色周围大片是透明的，但那些透明像素**仍然属于窗口**。
 *   如果按矩形判断，用户在角色旁边的空白处点一下，事件会被桌宠吃掉，
 *   点不到后面的桌面图标 —— 这正是"桌宠挡路"的根源。
 *
 * 做法：把图缩到 64×64，把 alpha 通道读出来当掩码。
 * 鼠标位置换算成掩码坐标，alpha 低于阈值就算"没命中"，让事件穿透过去。
 *
 * 64×64 是精度和性能的平衡：够准（每个格子约角色宽度的 1.5%），
 * 又小到可以常驻内存、每次 mousemove 查一次毫无压力。
 */

const MASK_SIZE = 64
const ALPHA_THRESHOLD = 24 // 低于这个 alpha 视为透明（抗锯齿边缘也不该命中）

/** 缓存：同一张图只建一次掩码 */
const maskCache = new Map()

/**
 * 为一张图建立 alpha 掩码。
 * @returns {Promise<{size:number, alpha:Uint8Array}|null>}
 */
export function buildAlphaMask(url) {
  if (maskCache.has(url)) return maskCache.get(url)

  const promise = new Promise((resolve) => {
    const img = new Image()
    img.crossOrigin = 'anonymous'
    img.onload = () => {
      try {
        const c = document.createElement('canvas')
        c.width = MASK_SIZE
        c.height = MASK_SIZE
        const ctx = c.getContext('2d', { willReadFrequently: true })
        ctx.drawImage(img, 0, 0, MASK_SIZE, MASK_SIZE)
        const data = ctx.getImageData(0, 0, MASK_SIZE, MASK_SIZE).data
        const alpha = new Uint8Array(MASK_SIZE * MASK_SIZE)
        for (let i = 0; i < alpha.length; i++) {
          alpha[i] = data[i * 4 + 3]
        }
        resolve({ size: MASK_SIZE, alpha })
      } catch {
        resolve(null) // 读不到像素就退化成"整块矩形都命中"
      }
    }
    img.onerror = () => resolve(null)
    img.src = url
  })

  maskCache.set(url, promise)
  return promise
}

/**
 * 命中检测。
 *
 * @param mask   buildAlphaMask 的结果
 * @param nx     鼠标在图片矩形内的归一化横坐标（0~1）
 * @param ny     鼠标在图片矩形内的归一化纵坐标（0~1）
 * @returns {boolean} true = 命中角色，应接收事件；false = 落在透明区，应穿透
 */
export function hitTest(mask, nx, ny) {
  // 没有掩码就保守放行（宁可接收事件，也不要让用户点不到角色）
  if (!mask) return true

  // 矩形之外一定不命中
  if (nx < 0 || nx > 1 || ny < 0 || ny > 1) return false

  const x = Math.min(mask.size - 1, Math.max(0, Math.floor(nx * mask.size)))
  const y = Math.min(mask.size - 1, Math.max(0, Math.floor(ny * mask.size)))

  return mask.alpha[y * mask.size + x] >= ALPHA_THRESHOLD
}
