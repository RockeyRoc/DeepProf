/**
 * 用 Chrome DevTools Protocol 驱动桌宠窗口做**实拍验证**。
 *
 * 为什么要这么干：教学表情只有点按钮才会切，而桌宠是置顶透明窗口，
 * 从外面点鼠标要赌坐标（DPI 一缩放就全错）。走 CDP 可以直接在页面里
 * `dispatchEvent` / `element.click()` —— React 的事件挂在 root 上，
 * 原生事件冒泡上去照样触发 onClick / onDoubleClick。
 *
 * 用法（**必须在 `_素材工作区/` 下运行** —— 截图落在 `动作帧/预览/`，
 * 脚本是按当前工作目录拼的路径。先带调试端口启动桌宠：
 * `cd desktop && npx electron . --remote-debugging-port=9222`）：
 *
 *   node "…/desktop/tools/实拍驱动.mjs" 探测        只打印页面里的按钮，不点
 *   node "…/desktop/tools/实拍驱动.mjs" 表情        开面板 → 逐个点教学节点按钮 → 每个截一张
 *   node "…/desktop/tools/实拍驱动.mjs" 收尾        清掉残留节点 → 真待机截图 → 触发一轮流式
 *   node "…/desktop/tools/实拍驱动.mjs" 录屏 [秒]   ⭐ 用 screencast 高频抓眨眼
 *
 * ═══ 三个踩过的坑（不写下来一定再踩）═══
 *
 * 1. **别用 `连拍` 抓眨眼**。`captureScreenshot` 往返要 200ms+，而闭眼只保持 110ms
 *    —— 会直接从上面跨过去，连拍一百张全是睁眼，得出"没在眨眼"的**错误结论**。
 *    要用 `录屏`（`Page.startScreencast`，每绘制一帧推一张，实测 ~100fps）。
 *
 * 2. **窗口不在前台时 Electron 会节流重绘**，连拍回去一大半是**逐像素完全相同**的帧。
 *    所以每个模式都先 `Page.bringToFront`。
 *
 * 3. **状态会残留**：点过教学节点或跑过一轮 Mock 之后，node/status 还留着，
 *    小人显示的是 sad/teach 之类 —— 而眨眼 overlay 只在 `animName === 'idle'` 时才挂，
 *    那种状态下根本不会眨眼。要么先跑 `收尾` 清节点，要么**重启一个干净进程**。
 *    ⚠️ 别用 `Page.reload` 复位：重载后透明窗口背景会变成不透明黑、尺寸也变，
 *    比不复位还糟。
 */
import fs from 'node:fs'
import path from 'node:path'

const PORT = 9222
const OUT = path.join(process.cwd(), '动作帧', '预览')
const MODE = process.argv[2] || '探测'

const sleep = (ms) => new Promise((r) => setTimeout(r, ms))

async function findPage(timeoutMs = 60000) {
  const t0 = Date.now()
  let lastErr = ''
  while (Date.now() - t0 < timeoutMs) {
    try {
      const r = await fetch(`http://127.0.0.1:${PORT}/json`)
      const list = await r.json()
      const page = list.find((t) => t.type === 'page' && t.webSocketDebuggerUrl)
      if (page) return page
      lastErr = '有端口但没有 page target'
    } catch (e) {
      lastErr = e.message
    }
    await sleep(1000)
  }
  throw new Error('等不到调试端口：' + lastErr)
}

function connect(url) {
  return new Promise((resolve, reject) => {
    const ws = new WebSocket(url)
    let id = 0
    const pending = new Map()
    const handlers = new Map() // method -> fn(params)
    ws.addEventListener('message', (ev) => {
      const msg = JSON.parse(ev.data)
      if (msg.id && pending.has(msg.id)) {
        const { res, rej } = pending.get(msg.id)
        pending.delete(msg.id)
        msg.error ? rej(new Error(JSON.stringify(msg.error))) : res(msg.result)
      } else if (msg.method && handlers.has(msg.method)) {
        handlers.get(msg.method)(msg.params)
      }
    })
    ws.addEventListener('open', () =>
      resolve({
        send(method, params = {}) {
          return new Promise((res, rej) => {
            const n = ++id
            pending.set(n, { res, rej })
            ws.send(JSON.stringify({ id: n, method, params }))
          })
        },
        on(method, fn) {
          handlers.set(method, fn)
        },
        close: () => ws.close()
      })
    )
    ws.addEventListener('error', () => reject(new Error('WS 出错')))
  })
}

