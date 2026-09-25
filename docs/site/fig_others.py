"""图 2–5：教学闭环 / A·B·C 条件矩阵 / M1–M3 证据构成 / BKT 保留的不利结果。

全部输出 zh + en 两版。数据图遵循 scientific-visualization-honesty：
  · 构成条与基线诚实（堆叠条为整体各部分，非截断柱）
  · AUC 用点图 + 0.5 随机参考线，不用截断柱
  · 不利结果与有利结果同等呈现，并显式标注
"""
from __future__ import annotations
import sys
from pathlib import Path

import numpy as np
import matplotlib.pyplot as plt

sys.path.insert(0, str(Path(__file__).resolve().parent))
from sitefig import *  # noqa
from sitefig import _head  # noqa

# ==================================================================== 图 2 闭环
LOOP = {
    "zh": dict(
        eyebrow="教学闭环", title="学情驱动的自适应教学闭环",
        sub="每完成一轮，证据与学情状态回到中枢；下一轮的教学决策从更新后的状态出发。"
            "虚线是写回，实线是工作流。",
        hub=("学情状态与证据链", "每次循环追加，不覆盖"),
        st=[("学生作答", "输入或答题"), ("检索课程证据", "教材页码可定位"),
            ("合格判定门槛", "提示后重试 / 待人工评分不进入"),
            ("读取 · 更新 BKT", "仅 C 组，且仅对合格证据"),
            ("策略图产出决策", "结构化动作与停止条件"),
            ("执行并记录", "审计事件可回放")],
        ring="顺序推进", write="写回状态",
        focal=2,
        foot="依据 DeepProf v0.6.2 设计文档 §1、§3。闭环描述的是系统设计的链路；"
             "当前公开证据只覆盖其中前两层的工程执行能力。",
    ),
    "en": dict(
        eyebrow="Teaching loop", title="A learning-state-driven teaching loop",
        sub="Each turn returns evidence and learner state to the hub; the next teaching decision "
            "starts from the updated state. Dashed is write-back, solid is workflow.",
        hub=("Learner state and evidence", "appended every turn, never overwritten"),
        st=[("Student response", "input or answer"), ("Retrieve course evidence", "locatable pages"),
            ("Reliability gate", "hinted retries and pending grades excluded"),
            ("Read · update BKT", "group C only, reliable answers only"),
            ("Policy graph decision", "structured action and stop condition"),
            ("Execute and record", "replayable audit events")],
        ring="forward flow", write="state write-back",
        focal=2,
        foot="Based on the DeepProf v0.6.2 design document §1 and §3. The loop describes the "
             "designed chain; current published evidence covers only the first two layers.",
    ),
}


