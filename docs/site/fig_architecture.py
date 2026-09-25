"""图 1：系统架构 —— 一次教学回合的本机路径（zh/en）。

布局约束（diagram-design 六条连接线硬规则）：
  · 全部正交折线，无斜线
  · 每条连线独立可追踪，无重叠
  · 进出同一盒同一边的连线各有独立接入点
  · 无连线穿越非端点盒 —— 拓扑按此约束排布（课程索引正对课程检索、Provider 正对 Runtime）
节点 9 个、连线 8 条，在复杂度预算内（≤9 节点 / ≤12 连线）。
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sitefig import *  # noqa

T = {
    "zh": {
        "eyebrow": "系统架构",
        "title": "一次教学回合，全程在本机完成",
        "sub": "CLI 只与本机 Gateway 通信，Gateway 只监听环回地址。教学策略图读到的是带版本的证据与状态，"
               "产出结构化决策后，再由 Runtime 按显式绑定执行，并写下可回放的审计事件。",
        "cli": ("TypeScript CLI", "会话 · 续接 · 配置"),
        "gw": ("Gateway 命令校验", "127.0.0.1 环回"),
        "pg": ("教学策略图", "LangGraph · 结构化决策"),
        "rt": ("Runtime 能力绑定", "显式绑定 · 审计事件"),
        "ret": ("课程检索", "版本化索引 · 页码定位"),
        "prov": ("Provider 适配器", "Profile 与密钥分离"),
        "db": ("SQLite 会话与事件", "Attempt · BKT · 审计 outbox"),
        "replay": ("脱敏只读回放", "字段白名单 · 不返回答案与凭据"),
        "lib": ("课程索引（版本化）", "只允许已审核资源"),
        "frame": "以上全部运行在用户本机 · 无账户 · 不上传会话",
        "f_http": "本机 HTTP",
        "f_dec": "结构化决策",
        "f_write": "事务写入",
        "f_replay": "只读",
        "f_call": "调用",
        "f_ret": "检索",
        "foot": "依据 DeepProf v0.6.2 设计文档 §4–§5 与 §7。节点为逻辑角色，不是部署拓扑；"
                "本机只读回放页通过字段白名单读取回放事件。",
    },
    "en": {
        "eyebrow": "Architecture",
        "title": "One teaching turn, start to finish, on your machine",
        "sub": "The CLI talks only to a loopback Gateway. The policy graph receives versioned evidence "
               "and state, returns a structured decision, and the Runtime then binds capabilities "
               "explicitly and writes replayable audit events.",
        "cli": ("TypeScript CLI", "sessions · resume · config"),
        "gw": ("Gateway", "command layer · 127.0.0.1"),
        "pg": ("Policy graph", "LangGraph · structured decision"),
        "rt": ("Runtime binding", "explicit binding · audit events"),
        "ret": ("Course retrieval", "versioned index · page locators"),
        "prov": ("Provider adapter", "profile and secrets separated"),
        "db": ("Local SQLite", "attempts · BKT · audit outbox"),
        "replay": ("Redacted replay", "field allowlist · no answers or secrets"),
        "lib": ("Course index", "versioned · reviewed resources only"),
        "frame": "All of the above runs on the user's machine · no account · no session upload",
        "f_http": "local HTTP",
        "f_dec": "structured decision",
        "f_write": "transactional write",
        "f_replay": "read-only",
        "f_call": "bound call",
        "f_ret": "retrieve",
        "foot": "Based on the DeepProf v0.6.2 design document §4–§5 and §7. Nodes are logical roles, "
                "not a deployment topology; the read-only browser replay page reads replay events "
                "through a field allowlist.",
    },
}


def build(lang: str, out: Path):
    t = T[lang]
    fig, ax, H = new_canvas(10.6, 6.5)

    def draw_node(x, yb, w, h, spec, *, focal=False, edge=INK_2, fill=PAPER,
                  tcol=INK, scol=MUTED, fs=11.0, subfs=8.6, lw=1.25, gap=1.85, ty=0.70):
        name, sub = spec
        box(ax, x, yb, w, h, "", None, fill=fill, edge=edge, lw=lw)
        lines = wrap_to(ax, sub, w - 2.4, subfs)
        ax.text(x + w / 2, yb + h * ty, name, ha="center", va="center",
                fontsize=fs, color=tcol, family=SANS, zorder=5, fontweight="bold")
        for j, ln in enumerate(lines):
            ax.text(x + w / 2, yb + h * 0.30 - j * gap, ln, ha="center", va="center",
                    fontsize=subfs, color=scol, family=SANS, zorder=5)

    # ---------------- 页头
    caption(ax, 1.5, H - 1.9, t["eyebrow"], fs=10, color=BRAND)
    ax.plot([1.5, 6.4], [H - 3.0, H - 3.0], color=BRAND, lw=1.6,
            solid_capstyle="butt", zorder=4)
    ax.text(1.5, H - 5.4, t["title"], fontsize=15, color=INK, family=SANS,
            fontweight="bold", ha="left", va="center", zorder=4)
    para(ax, 1.5, H - 8.3, t["sub"], width=90, fs=10, color=INK_2, lh=2.0)

    # ---------------- 竖向预算
    FY0, FY1 = 4.6, 46.4
    RY, RH = 25.4, 7.6                 # 主链
    M2Y = RY - 2.6 - RH                # 第二层（Provider / 课程索引）
    SY, SH = 6.0, 6.4                  # 存储层

    ax.add_patch(FancyBboxPatch((1.5, FY0), 97, FY1 - FY0,
                                boxstyle="round,pad=0,rounding_size=1.0",
                                linewidth=1.0, edgecolor=RULE, facecolor=PAPER_2,
                                linestyle=(0, (6, 4)), zorder=0))
    caption(ax, 3.0, FY1 - 1.7, t["frame"], fs=9.5, color=MUTED)

    # ---------------- 主链 5 节点
    W, GAP = 16.6, 3.0
    xs = [3.0 + i * (W + GAP) for i in range(5)]
    for i in range(4):
        el = BRAND if i == 2 else INK_2
        draw_node(xs[i], RY, W, RH, t[("cli", "gw", "pg", "rt")[i]],
                  focal=(i == 2), fill=("#EAF1FF" if i == 2 else PAPER),
                  edge=el, tcol=(BRAND if i == 2 else INK),
                  scol=(BLUE_500 if i == 2 else MUTED), lw=(2.0 if i == 2 else 1.25))
    draw_node(xs[4], RY, W, RH, t["ret"])

    # ---------------- 第二层 2 节点（各自正对其上游）
    draw_node(xs[3], M2Y, W, RH, t["prov"])
    draw_node(xs[4], M2Y, W, RH, t["lib"])

    # ---------------- 存储层 2 节点
    draw_node(3.0, SY, 30.0, SH, t["db"], edge=MUTED, tcol=INK, fs=10.4, subfs=8.4,
              lw=1.15, ty=0.64)
    draw_node(37.0, SY, 34.0, SH, t["replay"], edge=MUTED, tcol=INK, fs=10.4, subfs=8.4,
              lw=1.15, ty=0.64)

    # ---------------- 连线
    mid = RY + RH / 2
    for i in range(4):
        elbow(ax, (xs[i] + W, mid), (xs[i + 1], mid), color=INK_2, lw=1.25, arrow=True)
    LBL_Y = RY + RH + 2.7
    edge_label(ax, (xs[0] + W + xs[1]) / 2, LBL_Y, t["f_http"], fs=8.6)
    edge_label(ax, (xs[2] + W + xs[3]) / 2, LBL_Y, t["f_dec"], fs=8.6)

    # Runtime → Provider / 课程检索 → 课程索引（均为同轴竖直，互不重叠）
    robot = RY
    for k, lbl in ((3, t["f_call"]), (4, t["f_ret"])):
        cx = xs[k] + W * 0.62
        elbow(ax, (cx, robot), (cx, M2Y + RH), color=INK_2, lw=1.25, arrow=True)
        edge_label(ax, cx + 1.9, (robot + M2Y + RH) / 2, lbl, fs=8.4, ha="left")

    # Gateway → SQLite（独立竖直通道，穿过空白带）
    dgn = 27.0
    elbow(ax, (dgn, RY), (dgn, SY + SH), color=MUTED, lw=1.15, arrow=True)
    edge_label(ax, dgn + 1.9, (RY + SY + SH) / 2, t["f_write"], fs=8.4, ha="left")
    # SQLite → 脱敏回放
    smid = SY + SH / 2
    elbow(ax, (33.0, smid), (37.0, smid), color=MUTED, lw=1.15, arrow=True)
    edge_label(ax, 35.0, smid + 1.55, t["f_replay"], fs=8.4)

    # ---------------- 页脚
    para(ax, 1.5, 3.2, t["foot"], width=96, fs=8.6, color=MUTED, lh=1.9)

    save(fig, out)


if __name__ == "__main__":
    outdir = Path(sys.argv[1])
    for lang in ("zh", "en"):
        build(lang, outdir / f"fig-architecture-{lang}")
        print("ok", lang)
