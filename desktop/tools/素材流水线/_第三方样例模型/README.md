# 第三方样例模型（Live2D 官方样例 · 仅开发期使用，**不进交付物**）

> ### ⚠️ 先说清楚：**交付物里本目录只有这份说明，没有模型**
>
> `haru/`、`shizuku/`、`live2dcubismcore.min.js` **都不在本仓库内** ——
> 授权原因见 §二（样例模型条款禁止再分发）。下面是**开发机上的那份**的记录，
> 保留它是为了让评审看清「移走了什么、为什么移、怎么恢复」。
>
> 换句话说：本文档描述的三个目录**在这里是找不到的**，这是**有意为之**，不是漏拷。

> 移入日期：**2026-09-19**（从桌宠交付目录移出）
> 移出前位置：`desktop/src/renderer/public/live2d/`
> 开发机位置：`_素材工作区/_第三方样例模型/`（**不在本仓库内**）
> 相关文档：[`../../../docs/DeepProf_素材授权调研.md`](../../../docs/DeepProf_素材授权调研.md)、
> [`../../../docs/DeepProf_原创角色设定.md`](../../../docs/DeepProf_原创角色设定.md) §三、
> [`../../../docs/ASSETS_LICENSE.md`](../../../docs/ASSETS_LICENSE.md) §五 / §六

---

## 一、这里是什么

| 目录 | 内容 | 文件数 / 体积 | 说明 |
| --- | --- | --- | --- |
| `haru/` | Live2D 官方样例模型 **Haru**（`haru_greeter_t03`，Cubism 4 `.moc3`）：1 个模型 + 2 张 2048 贴图 + 8 个表情 `F01~F08` + 5 个动作 `Idle / m05 / m07 / m14 / m15` + 物理 / 姿势文件 | 19 个文件 · 3.05 MB | 链路验证**实际用的就是它** |
| `shizuku/` | Live2D 官方样例模型 **Shizuku** 的**音效文件**（`flickHead` / `pinchIn` / `pinchOut` / `shake` / `tapBody`，mp3） | 14 个文件 · 0.85 MB | ⚠️ **只有音效，没有模型本体**（没有 `.moc3` / `model3.json`）。它是当初当"备用模型"拷进来的，实际**从来没有被代码引用过**，桌宠里跑不起来 |

合计 **33 个文件 · 约 3.90 MB**。

> 桌宠代码里真正写死的路径只有一个（`desktop/src/renderer/src/Live2DPet.jsx`）：
> ```js
> const MODEL_URL = './live2d/haru/haru_greeter_t03.model3.json'
> ```
> 也就是说：**`shizuku/` 那堆 mp3 是纯占位垃圾**，可以单独处置；`haru/` 才是链路验证需要的。

---

## 二、来自哪里、什么许可

- **来源**：Live2D Cubism 官方发布的官方样例模型（Sample Models），随 Cubism SDK 分发。
- **许可**：**Live2D 样例模型使用条款（Live2D Sample Model Terms of Use）**
  <https://www.live2d.com/eula/live2d-sample-model-terms_en.html>
  （注意：这不等于 Cubism Core 的专有许可，两者是**两份不同的授权**）
- 我们（学生 / 个人身份）属于条款里的 **"General User"**：**开发期拿来验证技术链路，没有问题**。
- 条款的**两条硬约束**（这是它们不能进交付物的直接原因）：
  1. **禁止修改素材** → 所以**不能**把官方模型改成我们的深蓝配色/学院风，这条路是堵死的；
  2. **不得作为自己的素材再分发** → 它不是我们的原创素材，**不得出现在最终交付物中**。

> **✅ 补充（2026-09-19，晚些时候）**：`desktop/src/renderer/public/live2dcubismcore.min.js`
> （Live2D Cubism Core 运行时，约 207 KB）**后来也一并移到了本目录**（本目录下同名文件）。
>
> 移它的理由**不是"授权禁止"**：它属「Live2D 专有软件许可协议」下的 **Redistributable Code**，
> **本来允许随应用分发**（与样例模型是两份授权，别混为一谈，详见 `ASSETS_LICENSE.md` §5.1）。
> 真正理由是**已经用不上**——样例模型移走 + 右键菜单 Live2D 入口停用之后，
> 这个专有二进制在交付物里只是死重量。移走只赚不亏。
>
> ⚠️ **副作用（恢复链路验证时必看）**：它不在 npm 上，本地也没有第二份拷贝。
> 所以**本目录这一份是该运行时唯一的本地副本**，别删。恢复办法见 §五。