def fig_loop(lang, out):
    t = LOOP[lang]
    fig, ax, H = new_canvas(9.6, 8.3)
    C = (50.0, 43.0)
    R = 27.0
    a, b = 10.0, 3.6          # 站点半宽 / 半高
    A, B = 10.5, 5.4          # 中枢半宽 / 半高

    caption(ax, 1.5, H - 1.9, t["eyebrow"], fs=10, color=BRAND)
    ax.plot([1.5, 6.4], [H - 3.0, H - 3.0], color=BRAND, lw=1.6, solid_capstyle="butt")
    ax.text(1.5, H - 5.6, t["title"], fontsize=15, color=INK, family=SANS,
            fontweight="bold", ha="left", va="center")
    para(ax, 1.5, H - 8.6, t["sub"], width=88, fs=9.8, color=INK_2, lh=1.95)

    th = [np.deg2rad(90 - k * 60) for k in range(6)]

    def boxdist(ux, uy):
        cand = []
        if abs(ux) > 1e-9:
            cand.append(abs(a / ux))
        if abs(uy) > 1e-9:
            cand.append(abs(b / uy))
        return min(cand)

    # --- 环形箭头（先画，压在站点下面）
    for k in range(6):
        k2 = (k + 1) % 6
        d1 = boxdist(np.cos(th[k]), np.sin(th[k])) / R
        d2 = boxdist(np.cos(th[k2]), np.sin(th[k2])) / R
        s0 = th[k] - d1 * 1.06
        s1 = th[k2] + d2 * 1.06
        if s1 > s0:
            s1 -= 2 * np.pi
        n = 80
        ts = np.linspace(s0, s1, n)
        ax.plot(C[0] + R * np.cos(ts), C[1] + R * np.sin(ts), color=MUTED,
                lw=1.4, zorder=1, solid_capstyle="butt")
        _head(ax, (C[0] + R * np.cos(s1), C[1] + R * np.sin(s1)),
              (C[0] + R * np.cos(ts[-2]), C[1] + R * np.sin(ts[-2])), color=MUTED, size=1.0)

    # --- 虚线写回辐条
    for k in range(6):
        ux, uy = np.cos(th[k]), np.sin(th[k])
        ds = boxdist(ux, uy)
        dh = min(abs(A / ux) if abs(ux) > 1e-9 else 1e9,
                 abs(B / uy) if abs(uy) > 1e-9 else 1e9)
        p0 = (C[0] + (R - ds) * ux, C[1] + (R - ds) * uy)
        p1 = (C[0] + (dh + 1.5) * ux, C[1] + (dh + 1.5) * uy)
        ax.plot([p0[0], p1[0]], [p0[1], p1[1]], color=BLUE_300, lw=1.1,
                linestyle=(0, (5, 4)), zorder=1)
        _head(ax, p1, p0, color=BLUE_300, size=0.85)

    # --- 站点
    for k in range(6):
        cx, cy = C[0] + R * np.cos(th[k]), C[1] + R * np.sin(th[k])
        focal = (k == t["focal"])
        ax.add_patch(FancyBboxPatch((cx - a, cy - b), 2 * a, 2 * b,
                                    boxstyle="round,pad=0,rounding_size=0.85",
                                    linewidth=2.0 if focal else 1.2,
                                    edgecolor=ACCENT if focal else INK_2,
                                    facecolor="#FFF1EC" if focal else PAPER, zorder=3))
        bxc, byc = cx - a + 2.5, cy + b - 2.3
        ax.add_patch(plt.Circle((bxc, byc), 1.35, facecolor=ACCENT if focal else MUTED,
                                edgecolor="none", zorder=4))
        ax.text(bxc, byc - 0.12, f"{k+1}", ha="center", va="center", fontsize=8.0,
                color=PAPER, family=SANS, fontweight="bold", zorder=5)
        ax.text(cx + 1.25, byc - 0.12, t["st"][k][0], ha="center", va="center", fontsize=10.6,
                color=ACCENT if focal else INK, family=SANS, fontweight="bold", zorder=4)
        lines = wrap_to(ax, t["st"][k][1], 2 * a - 1.8, 8.3)
        for j, ln in enumerate(lines[:2]):
            ax.text(cx, cy - b + 2.55 - j * 1.6, ln, ha="center", va="center", fontsize=8.3,
                    color=MUTED, family=SANS, zorder=4)

    # --- 中枢
    ax.add_patch(FancyBboxPatch((C[0] - A, C[1] - B), 2 * A, 2 * B,
                                boxstyle="round,pad=0,rounding_size=1.0",
                                linewidth=0, facecolor=NAVY_800, zorder=4))
    ax.text(C[0], C[1] + 2.0, t["hub"][0], ha="center", va="center", fontsize=11.0,
            color=PAPER, family=SANS, fontweight="bold", zorder=5)
    for j, ln in enumerate(wrap_to(ax, t["hub"][1], 2 * A - 2.4, 8.3)):
        ax.text(C[0], C[1] - 1.0 - j * 1.7, ln, ha="center", va="center", fontsize=8.3,
                color=BLUE_200, family=SANS, zorder=5)

    # --- 图例条
    lgy = 8.2
    ax.plot([1.5, 98.5], [lgy + 3.4, lgy + 3.4], color=RULE, lw=0.9)
    ax.plot([1.5, 5.0], [lgy, lgy], color=MUTED, lw=1.4)
    _head(ax, (5.6, lgy), (5.0, lgy), color=MUTED, size=0.9)
    caption(ax, 6.6, lgy, t["ring"], fs=8.6, color=MUTED)
    ax.plot([26.0, 29.5], [lgy, lgy], color=BLUE_300, lw=1.1, linestyle=(0, (5, 4)))
    _head(ax, (30.4, lgy), (29.7, lgy), color=BLUE_300, size=0.85)
    caption(ax, 31.4, lgy, t["write"], fs=8.6, color=MUTED)

    para(ax, 1.5, 4.6, t["foot"], width=96, fs=8.5, color=MUTED, lh=1.85)
    save(fig, out)


