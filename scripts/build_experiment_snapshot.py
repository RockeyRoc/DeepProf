"""Build a secret-free experiment snapshot and chart source data."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from typing import Any

try:
    from source_fingerprint import repository_fingerprint
except ImportError:  # test/package import path
    from scripts.source_fingerprint import repository_fingerprint

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "experiments"
SOURCE = DOCS / "source-data"
COURSE = ROOT / "data" / "courses" / "data_structures_c"
HOME = Path(os.environ.get("DEEPPROF_HOME", "~/.deepprof")).expanduser().resolve()


def read(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def sha256(path: Path) -> str | None:
    if not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def percentile(values: list[float], probability: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = max(0, min(len(ordered) - 1, round((len(ordered) - 1) * probability)))
    return round(ordered[index], 2)


def next_steps_for(snapshot: dict[str, Any]) -> list[str]:
    steps: list[str] = []
    bank = snapshot["question_bank"]
    page = snapshot["textbook_page_audit"]
    coverage = snapshot.get("course_coverage", {})
    audit = snapshot.get("acceptance_audit") or {}
    automated = audit.get("automated_checks") or {}
    validation = snapshot.get("validation", {})
    repeat_attempt = validation.get("repeat_after_fix_attempt", {})
    if audit.get("status") == "needs_attention":
        failed_cases = sorted({str(item.get("case_id")) for item in audit.get("findings", []) if item.get("case_id")})
        suffix = ", ".join(failed_cases) if failed_cases else "查看 acceptance-audit.json 中的失败项"
        codes = ", ".join(f"{name}×{count}" for name, count in sorted((automated.get("failure_reason_codes") or {}).items()))
        code_note = f"失败原因：{codes}。" if codes else ""
        steps.append(f"复核自动验收未通过案例：{suffix}。{code_note}保留原 run_id；使用 --repeat-after-fix 留存复测。")
    elif audit.get("status") == "passed":
        steps.append("自动规则检查已通过；继续进行教师评分、语义泄露与引用支持度人工标注。")
    else:
        steps.append("完成真实 A/B 运行后再判定 M1 行为验收；不以构造数据代替 Provider 运行。")
    if bank.get("teacher_approval") != "approved":
        steps.append("题库保持教师待审；独立核对题面、标准答案和评分规则后再审批。")
    if page.get("pending_manual_pages", 0):
        steps.append(f"继续逐页核验教材页码映射，当前尚有 {page['pending_manual_pages']} / {page['total_pdf_pages']} 页待核。")
    coverage_rows = coverage.get("coverage", []) if isinstance(coverage, dict) else []
    uncovered = sum(row.get("coverage_status") in {"uncovered", "not_covered", "pending"} for row in coverage_rows)
    if uncovered:
        steps.append(f"按课程覆盖表逐项评估 {uncovered} 个未覆盖或待核知识点；不使用无关题目填补。")
    if repeat_attempt.get("status") == "blocked_before_run":
        steps.append("修复后复测尚未启动：Provider 当前无可用凭据/探测返回 HTTP 401；在 deepprof login 更新有效 API Key 后再运行验收。")
    if validation.get("docx_visual_qa") not in {"passed", "accepted_by_user"}:
        steps.append("完成图表库 DOCX 逐页渲染与视觉复核；当前环境没有 LibreOffice 或 Microsoft Word 渲染器。")
    figure_qa = validation.get("figures", {})
    if any((figure_qa.get(key) or {}).get("status") != "passed" for key in ("pdf_text_audit", "pdf_collision_audit")):
        steps.append("用能正确处理 Cairo PDF 字体变换的检查器复核 F05 文本字号与碰撞扫描；现有扫描结果已留档并由 PNG 逐面板目视。")
    return steps


def write_source_note(snapshot: dict[str, Any]) -> None:
    bank = snapshot["question_bank"]
    cases = snapshot["constructed_cases"]
    pages = snapshot["textbook_page_audit"]
    ab = snapshot["real_ab_acceptance"]
    perf = snapshot["performance"]
    audit = snapshot.get("acceptance_audit") or {}
    checks = audit.get("automated_checks") or {}
    next_steps = snapshot.get("next_steps") or []
    text = f"""# 实验数据与来源说明