---

## 三、为什么不在交付物里

依据 `_文档/DeepProf_素材授权调研.md` 与 `_文档/DeepProf_原创角色设定.md` §三 的结论：

> **Haru / Shizuku 是 Live2D 官方样例模型，仅可用于开发期验证链路，【不进入交付物】。**

三条理由，按严重程度排：

1. **它不是我们的原创素材。** 交付物里混进第三方版权素材，直接污染**原创性证明链** ——
   而软著申请要求独创性，成果转化也要求素材权属干净（这正是当初弃用「鲸鱼娘」的同一个理由）。
2. **条款禁止修改**，所以它永远只能是一张"别人的脸"，当不了我们的角色。
   既然注定要被原创角色替换，**早移除比晚移除好** —— 留着它只会让人误以为那是我们的形象。
3. **体积**：3.9 MB 里 `haru` 的 `.moc3` + 两张 2048 贴图占了大头，
   交付包里带着一个用不上的官方模型，评审一眼就能看出"这不是你们做的"。

> 结论：**交付前 `desktop/src/renderer/public/live2d/` 必须是空的（现在已经是了）。**
> 本目录只是把它挪到开发区存着，**它仍然不是交付物**。

---

## 四、⚠️ 移走之后的后果：切到 Live2D 会"静默空白"（预期行为，不是 bug）

桌宠右键菜单里有个「**切到 Live2D（链路测试）**」入口。
**模型文件移走后，从那里切过去会加载失败。以下是实际表现（已在代码层核实）：**

### 实际表现：**不崩溃、不白屏、不报错弹窗 —— 角色区域静默变空白**

按发生顺序：

1. `Live2DPet` 挂载，先建好 Pixi 应用（`backgroundAlpha: 0`，canvas 挂进 `.live2d-host`，铺满 100%×100%）。
   **此时屏幕上是"一块透明的空画布"** —— 窗体和桌面是透明穿透的，看起来就是"小人没了"。
2. 接着请求 `./live2d/haru/haru_greeter_t03.model3.json` —— **文件不存在**。
   请求走 `pixi-live2d-display` 的 `XHRLoader`：
   `xhr.onload` 里只要 **status 不是 200/0，或者 response 为空**，就转 `xhr.onerror()`，
   抛出 `NetworkError('Network error', url, status)` → **Promise reject**。
   - 开发模式（Vite dev server）：404 → `status = 404` → `NetworkError`
   - 打包后（`file://`）：`status = 0` 且 response 为空 → `NetworkError`
3. `Live2DPet.jsx` 的 `.catch()` **兜住了这个 rejection**（第 93~96 行）：
   ```js
   console.error('[Live2D] 模型加载失败:', err)
   statusRef.current && statusRef.current('error', ...)
   ```
   → 所以**不会**冒泡成未捕获异常，**不会**让渲染进程崩溃或重启。
4. 表现汇总：

   | 项 | 实际结果 |
   | --- | --- |
   | 应用崩溃 / 渲染进程挂掉 | ❌ **不会** |
   | 白屏 / 整窗报错 | ❌ **不会**（窗口还在、还置顶、还能被拖走和漫游） |
   | 角色 | ✅ **静默空白** —— 透明 canvas 上什么都没画 |
   | 终端输出 | ✅ 有一行 `[渲染进程] [Live2D] 模型加载失败: Network error    @ <url>:<line>`（主进程把渲染进程 `console.error` 及以上转发到终端，见 `src/main/index.js` 的 `console-message` 监听） |
   | App 内部状态 | ✅ `status` 被置为 `'error'` → 气泡/台词会走 `persona.js` 的 error 分支（画面空白但可能还在说话） |
   | 点击角色 | ✅ **无反应也不报错**（`onTap` 里 `if (!model) return`；表情/动作 effect 同样 `if (!model) return`；错误只发生一次，不会刷屏） |