# ================================================================ 图 3 条件矩阵
COND = {
    "zh": dict(
        eyebrow="对照设计", title="A / B / C 三组：只改变声明的教学策略状态",
        sub="课程、题目、证据检索、模型版本、采样参数、停止规则与会话时长在组间固定。"
            "真实模型试跑不得视为公平的效果比较——现有批次中 A 组截断比例明显更高。",
        cols=["A", "B", "C"], roles=["基线", "策略图", "策略图 + 学情"],
        varied="组间唯一变量", fixed="组间固定",
        rows=[("教学策略图", ["关闭", "开启", "开启"]),
              ("跨题 BKT 状态", ["不读取、不更新", "不读取、不更新", "对合格证据读取和更新"])],
        fixed_items=["课程 ds.c_language.v1", "题库版本", "证据检索配置", "模型版本与采样参数",
                     "停止规则", "会话时长"],
        note="A 组为基线苏格拉底策略；B、C 组不保留该 Prompt——Prompt 与策略的差异本身就是基线条件。",
        foot="依据 DeepProf v0.6.2 设计文档 §3 与 M1–M3 实验报告。",
    ),
    "en": dict(
        eyebrow="Controlled design", title="Groups A / B / C: only the declared policy state changes",
        sub="Course, question bank, evidence retrieval, model version, sampling parameters, stop "
            "rules and session length are held fixed. The live-model pilot must not be read as a "
            "fair effectiveness comparison — group A truncated noticeably more often.",
        cols=["A", "B", "C"], roles=["baseline", "policy graph", "policy + learner state"],
        varied="only variable across groups", fixed="held fixed across groups",
        rows=[("Teaching policy graph", ["off", "on", "on"]),
              ("Cross-question BKT state", ["not read, not updated", "not read, not updated",
                                            "read and updated on reliable evidence"])],
        fixed_items=["course ds.c_language.v1", "question bank version", "retrieval config",
                     "model version and sampling", "stop rules", "session length"],
        note="Group A is the baseline Socratic strategy; B and C do not keep that prompt — "
             "the prompt difference is itself part of the baseline condition.",
        foot="Based on the DeepProf v0.6.2 design document §3 and the M1–M3 experiment report.",
    ),
}


