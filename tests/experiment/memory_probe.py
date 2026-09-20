"""跨轮记忆探针实验（吸收自孙一新实验的记忆锚点设计，改造为走教学图真实链路）。

原版思路：多轮会话中第 1 轮植入「记忆锚点」（例题代号），后续正常教学，
探针轮要求模型复述锚点，判定跨轮上下文保持能力。

本版改造：每一轮都走 `run_teaching_turn` 教学图（真实 RuntimeService +
SQLite 记忆存储），锚点的跨轮传递依赖架构自身的学情记忆链路
（update_profile 写记忆 / assess 召回记忆），而不是把对话历史硬塞进 prompt——
这样实验同时验收「记忆读写闭环是否真的接进了生成上下文」。

场景脚本（每轮独立路由，路由本身不是本实验重点）：
    T0 植入：学生提到自己用「牛顿迭代法」解过方程（锚点 = 牛顿迭代法）
    T1 教学：继续正常学习导数
    T2 探针：要求复述锚点，判据 = 回复含锚点

探针轮刻意带上教材可覆盖的知识点（"讲讲导数的定义"）：
两次实验暴露了两个结构性冲突，都不是记忆链路的问题——
① 问"它叫什么"路由到 Ask → Socratic 的 avoid_answer 约束禁止模型说出
   "答案"，而锚点名恰好就是学生问的答案（回复已复现牛顿法特征：
   逼近根、切线、修正猜测，只因约束拒说名字）；
② 改用求讲解措辞路由到 Teach 后，探针问句里没有任何教材关键词，RAG
   无命中 → Teach 的 require_evidence 门直接给出确定性"证据不足"文本、
   **不调用模型**（latency ~6ms 即为证据）。
因此探针轮把"讲讲导数的定义"与复述请求合并：既有教材证据让门打开，
又保留跨轮记忆的验收目标（reply 是否复现 T0 植入的锚点）。

CSV 另带 recall_records 诊断列（Assess 决策事件里的 memory_refs 条数），
用于确证"探针轮确实召回了 T0 写入的记忆"，区分"没召回"与"召回了没说"。

运行方式：
    py tests/experiment/memory_probe.py [--reps 3]

输出：tests/experiment/output/memory_probe.csv（逐轮记录 + 探针判定）。
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from graph.education.builder import run_teaching_turn  # noqa: E402
from runtime.core.message import new_id  # noqa: E402

from tests.experiment import common  # noqa: E402
from tests.experiment.education_scenario import build_experiment_runtime  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "output"

#: 记忆锚点：T0 由学生自述植入，T2 探针要求复述
MEMORY_ANCHOR = "牛顿迭代法"

BASE = {
    "session_id": "probe_sess",
    "learner_id": "probe_learner",
    "current_concept": "导数",
    "learning_goal": "理解导数的定义与几何意义",
}

TURN_SCRIPT: list[dict] = [
    {
        "turn": "T0_plant",
        "desc": "植入锚点：学生自述曾用牛顿迭代法解题",
        "state": {
            **BASE,
            "user_input": f"老师你好，我之前自己看书的时候用{MEMORY_ANCHOR}解过一道方程题，"
            "现在想系统学微积分，先从导数开始可以吗？",
        },
    },
    {
        "turn": "T1_learn",
        "desc": "正常教学：继续学导数",
        "state": {
            **BASE,
            "user_input": "好的，那请给我讲讲导数的定义吧",
        },
    },
    {
        "turn": "T2_probe",
        "desc": "记忆探针：讲导数（开证据门）+ 要求复述锚点",
        "state": {
            **BASE,
            "user_input": "聊回开头——请给我讲讲导数的定义，另外我一开始提到的"
            "那个数值方法，它叫什么名字？",
        },
    },
]


async def run_probe_once(reps_index: int) -> list[dict]:
    """跑一轮完整三步脚本，返回逐轮记录（含记忆链路事件诊断）。"""
    service = build_experiment_runtime()
    rows: list[dict] = []

    for step in TURN_SCRIPT:
        state = dict(step["state"])
        state["trace_id"] = new_id("trace")
        t0 = time.time()
        result = await run_teaching_turn(service, state)
        ms = (time.time() - t0) * 1000
        text = str(result.get("response_text") or "")
        # 诊断：本轮教学图触发的事件类型（看记忆读写是否真的发生）
        events = service.events_by_trace(state["trace_id"])
        event_types = sorted({e.type for e in events})
        # 诊断：Assess 决策事件里的 memory_refs 条数（确证召回真实发生）
        recall_records = ""
        for event in events:
            if event.type == "pedagogy.decision" and event.payload.get("node") == "assess":
                recall_records = str(len(event.payload.get("memory_refs") or []))
                break
        rows.append(
            {
                "rep": reps_index,
                "turn": step["turn"],
                "desc": step["desc"],
                "action": str(result.get("action") or ""),
                "latency_ms": round(ms, 1),
                "reply_len": len(text),
                "reply_head": text[:80].replace("\n", " "),
                "anchor_hit": "1" if MEMORY_ANCHOR in text else "0",
                "recall_records": recall_records,
                "event_types": ",".join(event_types)[:200],
                "occurred_at": common.now_ts(),
            }
        )
        print(f"  rep{reps_index} {step['turn']}: action={rows[-1]['action']} "
              f"latency={rows[-1]['latency_ms']}ms anchor_hit={rows[-1]['anchor_hit']} "
              f"recall={recall_records or '?'}")
    return rows


async def main() -> int:
    parser = argparse.ArgumentParser(description="跨轮记忆探针实验")
    parser.add_argument("--reps", type=int, default=3, help="重复次数")
    args = parser.parse_args()

    info = common.provider_info()
    print(f"provider={info['provider']} model={info['model']} key={info['api_key_masked']}")
    print(f"锚点={MEMORY_ANCHOR!r}；脚本 {len(TURN_SCRIPT)} 轮 × {args.reps} 次（真实链路）")

    all_rows: list[dict] = []
    for rep in range(1, args.reps + 1):
        print(f"--- rep {rep}/{args.reps} ---")
        all_rows.extend(await run_probe_once(rep))

    # 汇总
    probes = [r for r in all_rows if r["turn"] == "T2_probe"]
    hits = sum(1 for r in probes if r["anchor_hit"] == "1")
    print("-" * 60)
    print(f"探针命中：{hits}/{len(probes)}（回复包含锚点 {MEMORY_ANCHOR!r}）")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "memory_probe.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(all_rows[0]))
        writer.writeheader()
        writer.writerows(all_rows)
    print(f"CSV 已写出：{out}")

    return 0 if hits == len(probes) and probes else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
