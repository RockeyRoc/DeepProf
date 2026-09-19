/**
 * Live2D / Pixi 环境准备 —— 必须在任何用到 pixi-live2d-display 的代码之前执行。
 *
 * 三个必须做对的地方：
 *
 * 1. @pixi/unsafe-eval
 *    PixiJS 6 内部用 new Function() 动态生成着色器代码。我们的 CSP 不允许
 *    unsafe-eval（这正是 CSP 该拦的东西），所以 Pixi 会直接抛错：
 *      "Current environment does not allow unsafe-eval"
 *    官方给的解法是装这个包，它把那些代码在编译期就生成好，运行时不需要 eval。
 *    —— 千万不要为了图省事去放宽 CSP。
 *    ⚠️ install() 必须在创建任何 Pixi 对象之前调用。
 *
 * 2. window.PIXI
 *    pixi-live2d-display 加载时会去读 window.PIXI。不提前挂上，
 *    Cubism 4 支持会被静默禁用，模型加载失败且报错很难懂。
 *
 * 3. Cubism Core —— ⚠️ 已于 2026-09-19 移出交付目录
 *    官方运行时 live2dcubismcore.min.js 原先通过 index.html 的 <script> 引入，
 *    它会挂出 window.Live2DCubismCore。这个文件不在 npm 上，得单独放 public/。
 *    没有它，.moc3 模型解不开。
 *
 *    ⚠️ 现在 public/ 下**没有**这个文件了（样例模型 + 运行时都已移到
 *       `_素材工作区/_第三方样例模型/`）。本文件**自身不受影响** ——
 *       它只 import pixi.js 和 @pixi/unsafe-eval，**不碰** pixi-live2d-display，
 *       所以 FramePet / RigPet / SwayPet 照常工作。
 *       真正会因为缺运行时炸掉的是 `Live2DPet.jsx`（它 import cubism4），
 *       而那条路径已被 `App.jsx` 的 React.lazy 隔离。
 *       要恢复 Live2D 链路验证，见 `_素材工作区/_第三方样例模型/README.md` §五。
 *
 * 另外：我们只用 Cubism 4（.moc3），所以 import 走 'pixi-live2d-display/cubism4'。
 * 完整版还需要 Cubism 2 的 live2d.min.js，那是另一份授权文件，这里用不上。
 */
import * as PIXI from 'pixi.js'
import { install } from '@pixi/unsafe-eval'

install(PIXI) // ⚠️ 必须在任何 Pixi 对象之前

window.PIXI = PIXI

export { PIXI }
