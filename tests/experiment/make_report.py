"""实验报告图表生成器（吸收自孙一新实验的 make_report.py，适配 realrun CSV 结构）。

读取教育场景实验的产物并出图 + 延迟统计：
    输入：tests/experiment/output/realrun_results.csv   逐轮原始记录
          tests/experiment/output/realrun_summary.csv   分场景汇总
    产出：tests/experiment/output/charts/latency_box.png    各场景耗时箱线图
          tests/experiment/output/charts/check_pass_bar.png 行为判据通过率柱状图
          追加「图表与延迟统计」章节到 realrun_report.md（幂等：重复运行替换该章节）

运行方式：
    py tests/experiment/make_report.py
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import matplotlib  # noqa: E402

matplotlib.use("Agg")  # 无显示环境也能出图
import matplotlib.pyplot as plt  # noqa: E402

# 中文字体回退：Windows 优先微软雅黑，缺失则退回 DejaVu（中文可能显示为方框但不报错）
plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "SimSun", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False

OUT_DIR = Path(__file__).resolve().parent / "output"
CHART_DIR = OUT_DIR / "charts"
RAW_CSV = OUT_DIR / "realrun_results.csv"
SUMMARY_CSV = OUT_DIR / "realrun_summary.csv"
REPORT_MD = OUT_DIR / "realrun_report.md"

#: 报告中图表章节的标记（幂等替换用）
SECTION_MARK = "## 四、图表与延迟统计"


def read_csv(path: Path) -> list[dict]:
    with path.open("r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def quantile(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if not ordered:
        return 0.0
    index = min(int(len(ordered) * q), len(ordered) - 1)
    return ordered[index]


# ---------------------------------------------------------------- 图表 ----
def chart_latency(rows: list[dict]) -> Path:
    """各场景耗时箱线图（含失败轮——挂起/重试本身就是重要信号）。"""
    by_scenario: dict[str, list[float]] = {}
    for r in rows:
        by_scenario.setdefault(r["scenario"], []).append(float(r["latency_ms"]))

    names = sorted(by_scenario)
    series = [by_scenario[n] for n in names]

    fig, ax = plt.subplots(figsize=(10, 5))
    ax.boxplot(series, tick_labels=names, showmeans=True)
    ax.set_title("教育场景真实链路：各场景响应耗时分布")
    ax.set_ylabel("耗时 (ms)")
    ax.set_yscale("log")  # 确定性场景 <10ms 与 LLM 场景 >10s 相差三个量级
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    out = CHART_DIR / "latency_box.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


def chart_check_pass(summary_rows: list[dict]) -> Path:
    """行为判据通过率柱状图（路由准确率恒 100% 时它才是有区分度的指标）。"""
    names = [r["scenario"] for r in summary_rows]
    values = [float(r.get("checks_pass_pct") or 0.0) for r in summary_rows]

    fig, ax = plt.subplots(figsize=(10, 5))
    bars = ax.bar(names, values, color="#2f6fed", width=0.6)
    ax.set_ylim(0, 110)
    ax.set_ylabel("行为判据通过率 (%)")
    ax.set_title("教育场景真实链路：行为判据通过率")
    for bar, v in zip(bars, values):
        ax.text(bar.get_x() + bar.get_width() / 2, v + 2, f"{v:.0f}%", ha="center", fontsize=9)
    ax.grid(axis="y", linestyle="--", alpha=0.4)
    plt.xticks(rotation=30, ha="right")
    fig.tight_layout()
    out = CHART_DIR / "check_pass_bar.png"
    fig.savefig(out, dpi=150)
    plt.close(fig)
    return out


# ---------------------------------------------------------------- 统计 ----
def latency_stats_table(rows: list[dict]) -> str:
    """逐场景 min/avg/p95/max 延迟表（含全部轮次，不剔除失败轮）。"""
    by_scenario: dict[str, list[float]] = {}
    for r in rows:
        by_scenario.setdefault(r["scenario"], []).append(float(r["latency_ms"]))

    lines = [
        "| 场景 | 轮次 | min(ms) | avg(ms) | p95(ms) | max(ms) |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for name in sorted(by_scenario):
        values = by_scenario[name]
        avg = sum(values) / len(values)
        lines.append(
            f"| {name} | {len(values)} | {min(values):.0f} | {avg:.0f} | "
            f"{quantile(values, 0.95):.0f} | {max(values):.0f} |"
        )
    return "\n".join(lines)


def build_section(rows: list[dict], latency_chart: Path, pass_chart: Path) -> str:
    return f"""{SECTION_MARK}

> 本章节由 `tests/experiment/make_report.py` 自动生成（可重复运行，幂等替换）。

### 4.1 各场景响应耗时分布

![各场景耗时分布](./charts/{latency_chart.name})

注意 y 轴为对数刻度：S3/S7/S8 为确定性路径（模板/证据不足/停止），耗时 <10ms；
LLM 场景正常 3–20s，S1 的离群点是曾观察到的 603s 流式挂起（已由 LLM_TIMEOUT_SECONDS 修复兜底）。

### 4.2 行为判据通过率

![行为判据通过率](./charts/{pass_chart.name})

### 4.3 逐场景延迟统计

{latency_stats_table(rows)}
"""


def main() -> int:
    if not (RAW_CSV.exists() and SUMMARY_CSV.exists()):
        print("[失败] 缺少 realrun_results.csv / realrun_summary.csv，请先运行 education_scenario.py")
        return 1

    rows = read_csv(RAW_CSV)
    summary_rows = read_csv(SUMMARY_CSV)
    CHART_DIR.mkdir(parents=True, exist_ok=True)

    latency_chart = chart_latency(rows)
    pass_chart = chart_check_pass(summary_rows)
    section = build_section(rows, latency_chart, pass_chart)

    report = REPORT_MD.read_text(encoding="utf-8")
    # 幂等：已有章节则整体替换，否则追加
    if SECTION_MARK in report:
        report = re.split(rf"^{re.escape(SECTION_MARK)}", report, maxsplit=1, flags=re.M)[0].rstrip() + "\n"
    else:
        report = report.rstrip() + "\n\n"
    REPORT_MD.write_text(report + "\n" + section, encoding="utf-8")

    print(f"图表已写出：{latency_chart} / {pass_chart}")
    print(f"报告章节已更新：{REPORT_MD}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
