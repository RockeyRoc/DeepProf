"""教学策略图真实链路实验（教育组第一轮验收补充，DESIGNv0.4 §16.3）。

与单测的区别：单测用 FakeRuntime（provider=fake），本实验走**真实链路**——
真实 RuntimeService（DeepSeek provider + 真实 Skill 注册表 + ACTION_BINDINGS
+ SQLite 事件落盘）驱动教学策略图 `run_teaching_turn`，验证六个教学动作在
真实模型下的端到端行为。

实验装置声明（诚实原则 §12）：
- 教材检索为**实验装置**（本地微型语料 + 关键词匹配，见 corpus.py），
  语料与向量索引正式版由 RAG 组交接；概念不在语料中时装置诚实返回
  insufficient_evidence，用于"无证据不编造引用"场景；
- 其余全部为生产代码路径：绑定表、节点、路由、Skill、事件落盘都不打桩。

场景与预期路由（判据来自 policies 常量与 router.decide_action 优先级）：

    S1 prior_gap_teach     先验不足自述            → teach（起讲点前移）
    S2 explain_teach       主动求讲解（语料内）    → teach（带可定位引用）
    S3 blocked_hint        连续答错 1 次            → hint（确定性模板，0 次 LLM）
    S4 stable_error_correct 连错 3 次且提示用尽    → correct（带引用对比例）
    S5 reasoning_ask       正常推理陈述            → ask（苏格拉底追问，只提问）
    S6 quiz_test           累计尝试≥3 且无未处理错 → test（出题；Attempt 显式跳过）
    S7 no_evidence_teach   求讲解语料外概念        → teach（声明证据不足、零引用）
    S8 stopped_end         学生主动停止            → END（0 次 LLM，不产出动作）

运行方式：
    py tests/experiment/education_scenario.py [--reps 3]

输出（tests/experiment/output/）：
    realrun_results.csv   逐轮原始记录
    realrun_summary.csv   分场景汇总
    realrun_report.md     实验报告（含逐场景判定与样例回复）
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import re
import sys
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.core.message import new_id  # noqa: E402
from runtime.service import build_runtime_service  # noqa: E402
from runtime.storage.sqlite_store import SqliteDatabase  # noqa: E402
from graph.education.builder import run_teaching_turn  # noqa: E402
from graph.education.policies import HINT_LEVEL_TEMPLATES  # noqa: E402
from api.deps import configure_runtime  # noqa: E402

from tests.experiment import common  # noqa: E402
from tests.experiment.corpus import (  # noqa: E402
    CORPUS,
    TOOL_NAME,
    build_experiment_search_tool,
)

OUT_DIR = Path(__file__).resolve().parent / "output"

EVENT_DECISION = "pedagogy.decision"
EVENT_NODE_ENTERED = "pedagogy.node.entered"
EVENT_MODEL_COMPLETED = "model.completed"
EVENT_MODEL_FAILED = "model.failed"

#: 引用伪造探测：正文出现"第 N 页 / doc:xxx / chunk"样式但 citations 为空
_FABRICATION_RE = re.compile(r"第\s*\d+\s*页|document_id|chunk_id|(?:doc|chunk)[:：]")


# ======================================================================
# 实验装置：真实 Runtime（仅教材检索为实验装置）
# ======================================================================
def build_experiment_runtime():
    """真实 RuntimeService：DeepSeek provider + 真实 Skill/绑定 + 内存 SQLite。

    只覆盖 search_textbook 为实验装置版（生产版是诚实占位：语料未接入）。
    """
    service = build_runtime_service(db=SqliteDatabase(":memory:"))
    configure_runtime(service)
    service.tools.unregister(TOOL_NAME)
    service.tools.register(build_experiment_search_tool())
    return service


# ======================================================================
# 场景定义：状态初值 + 预期动作 + 行为检查
# ======================================================================
BASE = {
    "session_id": "exp_sess",
    "learner_id": "exp_learner",
    "current_concept": "导数",
    "learning_goal": "理解导数的定义与几何意义",
}

SCENARIOS: list[dict] = [
    {
        "name": "S1_prior_gap_teach",
        "desc": "先验不足自述 → teach（起讲点前移到前置概念）",
        "expected": "teach",
        "state": {
            **BASE,
            "user_input": "我没学过导数，能直接学梯度下降吗？",
            "current_concept": "梯度下降",
        },
        "checks": {"prior_note_used": None, "no_fabrication": None, "reply_nonempty": None},
    },
    {
        "name": "S2_explain_teach",
        "desc": "主动求讲解（语料内概念）→ teach（带可定位引用）",
        "expected": "teach",
        "state": {
            **BASE,
            "user_input": "请给我讲解一下导数的定义",
        },
        "checks": {
            "citations_trackable": None,  # 引用可定位且来自语料
            "reply_nonempty": None,
        },
    },
    {
        "name": "S3_blocked_hint",
        "desc": "尝试受阻（连错1次）→ hint（确定性模板，0 次 LLM）",
        "expected": "hint",
        "state": {
            **BASE,
            "user_input": "我觉得导数就是函数值的变化量",
            "attempt_count": 1,
            "wrong_streak": 1,
            "hint_level": 0,
        },
        "checks": {"hint_template_used": None, "zero_llm": None},
    },
    {
        "name": "S4_stable_error_correct",
        "desc": "连错3次且提示用尽 → correct（带引用对比例）",
        "expected": "correct",
        "state": {
            **BASE,
            "user_input": "导数就是这一点函数值和下一点的差",
            "attempt_count": 4,
            "wrong_streak": 3,
            "hint_level": 3,
            "misconceptions": ["把增量当成导数"],
        },
        "checks": {"citations_trackable": None, "reply_nonempty": None},
    },
    {
        "name": "S5_reasoning_ask",
        "desc": "正常推理陈述 → ask（苏格拉底追问，只提问不泄答案）",
        "expected": "ask",
        "state": {
            **BASE,
            "user_input": "我认为切线斜率可以用两点连线算出来，对吗？",
        },
        "checks": {"question_only": None, "reply_nonempty": None},
    },
    {
        "name": "S6_quiz_test",
        "desc": "累计尝试≥3 → test（出题；Attempt 因题库未接入显式跳过）",
        "expected": "test",
        "state": {
            **BASE,
            "user_input": "",
            "attempt_count": 3,
        },
        "checks": {"attempt_skip_reason": None, "reply_nonempty": None},
    },
    {
        "name": "S7_no_evidence_teach",
        "desc": "求讲解语料外概念 → teach（声明证据不足、零引用）",
        "expected": "teach",
        "state": {
            **BASE,
            "user_input": "请给我讲解一下傅里叶变换",
            "current_concept": "傅里叶变换",
            "learning_goal": "理解傅里叶变换的基本思想",
        },
        "checks": {"insufficient_declared": None, "no_citations": None, "no_fabrication": None},
    },
    {
        "name": "S8_stopped_end",
        "desc": "学生主动停止 → END（0 次 LLM，不产出教学动作）",
        "expected": "END",
        "state": {
            **BASE,
            "user_input": "我先去上课了，下次继续",
            "student_stopped": True,
        },
        "checks": {"zero_llm": None, "no_teaching_node": None},
    },
]

# 每个场景的重复次数（LLM 场景 3 次看稳定性；确定性场景 1 次足够）
DEFAULT_REPS = 3
DETERMINISTIC_REPS = {"S3_blocked_hint": 1, "S8_stopped_end": 1}


# ======================================================================
# 事件提取
# ======================================================================
def _payloads(events: list, event_type: str) -> list[dict]:
    return [dict(e.payload) for e in events if e.type == event_type]


def collect_run_facts(service, trace_id: str, result: dict) -> dict:
    """从事件流与最终状态提取本轮事实。"""
    events = service.events_by_trace(trace_id)
    decisions = _payloads(events, EVENT_DECISION)
    entered_nodes = [p.get("node") for p in _payloads(events, EVENT_NODE_ENTERED)]
    completed = _payloads(events, EVENT_MODEL_COMPLETED)
    failed = _payloads(events, EVENT_MODEL_FAILED)

    usage_total = 0
    for item in completed:
        usage = item.get("usage") or {}
        usage_total += int(usage.get("total_tokens") or 0)

    teaching_nodes = [n for n in entered_nodes if n in ("teach", "ask", "hint", "correct", "test")]
    attempts = [e for e in events if e.type == "pedagogy.attempt"]

    return {
        "action": str(result.get("action") or ""),
        "nodes": entered_nodes,
        "teaching_nodes": teaching_nodes,
        "decisions": decisions,
        "response_text": str(result.get("response_text") or ""),
        "citations": list(result.get("citations") or []),
        "llm_calls": len(completed),
        "llm_failures": len(failed),
        "usage_total": usage_total,
        "attempts": attempts,
        "event_count": len(events),
    }


# ======================================================================
# 行为检查（每场景一组判据；返回 {check_name: (ok, detail)}）
# ======================================================================
def run_checks(scenario: dict, facts: dict) -> dict[str, tuple[bool, str]]:
    checks: dict[str, tuple[bool, str]] = {}
    text = facts["response_text"]
    wanted = set(scenario["checks"])

    if "reply_nonempty" in wanted:
        checks["reply_nonempty"] = (len(text.strip()) > 10, f"len={len(text.strip())}")

    if "zero_llm" in wanted:
        checks["zero_llm"] = (facts["llm_calls"] == 0, f"llm_calls={facts['llm_calls']}")

    if "prior_note_used" in wanted:
        decisions = facts["decisions"]
        reason_hit = any("先验" in str(d.get("reason") or "") for d in decisions)
        teach_dec = next((d for d in decisions if d.get("node") == "teach"), {})
        prior_flag = bool(teach_dec.get("prior_knowledge_gap"))
        checks["prior_note_used"] = (
            reason_hit and prior_flag,
            f"reason_hit={reason_hit} prior_flag={prior_flag}",
        )
        # 起讲点前移的行为面：回复应出现前置/定义等基础讲解迹象
        moved = any(kw in text for kw in ("前置", "定义", "基础", "极限"))
        checks["prior_reply_moved"] = (moved, f"moved={moved}")

    if "citations_trackable" in wanted:
        corpus_ids = {
            f"{c['document_id']}#{c['chunk_id']}@{c['page']}" for c in CORPUS
        }
        cites = facts["citations"]
        ok = bool(cites) and all(
            (c if isinstance(c, str) else str(c.get("evidence_id") or "")) in corpus_ids
            for c in cites
        )
        checks["citations_trackable"] = (ok, f"n={len(cites)} trackable={ok}")

    if "hint_template_used" in wanted:
        # hint 是确定性模板：回复应包含对应级别模板文本（填入 concept 后比对），且无 LLM 调用
        level_text = ""
        for d in facts["decisions"]:
            if d.get("node") == "hint":
                level = int(d.get("hint_level_to") or 0)
                level_text = str(HINT_LEVEL_TEMPLATES.get(level, ""))
        rendered = level_text.replace("{concept}", "导数")
        checks["hint_template_used"] = (
            bool(rendered) and rendered[:12] in text,
            f"template_hit={bool(rendered) and rendered[:12] in text}",
        )

    if "question_only" in wanted:
        # 苏格拉底约束的行为面：以问句收尾或至少包含追问，且不直接给结论
        has_question = "？" in text or "?" in text
        not_conclusion = "所以答案是" not in text and "正确答案" not in text
        checks["question_only"] = (has_question and not_conclusion, f"q={has_question}")

    if "attempt_skip_reason" in wanted:
        # 出题模式（generate）没有作答，闸门按"缺少可靠判分"跳过；
        # 评价模式（evaluate）缺 item_id 时按"题目不可追踪"跳过——两者都必须显式给原因
        dec = next((d for d in facts["decisions"] if d.get("node") == "test"), {})
        reason = str(dec.get("attempt_skip_reason") or "")
        recorded = bool(dec.get("attempt_recorded"))
        checks["attempt_skip_reason"] = (
            (not recorded) and bool(reason) and ("判分" in reason or "item_id" in reason),
            f"recorded={recorded} reason={reason[:40]}",
        )

    if "insufficient_declared" in wanted:
        checks["insufficient_declared"] = ("证据不足" in text, f"declared={'证据不足' in text}")

    if "no_citations" in wanted:
        checks["no_citations"] = (len(facts["citations"]) == 0, f"n={len(facts['citations'])}")

    if "no_fabrication" in wanted:
        fabricated = bool(_FABRICATION_RE.search(text)) and len(facts["citations"]) == 0
        checks["no_fabrication"] = (not fabricated, f"fabricated={fabricated}")

    if "no_teaching_node" in wanted:
        checks["no_teaching_node"] = (
            len(facts["teaching_nodes"]) == 0,
            f"teaching_nodes={facts['teaching_nodes']}",
        )

    return checks


# ======================================================================
# 主流程
# ======================================================================
async def run_once(service, scenario: dict) -> dict:
    state = dict(scenario["state"])
    state["trace_id"] = new_id("trace")
    t0 = time.time()
    result = await run_teaching_turn(service, state)
    latency_ms = (time.time() - t0) * 1000
    facts = collect_run_facts(service, state["trace_id"], result)
    checks = run_checks(scenario, facts)
    action = facts["action"]
    expected = scenario["expected"]
    route_ok = (action == expected) or (expected == "END" and action in ("", "end"))
    chain_ok = len(facts["nodes"]) >= 2  # assess + update_profile 至少各一次
    return {
        "scenario": scenario["name"],
        "desc": scenario["desc"],
        "expected_action": expected,
        "actual_action": action or "END",
        "route_ok": route_ok,
        "event_chain_ok": chain_ok,
        "nodes": "→".join(facts["nodes"]),
        "llm_calls": facts["llm_calls"],
        "llm_failures": facts["llm_failures"],
        "usage_total": facts["usage_total"],
        "latency_ms": round(latency_ms, 1),
        "response_text": facts["response_text"],
        "citations": ";".join(
            c if isinstance(c, str) else str(c.get("evidence_id") or "")
            for c in facts["citations"]
        ),
        **{f"check_{k}": "PASS" if ok else f"FAIL({detail})" for k, (ok, detail) in checks.items()},
        "all_checks_ok": all(ok for ok, _ in checks.values()) if checks else True,
        "occurred_at": common.now_ts(),
    }


async def main(reps: int) -> int:
    if not common.has_real_key():
        print(f"[失败] 未配置有效密钥（key={common.mask(__import__('config').settings.deepseek_api_key)}）")
        return 1
    info = common.provider_info()
    print(f"provider={info['provider']} model={info['model']} key={info['api_key_masked']}")
    print(f"装置：真实 RuntimeService + 教学图；教材检索为实验装置（{len(CORPUS)} 条语料）")

    service = build_experiment_runtime()
    rows: list[dict] = []
    for scenario in SCENARIOS:
        n = DETERMINISTIC_REPS.get(scenario["name"], reps)
        for i in range(n):
            try:
                row = await run_once(service, scenario)
            except Exception as exc:  # noqa: BLE001 - 单轮失败不阻断实验
                row = {
                    "scenario": scenario["name"],
                    "expected_action": scenario["expected"],
                    "actual_action": "EXCEPTION",
                    "route_ok": False,
                    "all_checks_ok": False,
                    "response_text": "",
                    "latency_ms": 0,
                    "occurred_at": common.now_ts(),
                    "_error": f"{type(exc).__name__}: {exc}",
                }
            rows.append(row)
            status = "OK" if row.get("route_ok") and row.get("all_checks_ok") else "FAIL"
            print(f"  {scenario['name']} #{i + 1}: route={row.get('actual_action')} [{status}] "
                  f"latency={row.get('latency_ms')}ms")

    write_outputs(rows)
    ok = all(r.get("route_ok") for r in rows)
    print(f"{'[通过]' if ok else '[存在失败]'} 共 {len(rows)} 轮；结果见 {OUT_DIR}")
    return 0 if ok else 2


def write_outputs(rows: list[dict]) -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    fields = [
        "scenario", "expected_action", "actual_action", "route_ok", "event_chain_ok",
        "nodes", "llm_calls", "llm_failures", "usage_total", "latency_ms",
        "response_text", "citations", "all_checks_ok", "occurred_at",
    ]
    check_fields = sorted({k for r in rows for k in r if k.startswith("check_")})
    with (OUT_DIR / "realrun_results.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=fields + check_fields, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)

    # 分场景汇总
    summary: dict[str, dict] = {}
    for r in rows:
        s = summary.setdefault(r["scenario"], {
            "scenario": r["scenario"], "runs": 0, "route_pass": 0, "checks_pass": 0,
            "latency_sum": 0.0, "usage_sum": 0, "llm_fail": 0,
        })
        s["runs"] += 1
        s["route_pass"] += 1 if r.get("route_ok") else 0
        s["checks_pass"] += 1 if r.get("all_checks_ok") else 0
        s["latency_sum"] += float(r.get("latency_ms") or 0)
        s["usage_sum"] += int(r.get("usage_total") or 0)
        s["llm_fail"] += int(r.get("llm_failures") or 0)
    summary_rows = []
    for s in summary.values():
        runs = s["runs"]
        summary_rows.append({
            "scenario": s["scenario"],
            "runs": runs,
            "route_accuracy_pct": round(s["route_pass"] / runs * 100, 1),
            "checks_pass_pct": round(s["checks_pass"] / runs * 100, 1),
            "avg_latency_ms": round(s["latency_sum"] / runs, 1),
            "avg_tokens": round(s["usage_sum"] / runs, 1),
            "llm_failures": s["llm_fail"],
        })
    with (OUT_DIR / "realrun_summary.csv").open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(summary_rows[0].keys()))
        writer.writeheader()
        writer.writerows(summary_rows)

    write_report(rows, summary_rows)


def write_report(rows: list[dict], summary_rows: list[dict]) -> None:
    info = common.provider_info()
    lines = [
        "# 教学策略图真实链路实验报告（教育组·孙一新模块）",
        "",
        f"- 实验时间：{common.now_ts()}",
        f"- 配置：provider={info['provider']} · model={info['model']} · key={info['api_key_masked']}",
        "- 链路：真实 RuntimeService（真实 Provider / Skill / ACTION_BINDINGS / SQLite 事件落盘）"
        " → `run_teaching_turn` 教学策略图",
        "- 实验装置：`search_textbook` 为实验版（本地微型语料 + 关键词检索，见 corpus.py）；"
        "生产占位工具因课程语料未接入永远返回证据不足（§16.2，RAG 组交接前）",
        "",
        "## 一、分场景汇总",
        "",
        "| 场景 | 轮次 | 路由准确率 | 行为判据通过率 | 平均耗时(ms) | 平均 tokens | LLM失败 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for s in summary_rows:
        lines.append(
            f"| {s['scenario']} | {s['runs']} | {s['route_accuracy_pct']}% | "
            f"{s['checks_pass_pct']}% | {s['avg_latency_ms']} | {s['avg_tokens']} | {s['llm_failures']} |"
        )
    total_route = sum(1 for r in rows if r.get("route_ok"))
    lines += [
        "",
        f"整体路由准确率：**{total_route}/{len(rows)}**（策略侧为确定性规则，预期 100%）。"
        "行为判据（引用可定位、无编造、苏格拉底只提问、先验起讲点前移等）由真实模型行为决定，"
        "是本实验观察的重点。",
        "",
        "## 二、逐轮明细",
        "",
    ]
    for r in rows:
        if r.get("_error"):
            lines.append(f"### {r['scenario']}（异常）")
            lines.append(f"- `{r['_error']}`")
            lines.append("")
            continue
        lines.append(f"### {r['scenario']} → {r['actual_action']} "
                     f"({'PASS' if r.get('all_checks_ok') else 'CHECK-FAIL'})")
        lines.append(f"- 节点链：`{r['nodes']}`；LLM 调用 {r['llm_calls']} 次，"
                     f"耗时 {r['latency_ms']}ms，tokens {r['usage_total']}")
        if r.get("citations"):
            lines.append(f"- 引用：{r['citations']}")
        for k in sorted(r):
            if k.startswith("check_"):
                lines.append(f"- {k[6:]}：{r[k]}")
        sample = (r.get("response_text") or "").replace("\n", " ")
        lines.append(f"- 回复样例：{sample[:180]}{'…' if len(sample) > 180 else ''}")
        lines.append("")
    (OUT_DIR / "realrun_report.md").write_text("\n".join(lines), encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="教学策略图真实链路实验（DeepSeek）")
    parser.add_argument("--reps", type=int, default=DEFAULT_REPS, help="每个 LLM 场景重复次数（默认 3）")
    args = parser.parse_args()
    sys.exit(asyncio.run(main(args.reps)))
