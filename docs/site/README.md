# 站点配图与品牌资产的生成脚本

`index.html`（仓库根）用到的全部图片都由本目录的脚本生成，输出到仓库根的 `media/`。
本目录位于 `docs/site/`，因此下面命令里的输出路径是 `../../media`。
脚本只依赖 **matplotlib、Pillow、fontTools、numpy**，不需要浏览器或 SVG 光栅化工具
（沙箱里没有 chromium，也没有 cairosvg 可装，所以示意图全部用 matplotlib 直接画，
不是"画 SVG 再转 PNG"）。

## 重新生成

```sh
pip install matplotlib pillow fonttools numpy

export DP_FONT_DIR=/path/to/fonts      # 见下方"字体"一节
python fig_architecture.py ../../media    # 图 1   系统架构（zh + en）
python fig_others.py     ../../media      # 图 2–5 闭环 / 条件矩阵 / 证据构成 / BKT
python make_promo.py     ../../media      # 品牌资产、Hero 大图、OG 分享卡

# 只重出某一张：
python fig_others.py ../../media loop bkt
```

`fig_architecture.py` 与 `fig_others.py` 各自接受"输出目录"作为第一个参数，
`fig_others.py` 还可以在第二个参数起指定只跑哪几张图。

## 输出清单

| 文件 | 内容 |
| --- | --- |
| `fig-architecture-{zh,en}.{png,svg}` | 图 1 · 系统架构 |
| `fig-loop-{zh,en}.{png,svg}` | 图 2 · 学情驱动的自适应教学闭环 |
| `fig-conditions-{zh,en}.{png,svg}` | 图 3 · A/B/C 条件矩阵 |
| `fig-evidence-{zh,en}.{png,svg}` | 图 4 · M1–M3 批次完成构成 |
| `fig-bkt-{zh,en}.{png,svg}` | 图 5 · BKT 与动作族（含保留的不利结果） |
| `hero.png` | 主视觉背景 3200×1400 |
| `og-card.png` | 社交分享卡 1200×630 |
| `logo.svg` / `logo-white.svg` | 标识矢量（官方路径，双色） |
| `wordmark.svg` / `wordmark-white.svg` | 字标：标识 + `DeepProf` 字形轮廓 |
| `wordmark.png` / `wordmark-white.png` | 字标位图版 |
| `logo-512.png` / `logo-white-512.png` | 标识位图（favicon / 页脚） |

## 字体

中文必须用 **Noto Sans CJK SC / Noto Serif CJK SC 的简体字面**。
直接装 `.ttc` 是不够的：Noto CJK 的 `.ttc` 里 SC/TC/JP/HK/KR 五个字面同包，
matplotlib 只会解析到 index 0（日文），`直骨画次令` 等字会出日文字形。
需要先把 SC 字面抽成独立的 `.otf`：

```python
from fontTools.ttLib import TTCollection
for src, want, out in (
    ("NotoSansCJK-Regular.ttc",  "Noto Sans CJK SC",  "NotoSansCJKsc-Regular.otf"),
    ("NotoSansCJK-Bold.ttc",     "Noto Sans CJK SC",  "NotoSansCJKsc-Bold.otf"),
    ("NotoSerifCJK-Regular.ttc", "Noto Serif CJK SC", "NotoSerifCJKsc-Regular.otf"),
    ("NotoSerifCJK-Bold.ttc",    "Noto Serif CJK SC", "NotoSerifCJKsc-Bold.otf"),
):
    for face in TTCollection(src, lazy=False).fonts:
        if face["name"].getDebugName(1) == want:
            face.save(out); break
```

把四份 `.otf` 放进本目录的 `fonts/` 子目录，或把该目录指给 `DP_FONT_DIR`。
`sitefig.py` 会在导入时把它们注册进 matplotlib。

`make_promo.py` 另需 `NotoSerifCJKsc-Bold.otf`，用于字标里的 `DeepProf` 拉丁字形
——思源宋体的拉丁部分就是 Source Serif，与网页上加载的 Source Serif 4 同源，
所以字标和正文标题看起来是同一套字。

## 坐标约定

`sitefig.new_canvas(w_in, h_in)` 建出的画布，x 轴固定 `0..100`，y 轴为 `h_in / w_in * 100`，
两个方向的单位长度相同，因此画正圆不需要额外补偿。

**所有折行都走 `sitefig.wrap_to()`，它用 matplotlib 渲染器实测字符串宽度**，
不是按字符数猜。早期版本按"全角算 2、半角算 1"估算，长英文串会溢出盒子。

## 示意图的连线规则

`fig_architecture.py` 与 `fig_others.py` 里的拓扑是**按连线规则反推着摆的**，
不是画完再调：

- 全部走正交折线（`sitefig.elbow()`，圆角 `r`），没有斜线
- 每条连线独立可追踪，不重叠、不共路径
- 进出同一个盒子同一条边的连线，各自有独立接入点（例如 Runtime 分别接
  Provider 与课程检索时，从右边两个不同高度引出）
- **没有连线穿过非端点盒子**——课程索引正对课程检索、Provider 正对 Runtime，
  就是为了让竖直连线不用绕行；改布局时要先确认这一点仍然成立

## 数据图的诚实性约束

图 4 与图 5 遵循几条硬规则，改图时不要破坏：

- 堆叠条画的是**整体与各部分**，不是截断柱；分母始终写明
- AUC 用**点图 + 0.5 随机参考线**，不用截断的柱状图（柱高从基线量起，
  截断会夸大差异）
- 失败、截断、未触发的格子**保留在图上**，不合并进"完成"
- 不利结果与有利结果同等排版，并显式标注（图 5 的珊瑚色就是留给它的）