本说明与图表库的 Markdown、DOCX 和作图数据来自同一快照：`source-data/experiment-snapshot.json`（{snapshot['generated_at']}）。当前运行 {ab.get('run_id') or '尚无 run_id'}，Provider {ab.get('provider_profile') or '未配置'} / {ab.get('model') or '未配置'}；计划 {ab['planned_cells']} 格、终态完成 {ab['completed_cells']} 格，模型调用 {perf.get('model_calls') or 0} 次。独立自动审计状态为 {audit.get('status', 'not_run')}，规则通过 {checks.get('cells_passed', 0)} 格、未通过 {checks.get('cells_failed', 0)} 格。完成格表示回合正常终止，不等于教学行为已验收。费用为 {perf.get('estimated_cost') if perf.get('estimated_cost') is not None else '未估算'}；原因：{perf.get('cost_reason', '未配置价格')}。

## 数据来源与范围

- 首批题目转录来自用户提供的《数据结构题集（C语言版）》；当前收录 {bank['question_count']} 题，其中 {bank['verified_source_count']} 题来源已复核、{bank['deterministic_count']} 题具备经复核的确定性评分规则、{bank['pending_reference_count']} 题引用待核。原 PDF、答案页、OCR 全量结果和含答案题库仅保存在本机开发者测试目录。图表源数据不含题目正文、答案、截图或学生信息。
- 课程概念、学习目标和题目覆盖索引保存在仓库 `data/courses/data_structures_c/`；未覆盖知识点在快照和图表库中列明。
- {cases['total']} 个 A/B 测试案例（{cases.get('version') or '版本未记录'}）为人工构造的开发固定案例，不是学生或真人参与者数据。原始 Provider 输出只保存在本机开发测试目录；仓库作图表只含案例编号、分组、延迟、Token、调用数与终态。
- 配套教材页码盘点共有 {pages['total_pdf_pages']} 页，人工确认 {pages['manually_verified_pages']} 页、{pages['pending_manual_pages']} 页待核。逐页记录 PDF 页序、印刷页候选和状态；不使用统一页码偏移。
- 题集、教材和案例元数据 SHA-256 见本图表库快照。A/B 使用真实 Provider 时保留唯一 run_id、提交号与工作树差异指纹；构造案例运行只用于工程检查，不构成学生效果证据。

## 访问、许可和保留

数据可用性说明：本开发验收包提供不含题目正文和答案的代码、汇总指标、构造案例元数据及图表。扫描教材、答案、OCR 页面、原始模型输出和密钥不公开，也不上传仓库；仅在获准的本机开发目录中访问。用户确认材料用于团队内部测试，但该确认不构成独立许可核验或再分发授权。

对外发布前需核对教材题目和答案的授权范围。若不能公开原始材料，只发布足以复算图表的聚合源数据、字段说明、生成脚本和合法访问申请方式。敏感原始响应按本机实验目录权限保存；对外导出前执行脱敏和授权审查。

## FAIR 元数据检查

- **可发现：** 使用稳定课程 ID、题库版本、案例版本、图表编号、`run_id` 和 SHA-256。
- **可访问：** 仓库保留代码与非受限元数据；原始教材、答案和模型输出仅限本机开发目录，不虚构公开 DOI 或下载地址。
- **可互操作：** 汇总采用 UTF-8 JSON/CSV，字段名与单位明确；DOCX 与 Markdown 引用同一快照。
- **可复用：** 快照记录提交与工作树指纹、课程/策略/题库/案例版本、样本类型、分母、缺失状态和作图环境。教师评分、语义泄露和引用支持度在人工标注完成前均为待评。

## 当前下一步