5. **一个要知道的坑**：`Live2DPet.jsx` **不上报命中区**（只有 `FramePet.jsx` / `RigPet.jsx` 会调
   `setHitRegion`）。所以切到 Live2D 后，主进程用的仍是 **PNG 模式最后上报的那块 mask（陈旧值）** ——
   好处是**右键菜单在那块区域还能点出来，还能切回 PNG**；
   坏处是画面上什么都没有，用户**不知道该往哪儿点**，容易以为桌宠挂了。

> 一句话给后面的人：**「切到 Live2D = 小人凭空消失、终端有一行 `[Live2D] 模型加载失败`」，
> 这是模型已移出的预期结果，不是新 bug。** 想要它不出现，就按下面第五节临时放回去，
> 或者等 `App.jsx` 的 Live2D 入口加上保护（提示"模型未安装"并禁用该菜单项）。

---

## 五、开发期想重新启用链路验证，怎么放回去

**把目录挪回去就行**（用移动，别用复制，免得出现两份）：

```powershell
$dst = "C:\Users\87092\Desktop\DeepProf\_素材工作区\_第三方样例模型"
$pub = "C:\Users\87092\Desktop\DeepProf\desktop\src\renderer\public\live2d"
New-Item -ItemType Directory -Force -Path $pub | Out-Null
Move-Item "$dst\haru" $pub
Move-Item "$dst\shizuku" $pub   # 可选：没人用它，只有音效
```

放回去之后（⚠️ 2026-09-19 起**比以前多了两步**，因为运行时和 `<script>` 也被移走了）：

0. **先把运行时也放回去**（否则一切照旧会白屏）：
   ```powershell
   Move-Item "$dst\live2dcubismcore.min.js" `
             "C:\Users\87092\Desktop\DeepProf\desktop\src\renderer\public\"
   ```
   然后**在 `src/renderer/index.html` 里把下面这行加回来**，
   位置必须在 `<script type="module" src="/src/main.jsx">` **之前**
   （它得是**普通 script**，不能是 module —— `window.Live2DCubismCore` 必须在
   pixi 模块求值前就位）。文件里已留了带说明的注释块，照着加即可：
   ```html
   <script src="./live2dcubismcore.min.js"></script>
   ```
1. **开发模式**：`desktop\启动桌宠.bat`（或 `npm run dev`）重载即可 ——
   `Live2DPet.jsx` 的 `MODEL_URL` 一直指向 `./live2d/haru/haru_greeter_t03.model3.json`。
   ⚠️ **但右键菜单入口还是停用的，切不过去。** 要验证链路，得临时把 `App.jsx` 里
   那个菜单按钮的 `window.alert(...)` 分支改回 `setMode('live2d')`。
   （`Live2DPet` 现在是 `React.lazy` 动态加载的，**不用**改回静态 import ——
   切过去时它会自己按需加载。）
2. **打包产物**：`out/renderer/live2d/` 是**构建时从 `public/` 拷过去的**，
   所以**必须重新构建**才会生效（不重新构建就还是旧的）。
   ⚠️ 交付前记得**再移出去一次**，并**重新构建一遍**，否则模型和运行时都会留在 `out/` 里跟着交付包走。
3. 其余前置条件（不然切过去一样是黑屏，别误判成模型问题）：
   - 只用 Cubism 4（`.moc3`），import 路径是 `pixi-live2d-display/cubism4`；
   - `live2dSetup.js` 里 `install(PIXI)` 必须在任何 Pixi 对象之前执行。
   （详见 `desktop/docs/启动说明.md` §5.3）

**验证完请务必再把它移回本目录**，并在 `ASSETS_LICENSE.md` 里更新状态 —— 别让它又悄悄漂回交付目录。

---

## 六、处置建议（可选）

- `shizuku/`：**从未被代码引用**（没有模型本体，只有 14 个音效 mp3），
  留着只是为了"以后可能要用"。如果确认不用，可以直接删掉，不影响任何链路。
- `haru/`：**建议保留**，它是"PNG → Live2D 链路"唯一的验证手段，
  等原创角色做完 Live2D 版、链路重新验证通过之后再决定去留。