def fig_conditions(lang, out):
    t = COND[lang]
    fig, ax, H = new_canvas(10.0, 5.6)
    caption(ax, 1.5, H - 1.9, t["eyebrow"], fs=10, color=BRAND)
    ax.plot([1.5, 6.4], [H - 3.0, H - 3.0], color=BRAND, lw=1.6, solid_capstyle="butt")
    ax.text(1.5, H - 5.5, t["title"], fontsize=15, color=INK, family=SANS,
            fontweight="bold", ha="left", va="center")
    para(ax, 1.5, H - 8.6, t["sub"], width=92, fs=9.8, color=INK_2, lh=1.95)

    LX = 3.0
    CX = [46.0, 63.0, 80.0]
    CW = 16.0
    CH = 4.7

    # ---- 表头
    ax.plot([3.0, 98.5], [41.0, 41.0], color=INK_2, lw=1.3)
    for j, (c, role) in enumerate(zip(t["cols"], t["roles"])):
        ax.text(CX[j] + CW / 2, 39.2, c, ha="center", va="center", fontsize=13,
                color=ACCENT if j == 2 else INK, family=SANS, fontweight="bold")
        for i, ln in enumerate(wrap_to(ax, role, CW, 8.2)[:2]):
            ax.text(CX[j] + CW / 2, 36.9 - i * 1.6, ln, ha="center", va="center",
                    fontsize=8.2, color=MUTED, family=SANS)
    ax.text(LX, 36.9, t["varied"], ha="left", va="center", fontsize=8.8, color=ACCENT,
            family=SANS)
    ax.plot([3.0, 98.5], [34.9, 34.9], color=RULE, lw=1.0)

    # ---- 两个变量行（都是组间唯一变量）
    rows_y = [30.6, 20.4]
    for ri, (name, vals) in enumerate(t["rows"]):
        y = rows_y[ri]
        ax.add_patch(Rectangle((3.0, y - CH / 2 - 0.7), 95.5, CH + 1.4,
                               facecolor="#FFF7F3", edgecolor="none", zorder=0))
        ax.text(LX, y, name, ha="left", va="center", fontsize=11.4, color=INK,
                family=SANS, fontweight="bold")
        for j, v in enumerate(vals):
            hot = (j == 2)
            ax.add_patch(FancyBboxPatch((CX[j], y - CH / 2), CW, CH,
                                        boxstyle="round,pad=0,rounding_size=0.7",
                                        linewidth=1.9 if hot else 1.1,
                                        edgecolor=ACCENT if hot else RULE,
                                        facecolor="#FFF1EC" if hot else PAPER, zorder=2))
            for i, ln in enumerate(wrap_to(ax, v, CW - 1.8, 8.6)[:2]):
                ax.text(CX[j] + CW / 2, y + 1.0 - i * 1.9, ln, ha="center", va="center",
                        fontsize=8.6, color=ACCENT if hot else INK_2, family=SANS, zorder=3)
        ax.plot([3.0, 98.5], [y - CH / 2 - 1.9, y - CH / 2 - 1.9], color=RULE, lw=0.9)

    # ---- 组间固定
    fy0, fy1 = 9.4, 16.4
    ax.add_patch(FancyBboxPatch((3.0, fy0), 95.5, fy1 - fy0,
                                boxstyle="round,pad=0,rounding_size=0.8",
                                linewidth=1.0, edgecolor=RULE, facecolor=PAPER_2, zorder=1))
    ax.text(LX + 1.6, fy1 - 1.5, t["fixed"], ha="left", va="center", fontsize=8.8,
            color=MUTED, family=SANS)
    for i, item in enumerate(t["fixed_items"]):
        ax.text(LX + 3.2 + (i % 3) * 31.0, fy1 - 3.9 - (i // 3) * 2.4, "· " + item,
                ha="left", va="center", fontsize=9.0, color=INK_2, family=SANS, zorder=2)

    para(ax, 1.5, fy0 - 2.2, t["note"], width=96, fs=8.8, color=INK_2, lh=1.9)
    para(ax, 1.5, 2.4, t["foot"], width=96, fs=8.5, color=MUTED, lh=1.85)
    save(fig, out)


# ================================================================ 图 4 证据构成
EVID = {
    "zh": dict(
        eyebrow="实验证据", title="M1–M3：完成的与没完成的，一并列出",
        sub="每一批的分母都是计划格数。全部批次均为构造开发案例与合成 Attempt，无真人参与；"
            "工程验收不构成教学效果证据。",
        rows=[("M1 A/B · 真实 Provider", 76, 4, 0),
              ("M3 离线 · Fake Provider", 400, 0, 0),
              ("M3 真实模型试点", 3, 26, 7)],
        labels=["完成", "失败 / 截断", "未触发"],
        denom="计划格数",
        callouts=[("76 / 80", "4 格 empty_model_response，全部在 B 组，未改写"),
                  ("400 / 400", "120 主矩阵 + 120 回放 + 160 消融"),
                  ("3 / 36", "36 格应用终态完成 26；与生成成功 3 分开计算")],
        note="应用终态完成数（26/36）与完整生成数（3/29）是两个口径，不可混用。",
        foot="依据 DeepProf v0.6.2 设计文档 §6 与《M1–M3 工程实验报告》。",
    ),
    "en": dict(
        eyebrow="Experiment evidence", title="M1–M3: what completed, and what did not",
        sub="Every denominator is the planned cell count. All batches use constructed development "
            "cases and synthetic attempts with no human participants; engineering acceptance is "
            "not evidence of learning gain.",
        rows=[("M1 A/B · live provider", 76, 4, 0),
              ("M3 offline · fake provider", 400, 0, 0),
              ("M3 live-model pilot", 3, 26, 7)],
        labels=["completed", "failed / truncated", "no request"],
        denom="planned cells",
        callouts=[("76 / 80", "4 cells empty_model_response, all in group B, left unrevised"),
                  ("400 / 400", "120 main + 120 replay + 160 ablation"),
                  ("3 / 36", "26 of 36 reached an application final state; reported separately"),
                  ],
        note="Application final state (26 of 36) and complete generation (3 of 29) are two "
             "different measures and must not be merged.",
        foot="Based on the DeepProf v0.6.2 design document §6 and the M1–M3 experiment report.",
    ),
}


def fig_evidence(lang, out):
    t = EVID[lang]
    fig, ax, H = new_canvas(10.0, 5.2)
    caption(ax, 1.5, H - 1.9, t["eyebrow"], fs=10, color=BRAND)
    ax.plot([1.5, 6.4], [H - 3.0, H - 3.0], color=BRAND, lw=1.6, solid_capstyle="butt")
    ax.text(1.5, H - 5.5, t["title"], fontsize=15, color=INK, family=SANS,
            fontweight="bold", ha="left", va="center")
    para(ax, 1.5, H - 8.6, t["sub"], width=92, fs=9.8, color=INK_2, lh=1.95)

    X0, XW, CXW = 27.0, 41.0, 27.0
    CX0 = X0 + XW + 2.0
    BH, GAP = 5.4, 5.0
    y0 = 13.2
    for i, (name, ok, bad, miss) in enumerate(t["rows"]):
        n = ok + bad + miss
        y = y0 + (len(t["rows"]) - 1 - i) * (BH + GAP)
        ax.text(X0 - 1.8, y + BH / 2, name, ha="right", va="center", fontsize=10.6,
                color=INK, family=SANS, fontweight="bold")
        x = X0
        for val, c in ((ok, POS), (bad, NEG), (miss, WARN)):
            if val <= 0:
                continue
            w = val / n * XW
            ax.add_patch(Rectangle((x, y), w, BH, facecolor=c, edgecolor=PAPER,
                                   linewidth=1.6, zorder=2))
            x += w
        ax.add_patch(Rectangle((X0, y), XW, BH, facecolor="none", edgecolor=INK_2,
                               linewidth=1.1, zorder=3))
        ax.text(CX0, y + BH + 0.9, t["denom"] + f"  n = {n}", ha="left", va="bottom",
                fontsize=8.2, color=MUTED, family=SANS)
        ax.text(CX0, y + BH / 2 + 0.55, t["callouts"][i][0], ha="left", va="center",
                fontsize=12.0, color=INK, family=SANS, fontweight="bold")
        for j, ln in enumerate(wrap_to(ax, t["callouts"][i][1], CXW, 8.2)[:2]):
            ax.text(CX0, y + BH / 2 - 1.9 - j * 1.7, ln, ha="left", va="center",
                    fontsize=8.2, color=MUTED, family=SANS)

    lx = X0
    for lab, c in zip(t["labels"], [POS, NEG, WARN]):
        ax.add_patch(Rectangle((lx, y0 - 3.2), 2.4, 1.9, facecolor=c, edgecolor=PAPER, lw=1.2))
        ax.text(lx + 3.4, y0 - 2.25, lab, ha="left", va="center", fontsize=8.8,
                color=INK_2, family=SANS)
        lx += 3.4 + measure(ax, lab, 8.8) + 4.2

    para(ax, 1.5, y0 - 7.0, t["note"], width=96, fs=8.8, color=ACCENT, lh=1.9)
    para(ax, 1.5, 2.2, t["foot"], width=96, fs=8.5, color=MUTED, lh=1.85)
    save(fig, out)


# ================================================================ 图 5 BKT 诚实呈现
BKT = {
    "zh": dict(
        eyebrow="保留不利结果", title="BKT 预测低于随机：这个结果留在页面上",
        sub="开发参数（P(L0)=0.20、P(T)=0.10、P(G)=0.20、P(S)=0.10）未用学生数据拟合或校准。"
            "构造历史上的预测表现因此不可用于有效预测，我们照原样公开。",
        auc_label="BKT 构造预测 AUC", chance="随机基线 0.5",
        metrics=[("Brier", "0.366"), ("Log loss", "0.936"), ("ECE", "0.530")],
        good=[("策略动作族匹配", "196 / 280", "70%"), ("回放决策一致", "120 / 120", "100%")],
        good_hdr="仍然成立的两项",
        note="低于 0.5 的 AUC 不是噪声，而是参数未校准的直接读数。校准需要教师审核后的作答数据。",
        foot="依据 DeepProf v0.6.2 设计文档 §6（M3 离线批次 m3-offline-20260924-final6）。",
    ),
    "en": dict(
        eyebrow="Adverse result retained", title="BKT predicts worse than chance — and stays on the page",
        sub="Development parameters (P(L0)=0.20, P(T)=0.10, P(G)=0.20, P(S)=0.10) were never fitted "
            "or calibrated on student data, so prediction on constructed history is not valid for "
            "forecasting. We publish it as measured.",
        auc_label="BKT AUC on constructed history", chance="chance line 0.5",
        metrics=[("Brier", "0.366"), ("Log loss", "0.936"), ("ECE", "0.530")],
        good=[("Policy action-family match", "196 / 280", "70%"),
              ("Replay decision agreement", "120 / 120", "100%")],
        good_hdr="Still holding",
        note="An AUC below 0.5 is not noise; it is a direct reading of uncalibrated parameters. "
             "Calibration needs teacher-reviewed response data.",
        foot="Based on the DeepProf v0.6.2 design document §6 "
             "(M3 offline batch m3-offline-20260924-final6).",
    ),
}


def fig_bkt(lang, out):
    t = BKT[lang]
    fig, ax, H = new_canvas(10.0, 4.25)
    caption(ax, 1.5, H - 1.9, t["eyebrow"], fs=10, color=ACCENT)
    ax.plot([1.5, 6.4], [H - 3.0, H - 3.0], color=ACCENT, lw=1.6, solid_capstyle="butt")
    ax.text(1.5, H - 5.5, t["title"], fontsize=14.6, color=INK, family=SANS,
            fontweight="bold", ha="left", va="center")
    para(ax, 1.5, H - 8.6, t["sub"], width=92, fs=9.7, color=INK_2, lh=1.95)

    TOP, AY = 30.1, 19.8

    # ---- 第一栏：AUC 点图（点图 + 随机参考线，不用截断柱）
    c1a, c1b = 3.0, 29.0
    ax.text(c1a, TOP, t["auc_label"], ha="left", va="center", fontsize=9.6,
            color=INK, family=SANS, fontweight="bold")
    lo, hi = 0.20, 0.60
    px = lambda v: c1a + (v - lo) / (hi - lo) * (c1b - c1a)
    ax.plot([c1a, c1b], [AY, AY], color=INK_2, lw=1.1)
    for v in (0.20, 0.30, 0.40, 0.50, 0.60):
        ax.plot([px(v), px(v)], [AY, AY - 1.0], color=INK_2, lw=1.0)
        ax.text(px(v), AY - 2.3, f"{v:.2f}", ha="center", va="top", fontsize=8.4,
                color=MUTED, family=SANS)
    ax.plot([px(0.5), px(0.5)], [AY - 0.3, AY + 7.2], color=NEG, lw=1.5,
            linestyle=(0, (5, 3.5)))
    ax.text(px(0.5) + 0.9, AY + 7.9, t["chance"], ha="left", va="center", fontsize=8.4,
            color=NEG, family=SANS)
    ax.plot([px(0.2964)], [AY + 3.4], marker="o", markersize=11, color=ACCENT,
            markeredgecolor=PAPER, markeredgewidth=1.8, zorder=5)
    ax.text(px(0.2964) - 1.4, AY + 6.0, "0.296", ha="right", va="center", fontsize=12.5,
            color=ACCENT, family=SANS, fontweight="bold")

    # ---- 第二栏：Brier / Log loss / ECE
    c2 = 36.0
    ax.text(c2, TOP, "Brier / Log loss / ECE", ha="left", va="center", fontsize=9.6,
            color=INK, family=SANS, fontweight="bold")
    for i, (k, v) in enumerate(t["metrics"]):
        y = TOP - 4.6 - i * 4.2
        ax.text(c2, y, k, ha="left", va="center", fontsize=8.6, color=MUTED, family=SANS)
        ax.text(c2 + 11.5, y, v, ha="left", va="center", fontsize=12.5, color=INK_2,
                family=SANS, fontweight="bold")

    # ---- 第三栏：仍然成立的两项
    c3 = 62.0
    ax.text(c3, TOP, t["good_hdr"], ha="left", va="center", fontsize=9.6,
            color=INK, family=SANS, fontweight="bold")
    for i, (k, v, pct) in enumerate(t["good"]):
        y = TOP - 4.6 - i * 4.2
        ax.text(c3, y + 1.15, k, ha="left", va="center", fontsize=8.4, color=MUTED,
                family=SANS)
        ax.text(c3, y - 1.0, v, ha="left", va="center", fontsize=11.8, color=POS,
                family=SANS, fontweight="bold")
        ax.text(c3 + measure(ax, v, 11.8, "bold") + 1.8, y - 1.0, pct, ha="left",
                va="center", fontsize=8.6, color=MUTED, family=SANS)

    para(ax, 1.5, 11.2, t["note"], width=96, fs=8.8, color=INK_2, lh=1.9)
    para(ax, 1.5, 3.4, t["foot"], width=96, fs=8.5, color=MUTED, lh=1.85)
    save(fig, out)


JOBS = dict(loop=fig_loop, conditions=fig_conditions, evidence=fig_evidence, bkt=fig_bkt)

if __name__ == "__main__":
    outdir = Path(sys.argv[1])
    only = sys.argv[2:] or list(JOBS)
    for name in only:
        for lang in ("zh", "en"):
            JOBS[name](lang, outdir / f"fig-{name}-{lang}")
            print("ok", name, lang)