""" + "\n".join(f"- {item}" for item in next_steps) + "\n\n" + "## 图表和数据对应关系\n\n`source-data/experiment-snapshot.json` 是 Markdown、DOCX 和图表的共同指标清单；`source-data/figure-data.csv` 与 `source-data/turn-performance.csv` 是 ggplot2 读取的脱敏作图表；`generate_charts.R` 使用 ggplot2 与 patchwork 生成 PNG、TIFF、SVG 和 PDF。图表定义、样本量、分子分母、解释和限制记录在 [图表库.md](图表库.md)。人工注释追加保存在 [图表人工注释.md](图表人工注释.md)，重建图表时不会覆盖。\n"
    (DOCS / "数据与来源说明.md").write_text(text, encoding="utf-8")


def load_live_results() -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    summary_path = HOME / "experiments" / "latest-summary.json"
    summary = read(summary_path)
    if not isinstance(summary, dict):
        return None, []
    raw_path = Path(str(summary.get("run_file") or ""))
    if not raw_path.is_absolute():
        raw_path = HOME / "experiments" / raw_path
    cells: list[dict[str, Any]] = []
    try:
        for line in raw_path.read_text(encoding="utf-8").splitlines():
            if line.strip():
                row = json.loads(line)
                turns = [{key: turn.get(key) for key in (
                    "trace_id", "turn_index", "elapsed_ms", "usage", "actions", "model_calls",
                    "terminal_status", "event_count",
                )} for turn in row.get("turns", []) if isinstance(turn, dict)]
                cells.append({key: row.get(key) for key in (
                    "run_id", "case_id", "group", "status", "elapsed_ms", "usage", "experiment", "trace_ids", "session_id",
                )} | {"turns": turns})
    except (OSError, json.JSONDecodeError):
        pass
    return summary, cells


def enrich_turns_from_events(cells: list[dict[str, Any]], database: Path) -> tuple[list[dict[str, Any]], int | None, str]:
    """Use the local event store to include failed calls and their safe usage totals."""
    if not database.is_file():
        return cells, None, "run_result_turns"
    uri = database.resolve().as_uri() + "?mode=ro"
    try:
        connection = sqlite3.connect(uri, uri=True)
    except sqlite3.Error:
        return cells, None, "run_result_turns"
    request_total = 0
    try:
        tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "events" not in tables:
            return cells, None, "run_result_turns"
        for cell in cells:
            session_id = str(cell.get("session_id") or "")
            traces = [str(value) for value in (cell.get("trace_ids") or []) if value]
            if not session_id or not traces:
                continue
            prior_turns = {str(row.get("trace_id") or ""): row for row in cell.get("turns", []) if row.get("trace_id")}
            enriched: list[dict[str, Any]] = []
            for index, trace_id in enumerate(traces, start=1):
                events = connection.execute(
                    "SELECT type,payload FROM events WHERE session_id=? AND trace_id=? ORDER BY sequence",
                    (session_id, trace_id),
                ).fetchall()
                model_calls = 0
                token_totals = {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0}
                usage_seen = False
                actions: list[str] = []
                terminal_status = "unknown"
                for event_type, raw_payload in events:
                    try:
                        payload = json.loads(raw_payload)
                    except (TypeError, json.JSONDecodeError):
                        payload = {}
                    if event_type == "model.requested":
                        model_calls += 1
                    elif event_type == "model.completed":
                        usage = payload.get("usage") or {}
                        if isinstance(usage, dict):
                            for key in token_totals:
                                value = usage.get(key)
                                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                                    token_totals[key] += int(value)
                                    usage_seen = True
                    elif event_type == "model.failed":
                        error = payload.get("error") if isinstance(payload.get("error"), dict) else {}
                        details = error.get("details") if isinstance(error.get("details"), dict) else {}
                        usage = details.get("usage") or payload.get("usage") or {}
                        if isinstance(usage, dict):
                            for key in token_totals:
                                value = usage.get(key)
                                if isinstance(value, (int, float)) and not isinstance(value, bool) and value >= 0:
                                    token_totals[key] += int(value)
                                    usage_seen = True
                    elif event_type in {"pedagogy.decision", "teaching.decision"}:
                        action = payload.get("action")
                        if action:
                            actions.append(str(action))
                    elif event_type == "agent.turn.completed":
                        terminal_status = str(payload.get("status") or "unknown")
                request_total += model_calls
                original = prior_turns.get(trace_id, {})
                if usage_seen:
                    usage = token_totals
                elif model_calls == 0:
                    usage = token_totals
                else:
                    usage = {}
                enriched.append({
                    **original,
                    "trace_id": trace_id,
                    "turn_index": original.get("turn_index", index),
                    "usage": usage,
                    "actions": original.get("actions") or actions,
                    "model_calls": model_calls,
                    "terminal_status": original.get("terminal_status") or terminal_status,
                    "event_count": original.get("event_count", len(events)),
                })
            cell["turns"] = enriched
    finally:
        connection.close()
    return cells, request_total, "session_events:model.requested"


def main() -> None:
    SOURCE.mkdir(parents=True, exist_ok=True)
    bank_path = HOME / "course" / "question_bank.json"
    bank = read(bank_path, {}) or {}
    cases_path = ROOT / "evaluation" / "dev_cases.json"
    cases = read(cases_path, {}) or {}
    audit_path = HOME / "course" / "textbook-page-audit.json"
    audit = read(audit_path, {}) or {}
    concepts = read(COURSE / "question_coverage.json", {}) or {}
    blocker = read(HOME / "experiments" / "acceptance-blocker.json")
    live_summary, cells = load_live_results()
    cells, event_model_calls, model_call_source = enrich_turns_from_events(cells, HOME / "sessions.sqlite")

    questions = bank.get("questions", []) if isinstance(bank, dict) else []
    by_module: dict[str, Counter[str]] = defaultdict(Counter)
    for row in questions:
        method = (row.get("grading") or {}).get("method")
        if row.get("source_review_status") != "verified":
            group = "reference_pending"
        elif method == "exact_normalized_match" and row.get("grading_review_status") == "verified":
            group = "deterministic"
        else:
            group = "manual"
        by_module[str(row.get("module") or "unknown")][group] += 1

    case_rows = cases.get("cases", []) if isinstance(cases, dict) else []
    case_counts = Counter(str(row.get("category") or "unknown") for row in case_rows)
    page_rows = audit.get("pages", []) if isinstance(audit, dict) else []
    # The page audit uses the explicit `manually_verified` state. Keep that
    # state visible in the aggregate rather than treating it as pending.
    verified_pages = sum(row.get("status") == "manually_verified" or str(row.get("status", "")).startswith("verified") for row in page_rows)
    pending_pages = len(page_rows) - verified_pages

    group_counts: dict[str, Counter[str]] = {"A": Counter(), "B": Counter()}
    for row in cells:
        group = str(row.get("group") or "")
        if group in group_counts:
            group_counts[group][str(row.get("status") or "unknown")] += 1
    for group in ("A", "B"):
        group_counts[group]["not_started"] = max(0, 40 - sum(group_counts[group].values()))

    turn_rows: list[dict[str, Any]] = []
    group_durations: dict[str, list[float]] = {"A": [], "B": []}
    group_usage = {group: {key: 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")} for group in ("A", "B")}
    group_usage_n = Counter()
    for cell in cells:
        group = str(cell.get("group") or "")
        if group not in {"A", "B"}:
            continue
        for turn in cell.get("turns") or []:
            if not isinstance(turn, dict):
                continue
            elapsed = turn.get("elapsed_ms")
            usage = turn.get("usage") or {}
            if isinstance(elapsed, (int, float)):
                group_durations[group].append(float(elapsed))
            observed_usage = any(isinstance(usage.get(key), (int, float)) for key in group_usage[group])
            if observed_usage:
                group_usage_n[group] += 1
                for key in group_usage[group]:
                    if isinstance(usage.get(key), (int, float)):
                        group_usage[group][key] += int(usage[key])
            turn_rows.append({
                "run_id": str(live_summary.get("run_id") or "") if live_summary else "",
                "case_id": str(cell.get("case_id") or ""),
                "group": group,
                "turn_index": turn.get("turn_index"),
                "elapsed_ms": round(float(elapsed), 2) if isinstance(elapsed, (int, float)) else None,
                "prompt_tokens": usage.get("prompt_tokens"),
                "completion_tokens": usage.get("completion_tokens"),
                "total_tokens": usage.get("total_tokens"),
                "model_calls": int(turn.get("model_calls") or 0),
                "terminal_status": str(turn.get("terminal_status") or "unknown"),
            })
    performance_by_group = {
        group: {
            "latency_ms": {"n": len(group_durations[group]), "median": percentile(group_durations[group], .5),
                           "p95": percentile(group_durations[group], .95)},
            "token_usage": {"n": group_usage_n[group], **(group_usage[group] if group_usage_n[group] else
                {key: None for key in group_usage[group]})},
            "model_calls": sum(row["model_calls"] for row in turn_rows if row["group"] == group),
        }
        for group in ("A", "B")
    }

    durations = [float(turn["elapsed_ms"]) for cell in cells for turn in (cell.get("turns") or [])
                 if isinstance(turn, dict) and isinstance(turn.get("elapsed_ms"), (int, float))]
    token_totals = {key: 0 for key in ("prompt_tokens", "completion_tokens", "total_tokens")}
    token_observed = 0
    model_calls = 0
    for cell in cells:
        for turn in cell.get("turns") or []:
            usage = turn.get("usage") or {}
            if any(isinstance(usage.get(key), (int, float)) for key in token_totals):
                token_observed += 1
                for key in token_totals:
                    if isinstance(usage.get(key), (int, float)):
                        token_totals[key] += int(usage[key])
            model_calls += int(turn.get("model_calls") or 0)

    run_id = str((live_summary or {}).get("run_id") or "") or None
    real_status = str((live_summary or {}).get("status") or (blocker or {}).get("status") or "not_started")
    if live_summary:
        acceptance_status = real_status
    elif blocker:
        acceptance_status = "blocked_before_run"
    else:
        acceptance_status = "not_started"

    acceptance_audit = read(SOURCE / "acceptance-audit.json", {}) or {}
    if acceptance_audit.get("run_id") != run_id:
        acceptance_audit = {}

    policy_source = (ROOT / "graph" / "education" / "contracts.py").read_text(encoding="utf-8")
    sampling_source = (ROOT / "graph" / "education" / "policies" / "__init__.py").read_text(encoding="utf-8")
    policy_match = re.search(r'^POLICY_VERSION\s*=\s*["\']([^"\']+)', policy_source, re.M)
    temperature_match = re.search(r"^GENERATE_TEMPERATURE\s*=\s*([0-9.]+)", sampling_source, re.M)

    source_hashes = {
        "question_bank_sha256": sha256(bank_path),
        "question_collection_pdf_sha256": (bank.get("source") or {}).get("question_pdf_sha256"),
        "companion_textbook_pdf_sha256": (bank.get("source") or {}).get("companion_textbook_sha256"),
        "constructed_case_file_sha256": sha256(cases_path),
        "textbook_page_audit_sha256": sha256(audit_path),
        "course_manifest_sha256": sha256(COURSE / "manifest.json"),
    }
    snapshot = {
        "snapshot_version": "deepprof-experiment-snapshot-v1",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "figure_backend": "R / ggplot2 + patchwork",
        # Bind the report to the code fingerprint captured when the live run
        # started; documentation and QA output can change after that run.
        "code": ((live_summary or {}).get("git") or repository_fingerprint(ROOT)),
        "source_hashes": source_hashes,
        "question_bank": {
            "version": bank.get("version"),
            "question_count": len(questions),
            "verified_source_count": sum(row.get("source_review_status") == "verified" for row in questions),
            "pending_reference_count": sum(row.get("source_review_status") != "verified" for row in questions),
            "deterministic_count": sum(row.get("grading_review_status") == "verified" and (row.get("grading") or {}).get("method") == "exact_normalized_match" and row.get("source_review_status") == "verified" for row in questions),
            "manual_or_not_enabled_count": sum(not (row.get("source_review_status") == "verified" and row.get("grading_review_status") == "verified" and (row.get("grading") or {}).get("method") == "exact_normalized_match") for row in questions),
            "teacher_approval": bank.get("teacher_approval", "pending"),
            "module_counts": dict(sorted(Counter(str(row.get("module") or "unknown") for row in questions).items())),
            "review_by_module": {module: dict(sorted(counts.items())) for module, counts in sorted(by_module.items())},
        },
        "constructed_cases": {
            "version": cases.get("version"),
            "sample_type": cases.get("sample_type", "constructed_developer_fixture"),
            "total": len(case_rows),
            "categories": dict(sorted(case_counts.items())),
            "human_subjects": bool(cases.get("human_subjects", False)),
        },
        "textbook_page_audit": {
            "total_pdf_pages": len(page_rows),
            "manually_verified_pages": verified_pages,
            "pending_manual_pages": pending_pages,
            "fixed_offset_assumed": False,
            "verified_mappings": [
                {"pdf_page": row.get("pdf_page"), "printed_page": row.get("printed_page_candidate"),
                 "chapter_heading": row.get("chapter_heading_candidate")}
                for row in page_rows if row.get("status") == "manually_verified"
            ],
        },
        "runtime_configuration": {
            "provider_profile": (live_summary or {}).get("provider_profile"),
            "model": (live_summary or {}).get("model"),
            "model_call_count": event_model_calls if event_model_calls is not None else int((live_summary or {}).get("model_call_count") or model_calls),
            "model_call_count_source": model_call_source,
            "policy_version": policy_match.group(1) if policy_match else None,
            "sampling_temperature": float(temperature_match.group(1)) if temperature_match else None,
            "retrieval": {"top_k": 5, "chunk_size": 800, "chunk_overlap": 120},
        },
        "real_ab_acceptance": {
            "status": acceptance_status,
            "run_id": run_id,
            "sample_type": "constructed_developer_fixture",
            "planned_cells": 80,
            "completed_cells": int((live_summary or {}).get("counts", {}).get("completed", 0)),
            "failed_cells": int((live_summary or {}).get("counts", {}).get("failed", 0)),
            "blocked_cells": int((live_summary or {}).get("counts", {}).get("blocked", 0)),
            "groups": {group: dict(sorted(counts.items())) for group, counts in group_counts.items()},
            "provider_profile": (live_summary or {}).get("provider_profile"),
            "model": (live_summary or {}).get("model"),
            "preflight_reason": (blocker or {}).get("reason") if not live_summary else None,
            "fake_provider_used": False,
        },
        "performance": {
            "latency_ms": {"n": len(durations), "median": percentile(durations, .5), "p95": percentile(durations, .95), "definition": "wall-clock per scripted model turn"},
            "token_usage": {"n": token_observed, **(token_totals if token_observed else {key: None for key in token_totals})},
            "model_calls": (event_model_calls if event_model_calls is not None else model_calls) if cells else None,
            "estimated_cost": None,
            "cost_reason": "No real run or pricing configuration is available." if not live_summary else "Price is not configured.",
        },
        "performance_by_group": performance_by_group,
        "acceptance_audit": acceptance_audit or None,
        "manual_evaluation": {
            "teacher_scoring": "pending_human_review",
            "semantic_leakage_labels": "pending_human_review",
            "citation_support_labels": "pending_human_review",
            "automated_acceptance": acceptance_audit.get("status", "not_run"),
            "fault_injection": (snapshot_validation := read(SOURCE / "validation.json", {})).get("fault_injection", "not_run"),
            "process_recovery": snapshot_validation.get("process_recovery", "not_run"),
        },
        "course_coverage": concepts,
        "validation": read(SOURCE / "validation.json", {"status": "not_recorded"}),
    }
    snapshot["next_steps"] = next_steps_for(snapshot)
    (SOURCE / "experiment-snapshot.json").write_text(json.dumps(snapshot, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    write_source_note(snapshot)

    with (SOURCE / "figure-data.csv").open("w", encoding="utf-8", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=["figure_id", "category", "status", "count", "denominator"])
        writer.writeheader()
        module_codes = {"线性表": "linear_list", "树": "tree", "图": "graph", "排序": "sorting"}
        for module, counts in sorted(by_module.items()):
            for state in ("deterministic", "manual", "reference_pending"):
                writer.writerow({"figure_id": "F01", "category": module_codes.get(module, "other"), "status": state, "count": counts[state], "denominator": sum(counts.values())})
        for group in ("A", "B"):
            statuses = group_counts[group]
            for state in ("completed", "failed", "blocked", "cancelled", "not_started"):
                writer.writerow({"figure_id": "F02", "category": group, "status": state, "count": statuses[state], "denominator": 40})
        writer.writerow({"figure_id": "F03", "category": "textbook", "status": "verified", "count": verified_pages, "denominator": len(page_rows)})
        writer.writerow({"figure_id": "F03", "category": "textbook", "status": "pending", "count": pending_pages, "denominator": len(page_rows)})
        for category, count in sorted(case_counts.items()):
            writer.writerow({"figure_id": "F04", "category": category, "status": "constructed_case", "count": count, "denominator": len(case_rows)})
        for group in ("A", "B"):
            for state in ("completed", "failed", "blocked", "cancelled", "not_started"):
                writer.writerow({"figure_id": "F05", "category": group, "status": state,
                                 "count": group_counts[group][state], "denominator": 40})

    with (SOURCE / "turn-performance.csv").open("w", encoding="utf-8", newline="") as stream:
        columns = ["run_id", "case_id", "group", "turn_index", "elapsed_ms", "prompt_tokens",
                   "completion_tokens", "total_tokens", "model_calls", "terminal_status"]
        writer = csv.DictWriter(stream, fieldnames=columns)
        writer.writeheader()
        writer.writerows(turn_rows)

    print(json.dumps({"snapshot": str(SOURCE / "experiment-snapshot.json"), "figures": 5,
                      "acceptance_status": acceptance_status, "completed_cells": snapshot["real_ab_acceptance"]["completed_cells"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