async function evaluate(cdp, body) {
  const r = await cdp.send('Runtime.evaluate', {
    expression: `(() => { ${body} })()`,
    returnByValue: true,
    awaitPromise: true
  })
  if (r.exceptionDetails) {
    throw new Error('页面内报错: ' + (r.exceptionDetails.exception?.description || JSON.stringify(r.exceptionDetails)))
  }
  return r.result.value
}

let shotN = 0
async function shot(cdp, name) {
  const r = await cdp.send('Page.captureScreenshot', { format: 'png' })
  const dst = path.join(OUT, `${name}.png`)
  fs.writeFileSync(dst, Buffer.from(r.data, 'base64'))
  console.log(`  📸 ${path.basename(dst)}  (${(r.data.length * 0.75 / 1024).toFixed(0)} KB)`)
  return dst
}

const main = async () => {
  const page = await findPage()
  console.log('连上页面：', page.title)
  const cdp = await connect(page.webSocketDebuggerUrl)
  await cdp.send('Runtime.enable')
  await cdp.send('Page.enable')
  await sleep(4000) // 等 Pixi 贴图加载完

  // ---- 收尾：把节点清掉回到真待机 → 连拍找眨眼 → 触发一轮流式 ----
  if (MODE === '收尾') {
    // ⚠️ 必须先看**当前哪个节点是选中的**再点它 —— 上一轮跑完会残留状态，
    //    不清掉的话"待机截图"截到的其实是残留的那个教学表情（踩过）。
    const cur = await evaluate(
      cdp,
      `
      const bar = document.querySelector('.node-bar');
      const on = bar ? bar.querySelector('button.on') : null;
      return on ? on.textContent.trim() : null;
    `
    )
    if (cur) {
      await evaluate(
        cdp,
        `
        const b = [...document.querySelectorAll('.node-bar button')].find(x => x.textContent.trim() === ${JSON.stringify(cur)});
        if (b) b.click();
      `
      )
      console.log(`  已清掉残留节点「${cur}」，回到待机`)
      await sleep(1000)
    } else {
      console.log('  没有残留节点，本来就是待机')
    }
    await sleep(500)
    await shot(cdp, '_cdp_A_真待机')

    // 连拍找眨眼：闭眼只有 110ms，采样要够密（项目文档里的实测值）
    console.log('  连拍 60 张 @110ms 找眨眼 …')
    for (let i = 1; i <= 60; i++) {
      await shot(cdp, `_cdp连拍_${String(i).padStart(3, '0')}`)
      await sleep(110)
    }

    // 触发一轮 Mock：流式期间**不该**出现说话帧（talk 仍停用），
    // 表现应是"当前教学表情 + 嘴不动"
    const sent = await evaluate(
      cdp,
      `
      const b = [...document.querySelectorAll('button')].find(x => x.textContent.trim() === '正常回复');
      if (!b) return '找不到「正常回复」按钮';
      b.click();
      return 'clicked';
    `
    )
    console.log('  点「正常回复」：', sent)
    await sleep(2500)
    await shot(cdp, '_cdp_B_流式中')
    await sleep(2000)
    await shot(cdp, '_cdp_C_流式中2')

    cdp.close()
    return
  }

  // ---- 录屏抓眨眼：startScreencast 是**每绘制一帧推一张**，没有轮询延迟 ----
  // 连拍（captureScreenshot 往返 ~200ms+）会**直接从 110ms 的闭眼上跨过去**，
  // 得出一堆"没在眨眼"的帧 —— 那是采样的问题，不是功能的问题。
  if (MODE === '录屏') {
    const secs = Number(process.argv[3]) || 25
    try {
      await cdp.send('Page.bringToFront')
    } catch { /* 提不到前台也继续 */ }
    await sleep(500)
    let n = 0
    const frames = []
    cdp.on('Page.screencastFrame', async (p) => {
      n++
      frames.push(p.data)
      try {
        await cdp.send('Page.screencastFrameAck', { sessionId: p.sessionId })
      } catch { /* ignore */ }
    })
    await cdp.send('Page.startScreencast', {
      format: 'png',
      everyNthFrame: 1,
      maxWidth: 400,
      maxHeight: 520
    })
    console.log(`  开始录屏 ${secs} 秒 …`)
    await sleep(secs * 1000)
    await cdp.send('Page.stopScreencast')
    console.log(`  收到 ${n} 帧`)
    let saved = 0
    for (let i = 0; i < frames.length; i++) {
      fs.writeFileSync(path.join(OUT, `_cdp录屏_${String(i + 1).padStart(4, '0')}.png`), Buffer.from(frames[i], 'base64'))
      saved++
    }
    console.log(`  已落盘 ${saved} 帧 → 动作帧/预览/_cdp录屏_*.png`)
    cdp.close()
    return
  }

  if (MODE === '连拍' || MODE === '眨眼') {
    // ⚠️ 眨眼那轮**必须从一个刚启动的干净进程跑**，别指望在当前这轮里"复位"：
    //    · 眨眼 overlay 只在 `animName === 'idle'` 时才挂（App.jsx:684），
    //      点过教学节点 / 跑过一轮 Mock 之后 node 或 status 还留着，
    //      小人显示的是 sad/teach —— 那种状态**根本不会眨眼**，连拍只会得出错误结论
    //    · 试过 `Page.reload` 复位，**不要用**：重载之后透明窗口的背景变成不透明黑、
    //      窗口尺寸也变了，连拍回去帧间乱跳，比不复位还糟
    //    正确做法：杀掉 electron 重新 `npx electron . --remote-debugging-port=9222`
    // ⚠️ 还必须 bringToFront：桌宠窗口不在前台时 Electron 会**节流重绘**，
    //    连拍回去一堆**逐像素完全相同**的帧（实测 60 张里一大半差异 0.00%），
    //    而眨眼只有 110ms —— 整段都会被节流吞掉，得出"没在眨眼"的错误结论。
    try {
      await cdp.send('Page.bringToFront')
      console.log('  已把窗口提到前台（否则渲染会被节流）')
      await sleep(600)
    } catch (e) {
      console.log('  ⚠️ bringToFront 失败：' + e.message)
    }
    const n = Number(process.argv[3]) || (MODE === '眨眼' ? 90 : 40)
    const every = Number(process.argv[4]) || (MODE === '眨眼' ? 60 : 120)
    const prefix = MODE === '眨眼' ? '_cdp眨眼_' : '_cdp连拍_'
    console.log(`连拍 ${n} 张，间隔 ${every}ms …`)
    for (let i = 1; i <= n; i++) {
      await shot(cdp, `${prefix}${String(i).padStart(3, '0')}`)
      await sleep(every)
    }
    cdp.close()
    return
  }

  // ---- 打开聊天面板：双击 .pet-area（App.jsx 的 onDoubleClick → toggleChat(true)）----
  const opened = await evaluate(
    cdp,
    `
    const el = document.querySelector('.pet-area');
    if (!el) return '找不到 .pet-area';
    el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));
    return 'ok';
  `
  )
  console.log('双击 .pet-area：', opened)
  await sleep(1200)

  const btns = await evaluate(
    cdp,
    `return [...document.querySelectorAll('button')].map(b => b.textContent.trim());`
  )
  console.log('面板打开后的按钮：', JSON.stringify(btns))
  if (MODE === '探测') {
    cdp.close()
    return
  }

  await shot(cdp, '_cdp_00_待机')

  // ---- 逐个节点按钮：点一下 → 等动画切过去 → 截图 ----
  //
  // ⚠️ 每轮都**先确认面板还在**：实测点了一两个之后 `.node-bar` 会从 DOM 里消失
  //    （面板收起），继续盲点只会一路"找不到按钮"。所以缺了就补一次双击再点。
  const nodes = ['Assess', 'Teach', 'Ask', 'Hint', 'Correct', 'Test', 'UpdateProfile', 'Reflect']
  for (const n of nodes) {
    const state = await evaluate(
      cdp,
      `
      const bar = () => document.querySelector('.node-bar');
      if (!bar()) {
        const el = document.querySelector('.pet-area');
        if (el) el.dispatchEvent(new MouseEvent('dblclick', { bubbles: true, cancelable: true, view: window }));
        return 'reopened';
      }
      return 'ok';
    `
    )
    if (state === 'reopened') {
      console.log(`  ↻ 面板不在了，已重新双击打开（准备点 ${n}）`)
      await sleep(1000)
    }

    const ok = await evaluate(
      cdp,
      `
      const bar = document.querySelector('.node-bar');
      if (!bar) return '面板还是没打开（.node-bar 不在）';
      const b = [...bar.querySelectorAll('button')].find(x => x.textContent.trim() === ${JSON.stringify(n)});
      if (!b) return '面板在，但没有按钮 ' + ${JSON.stringify(n)} + '；现有：' + [...bar.querySelectorAll('button')].map(x=>x.textContent.trim()).join('/');
      b.click();
      return 'clicked';
    `
    )
    if (ok !== 'clicked') {
      console.log(`  ⚠️ ${n}: ${ok}`)
      continue
    }
    await sleep(900) // 等 Pixi 换贴图 + 尺寸重算
    await shot(cdp, `_cdp_${String(++shotN).padStart(2, '0')}_${n}`)
  }

  cdp.close()
  console.log('\n完成。')
}

main().catch((e) => {
  console.error('❌', e.message)
  process.exit(1)
})
