#!/usr/bin/env python3
"""Build the source-grounded M1–M3 Markdown/PDF report and redacted evidence bundle."""
from __future__ import annotations

import csv
import hashlib
import html
import json
import os
import re
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from reportlab.lib import colors
from reportlab.lib.enums import TA_CENTER, TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (
    Image, KeepTogether, PageBreak, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle,
)

ROOT = Path(__file__).resolve().parents[1]
DOCS = ROOT / "docs" / "experiments"
SOURCE = DOCS / "source-data"
FIGURES = DOCS / "figures"
REPORT_MD = DOCS / "M1-M3-实验报告.md"
REPORT_PDF = DOCS / "M1-M3-experimental-report.pdf"
BUNDLE = DOCS / "releases" / "M1-M3-evidence.zip"
MANIFEST = SOURCE / "M1-M3-evidence-manifest.json"
FIELDS = SOURCE / "M1-M3-evidence-fields.md"
RELEASE_VALIDATION = SOURCE / "v0.6.2-release-validation.json"
M2_RELEASE_SUMMARY = SOURCE / "v0.6.2-m2-revalidation-summary.json"
M3_RELEASE_SUMMARY = SOURCE / "v0.6.2-m3-offline-summary.json"
M1_ID = "run_20260923T235151Z_ed03930c"
M2_JSON = ROOT / "docs" / "M2-offline-acceptance.json"
LIVE_JSON = SOURCE / "m3-live-pilot-live.json"
LIVE_CSV = SOURCE / "m3-live-pilot-live-cells.csv"
OFFLINE_EVIDENCE = SOURCE / "M3-offline-summary-redacted.json"
INK = colors.HexColor("#243746")
MUTED = colors.HexColor("#586873")
BLUE = colors.HexColor("#315D78")
GREEN = colors.HexColor("#477A66")
ORANGE = colors.HexColor("#C87537")
PALE = colors.HexColor("#EEF3F5")


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def json_bytes(value: Any) -> bytes:
    return (json.dumps(value, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


def load_evidence() -> dict[str, Any]:
    m1 = read_json(SOURCE / "acceptance-audit.json")
    snapshot = read_json(SOURCE / "experiment-snapshot.json")
    m2 = read_json(M2_JSON)
    offline_evidence = read_json(OFFLINE_EVIDENCE)
    offline = offline_evidence["summary"]
    offline_manifest = offline_evidence["frozen_configuration"]
    offline_provenance = {key: offline_evidence[key] for key in (
        "source_summary_sha256", "source_manifest_sha256") if key in offline_evidence}
    live = read_json(LIVE_JSON)
    release_validation = read_json(RELEASE_VALIDATION)
    m2_release = read_json(M2_RELEASE_SUMMARY)
    m3_release = read_json(M3_RELEASE_SUMMARY)
    return {"m1": m1, "snapshot": snapshot, "m2": m2, "offline": offline,
            "offline_manifest": offline_manifest, "live": live,
            "release_validation": release_validation, "m2_release": m2_release,
            "m3_release": m3_release, "offline_provenance": offline_provenance}


def verify(e: dict[str, Any]) -> None:
    m1 = e["m1"]
    m2 = e["m2"]
    offline = e["offline"]
    live = e["live"]
    validation = e["release_validation"]
    m2_release = e["m2_release"]
    m3_release = e["m3_release"]
    assert m1["run_id"] == M1_ID
    assert m1["matrix"]["planned"] == 80 and m1["matrix"]["completed"] == 76
    assert m1["automated_checks"]["failure_reason_codes"] == {"empty_model_response": 4}
    assert m2["status"] == "passed" and "82 passed" in m2["summary"]
    assert "309 passed" in (ROOT / "docs" / "M2-offline-acceptance.md").read_text(encoding="utf-8")
    assert "5 passed" in (ROOT / "docs" / "M2-offline-acceptance.md").read_text(encoding="utf-8")
    assert offline["run_id"] == "m3-offline-20260924-final6"
    assert offline["main_matrix"]["observed"] == 120
    assert offline["replay_matrix"]["observed"] == 120
    assert offline["rag_ablation_matrix"]["observed"] == 160
    metrics = offline["metrics"]
    assert metrics["expected_action_family_match"]["numerator"] == 196
    assert metrics["expected_action_family_match"]["denominator"] == 280
    assert metrics["locatable_citation_rate"]["numerator"] == 189
    assert metrics["decision_completeness"]["numerator"] == 520
    assert metrics["replay_decision_agreement"]["numerator"] == 120
    assert metrics["bkt_prediction"]["n"] == 120
    assert abs(metrics["bkt_prediction"]["auc"]["value"] - 0.2964285714) < 1e-9
    assert metrics["evidence_gap_behavior"]["retrieval_absent_constraint_on_blocks"] == 40
    assert metrics["evidence_gap_behavior"]["retrieval_absent_constraint_off_generated"] == 26
    assert live["sample"]["observed_cells"] == 36
    assert live["limits"]["actual_provider_requests"] == 29
    assert live["evaluation_judgment"]["cell_outcomes"] == {
        "completed": 3, "failed_truncated": 26, "failed_incomplete": 0,
        "failed_provider": 0, "application_failed": 0, "not_applicable": 7, "unknown": 0,
    }
    assert sum(group["terminal_completed"] for group in live["groups"].values()) == 26
    assert sum(group["terminal_failed"] for group in live["groups"].values()) == 10
    assert m2_release["denominator"] == {"passed": 82, "total": 82, "failures": 0}
    assert m2_release["requires_paid_model"] is False and m2_release["requires_network"] is False
    assert m3_release["run_id"] == "m3-offline-v0.6.2-release-20260924"
    assert m3_release["main_matrix"]["completed"] == 120
    assert m3_release["replay_matrix"]["completed"] == 120
    assert m3_release["ablation_matrix"]["completed"] == 160
    assert validation["denominators"]["m3_offline_total"] == 400
    assert validation["checks"]["github_remote"].startswith("pending:")
    with (SOURCE / "F09-m3-offline-summary.csv").open(encoding="utf-8", newline="") as stream:
        rows = list(csv.DictReader(stream))
    assert len(rows) == 12
    matrix_rows = [row for row in rows if row["metric"] == "matrix_coverage"]
    assert sum(int(row["observed"]) for row in matrix_rows) == 400
    assert sum(int(row["planned"]) for row in matrix_rows) == 400


def safe_m2(m2: dict[str, Any]) -> dict[str, Any]:
    return {
        "acceptance_version": m2.get("acceptance_version"),
        "sample_type": m2.get("sample_type"),
        "human_subjects": m2.get("human_subjects"),
        "requires_network": m2.get("requires_network"),
        "requires_paid_model": m2.get("requires_paid_model"),
        "created_at": m2.get("created_at"),
        "status": m2.get("status"),
        "elapsed_seconds": m2.get("elapsed_seconds"),
        "summary": m2.get("summary"),
        "failure_cases": m2.get("failure_cases"),
        "bkt_parameters": m2.get("bkt_parameters"),
        "code_fingerprint": m2.get("code_fingerprint"),
    }


def safe_offline(offline: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "schema_version", "run_id", "generated_at", "started_at", "sample_type", "provider",
        "status", "main_matrix", "replay_matrix", "rag_ablation_matrix", "metrics",
        "human_review", "teacher_approval", "real_provider_experiment", "student_learning_effect",
        "all_cells_completed", "session_isolation_passed", "failed_cells", "source_fingerprint",
    )
    return {
        "summary": {key: offline[key] for key in keys if key in offline},
        "frozen_configuration": {
            key: manifest.get(key) for key in (
                "case_version", "source_case_version", "source_case_sha256", "m3_case_sha256",
                "sample_type", "human_subjects", "provider_profile", "model", "fake_provider_used",
                "paid_model_used", "network_required", "course_id", "policy_version",
                "bkt_parameters", "bkt_config_hash", "strategy_bindings_sha256",
                "prompt_version", "prompt_sha256", "sampling", "retrieval", "experiment_design",
                "source_fingerprint",
            ) if key in manifest
        },
    }


def safe_live(live: dict[str, Any]) -> dict[str, Any]:
    return {
        "schema_version": live.get("schema_version"),
        "run_id": live.get("run_id"),
        "status": live.get("status"),
        "evaluation_run_status": live.get("evaluation_run_status"),
        "started_at": live.get("started_at"),
        "finished_at": live.get("finished_at"),
        "provider": live.get("provider"),
        "sample": {key: value for key, value in live.get("sample", {}).items()
                   if key in ("case_count", "observed_cells", "groups", "type", "human_subjects")},
        "limits": live.get("limits"),
        "groups": live.get("groups"),
        "evaluation_judgment": live.get("evaluation_judgment"),
        "provider_output": live.get("provider_output"),
        "token_usage": live.get("token_usage"),
        "price_estimate": live.get("price_estimate"),
        "metrics": live.get("metrics"),
        "frozen_configuration": {
            key: live.get("frozen_configuration", {}).get(key) for key in (
                "course_id", "case_version", "source_case_version", "source_case_sha256",
                "selected_cases_sha256", "prompt_version", "prompt_sha256", "policy_version",
                "bkt_config_sha256", "temperature", "indexed_chunk_count", "source_fingerprint",
            ) if key in live.get("frozen_configuration", {})
        },
        "redaction": "No case identifiers, prompt text, retrieved content, answer text, trace/session IDs, or credentials.",
    }


def evidence_manifest(e: dict[str, Any]) -> dict[str, Any]:
    m1, snapshot, m2, offline, om, live, validation = (
        e["m1"], e["snapshot"], e["m2"], e["offline"], e["offline_manifest"],
        e["live"], e["release_validation"])
    result = {
        "schema_version": "deepprof-m1-m3-evidence-v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "purpose": "Engineering validation and failure analysis; no student learning effect claim.",
        "runs": [
            {
                "module": "M1 live A/B",
                "run_id": m1["run_id"],
                "run_time_utc_from_run_id": "2026-09-23T23:51:51Z",
                "audit_generated_at": m1.get("generated_at"),
                "config": {"provider": m1.get("provider_profile"), "model": m1.get("model"),
                           "groups": ["A", "B"], "cases": 40,
                           "course_id": m1["frozen_configuration"]["observed_course_ids"],
                           "policy_version": m1["frozen_configuration"]["run_policy_version"],
                           "retrieval": m1["frozen_configuration"]["run_retrieval"],
                           "sampling": m1["frozen_configuration"]["run_sampling"]},
                "sample_type": m1.get("sample_type"),
                "human_subjects": False,
                "denominator": {"planned_cells": 80, "observed_cells": 80},
                "outcome": {"completed": 76, "failed": 4, "failure_reason": "empty_model_response"},
                "code_fingerprint": {
                    "commit": snapshot["code"]["commit"],
                    "working_tree_dirty": snapshot["code"]["working_tree_dirty"],
                    "working_tree_status_sha256": snapshot["code"]["working_tree_status_sha256"],
                    "working_tree_diff_sha256": snapshot["code"]["working_tree_diff_sha256"],
                    "audit_fingerprint_available": m1["frozen_configuration"]["run_fingerprint_available"],
                    "source_note": "Linked to this run_id in the historical M1 snapshot and first-batch record.",
                },
                "data_sources": ["source-data/acceptance-audit.json", "source-data/experiment-snapshot.json",
                                 "M1-first-batch.md"],
                "figure_ids": ["F02", "F05"],
            },
            {
                "module": "M2 engineering acceptance",
                "run_id": m2.get("acceptance_version") + "@" + m2.get("created_at", ""),
                "run_time_utc": datetime.fromisoformat(m2["created_at"]).astimezone(timezone.utc).isoformat(),
                "config": {"runtime": "Python 3.12.14", "sample_type": m2.get("sample_type"),
                           "requires_network": False, "requires_paid_model": False},
                "sample_type": m2.get("sample_type"),
                "human_subjects": False,
                "denominator": {"focused_cases": 82, "python_regression": 309, "cli_unit": 5},
                "overlap": "82 focused cases are a subset of 309 Python tests; do not add these counts.",
                "outcome": "passed; one non-blocking dependency deprecation warning",
                "code_fingerprint": {
                    "commit": m2["code_fingerprint"]["commit"],
                    "working_tree_dirty": m2["code_fingerprint"]["working_tree_dirty"],
                    "working_tree_status_sha256": m2["code_fingerprint"]["working_tree_status_sha256"],
                    "working_tree_diff_sha256": m2["code_fingerprint"]["working_tree_diff_sha256"],
                },
                "data_sources": ["docs/M2-offline-acceptance.json", "docs/M2-offline-acceptance.md",
                                 "source-data/m2-validation.csv"],
                "figure_ids": ["F06"],
            },
            {
                "module": "M3 offline Fake Provider",
                "run_id": offline["run_id"],
                "run_time_utc": offline.get("generated_at"),
                "config": {"provider": om.get("provider_profile"), "model": om.get("model"),
                           "sample_type": om.get("sample_type"), "cases": 40, "groups": ["A", "B", "C"],
                           "design": om.get("experiment_design")},
                "sample_type": offline.get("sample_type"),
                "human_subjects": False,
                "denominator": {"main": 120, "replay": 120, "ablation": 160, "total_cells": 400,
                                "decision_completeness": 520, "bkt_predictions": 120},
                "outcome": {"status": offline.get("status"), "action_family_match": "196/280",
                            "locatable_references": "189/189", "replay_agreement": "120/120",
                            "bkt_auc": offline["metrics"]["bkt_prediction"]["auc"]["value"],
                            "bkt_brier": offline["metrics"]["bkt_prediction"]["brier"]["value"],
                            "bkt_log_loss": offline["metrics"]["bkt_prediction"]["log_loss"]["value"],
                            "bkt_ece": offline["metrics"]["bkt_prediction"]["ece_10_bins"]["value"]},
                "code_fingerprint": om["source_fingerprint"],
                "data_sources": ["source-data/M3-offline-summary-redacted.json",
                                 "source-data/F09-m3-offline-summary.csv"],
                "figure_ids": ["F09"],
            },
            {
                "module": "M3 live model pilot",
                "run_id": live["run_id"],
                "run_time_utc": live["started_at"],
                "config": {"provider": live["provider"], "cases": 12, "groups": ["A", "B", "C"],
                           "max_requests": 36, "actual_requests": 29, "max_tokens": 512, "retries": 0},
                "sample_type": live["sample"]["type"],
                "human_subjects": False,
                "denominator": {"observed_cells": 36, "provider_requests": 29,
                                "full_generation": 3, "truncated": 26, "not_generated": 7},
                "outcome": {"application_terminal_completed": 26, "application_terminal_failed": 10,
                            "evaluation_full_generation": 3, "evaluation_truncation_failed": 26,
                            "evaluation_not_applicable": 7, "completion_tokens_reported_above_limit": 1},
                "code_fingerprint": live["frozen_configuration"]["source_fingerprint"],
                "data_sources": ["source-data/m3-live-pilot-live.json",
                                 "source-data/m3-live-pilot-live-cells.csv", "M3-live-pilot.md"],
                "figure_ids": ["F07", "F08"],
            },
            {
                "module": "v0.6.2 release validation (separate from historical experiments)",
                "run_id": validation["run_id"],
                "run_time_utc": validation["generated_at_utc"],
                "config": validation["environment"],
                "sample_type": validation["sample_type"],
                "human_subjects": False,
                "denominator": validation["denominators"],
                "overlap": validation["overlap"],
                "outcome": validation["checks"],
                "code_fingerprint": validation["code_fingerprint"],
                "data_sources": ["source-data/v0.6.2-release-validation.json",
                                 "source-data/v0.6.2-m2-revalidation-summary.json",
                                 "source-data/v0.6.2-m3-offline-summary.json",
                                 "source-data/F06-qa.json", "source-data/F09-qa.json"],
                "figure_ids": ["F06", "F09"],
            },
        ],
        "figures": [
            {"id": "F01", "n": 30, "unit": "questions", "source": "source-data/figure-data.csv; experiment-snapshot.json", "boundary": "Question bank composition; not learning outcomes."},
            {"id": "F02", "n": 80, "unit": "constructed A/B case cells", "source": "source-data/figure-data.csv; acceptance-audit.json", "boundary": "76 completed, four failures preserved; no human subjects."},
            {"id": "F03", "n": 347, "unit": "PDF pages", "source": "source-data/figure-data.csv; experiment-snapshot.json", "boundary": "Seven page mappings manually checked; 340 pending."},
            {"id": "F04", "n": 40, "unit": "constructed cases", "source": "source-data/figure-data.csv; case version ds-dev-cases-v1", "boundary": "Eight software scenario categories, not participants."},
            {"id": "F05", "n": "76/80 cells; 136 latency and 140 token turns", "unit": "cells, ms/turn, tokens/turn", "source": "source-data/turn-performance.csv; experiment-snapshot.json", "boundary": "Engineering timings only; not learning effect."},
            {"id": "F06", "n": "309 Python; 82 focused subset; 5 CLI", "unit": "fixed test cases", "source": "source-data/m2-validation.csv; docs/M2-offline-acceptance.json", "boundary": "Focused set is contained in Python total."},
            {"id": "F07", "n": 36, "unit": "planned local preflight cells", "source": "source-data/m3-live-pilot-preflight.csv; .json", "boundary": "Historical zero-call preflight; not the later actual pilot."},
            {"id": "F08", "n": "36 cells; 29 provider requests", "unit": "cells and requests", "source": "source-data/m3-live-pilot-live-cells.csv; redacted aggregate JSON", "boundary": "Three complete generations, 26 truncations, seven not applicable."},
        ] + [{
            "id": "F09", "formats": ["PNG", "SVG", "PDF", "TIFF"],
            "n": "120 main + 120 replay + 160 ablation cells; BKT predictions n=120",
            "unit": "isolated constructed case cells, decisions, references, predictions",
            "source": "source-data/F09-m3-offline-summary.csv; M3-offline-summary-redacted.json",
            "boundary": "Fake Provider and constructed history; not teacher ratings or student learning evidence.",
        }],
        "publication_safety": {
            "included": ["aggregate counts", "redacted per-cell outcome fields", "plots", "reproduction scripts"],
            "excluded": ["textbooks", "answer keys", "raw model output", "prompts", "retrieved excerpts",
                         "API credentials", "trace/session/learner identifiers", "absolute local paths"],
        },
        "figure_environment": {
            "record": "source-data/plot-runtime.txt",
            "backend": "R with ggplot2; patchwork for multi-panel figures; ragg, svglite, base PDF; Cairo PDF for F06",
            "note": "Exact package versions are retained in the plot runtime record.",
        },
    }
    for figure in result["figures"]:
        figure.setdefault("formats", ["PNG", "SVG", "PDF", "TIFF"])
    return result


def csv_text(rows: list[list[Any]]) -> str:
    import io
    stream = io.StringIO(newline="")
    writer = csv.writer(stream, lineterminator="\n")
    writer.writerows(rows)
    return stream.getvalue()


def make_m1_failure_csv(e: dict[str, Any]) -> str:
    return csv_text([
        ["run_id", "record_type", "case_id", "group", "outcome", "count", "planned_group_cells", "reason_code"],
        [M1_ID, "group_summary", "", "A", "completed", 40, 40, ""],
        [M1_ID, "group_summary", "", "B", "completed", 36, 40, ""],
        [M1_ID, "failed_case", "DSDEV-001", "B", "failed", 1, 40, "empty_model_response"],
        [M1_ID, "failed_case", "DSDEV-016", "B", "failed", 1, 40, "empty_model_response"],
        [M1_ID, "failed_case", "DSDEV-018", "B", "failed", 1, 40, "empty_model_response"],
        [M1_ID, "failed_case", "DSDEV-020", "B", "failed", 1, 40, "empty_model_response"],
    ])


def markdown_report(e: dict[str, Any], manifest: dict[str, Any]) -> str:
    offline = e["offline"]
    metrics = offline["metrics"]
    live = e["live"]
    bkt = metrics["bkt_prediction"]
    return "\n".join([
        "# DeepProf M1–M3 工程实验报告",
        "",
        "> v0.6.2 发布材料 · 生成于 2026-09-24 · 证据清单见 [M1–M3-evidence-manifest.json](source-data/M1-M3-evidence-manifest.json)。本报告汇总构造开发案例、自动化测试和受限真实模型试跑；不包含教师评审或真实学生学习效果证据。",
        "",
        "## 研究问题",
        "",
        "本报告检查 DeepProf 的教学策略链路、学习历史与 BKT 更新、证据检索及离线回放能否稳定运行；同时记录真实 Provider 小规模试跑中的工程失败。指标用于复核功能路径、数据隔离、终态处理与证据字段，不用于宣称教学有效或 A/B/C 组优劣。",
        "",
        "## 系统与实验方法",
        "",
        "当前产品路径由 npm CLI、本机 Gateway/API、教学策略图、课程与题库元数据、证据检索、SQLite Attempt/BKT 存储和只读事件回放组成。A 采用基线苏格拉底策略，B 采用规则教学图，C 在 B 的教学流程中启用 BKT 学情读取与更新。策略判断由固定策略规则驱动；BKT 使用四参数更新，只有可靠、可归属的作答进入估计，重复、提示、人工待评及不明确概念会跳过。开发初值为 P(L0)=0.20、P(T)=0.10、P(G)=0.20、P(S)=0.10，仍未拟合。",
        "",
        "实验批次按运行 ID 隔离。M1 使用 40 个构造案例分别运行 A/B；M2 为离线固定测试集；M3 离线使用 Fake Provider 和构造 Attempt 历史，另单列一次受预算约束的真实 DeepSeek 试跑。详细时间、配置、样本类型、分母、数据文件、图号和可用代码指纹记录在统一证据清单中。M1 指纹来自关联该 run_id 的历史快照与首批记录。所有批次均无真人参与者。",
        "",
        "## M1：A/B 工程运行",
        "",
        f"运行 {M1_ID} 计划 40 个案例 × A/B = 80 格，全部有运行记录；76 格应用完成、4 格失败。A 为 40/40 完成；B 为 36/40 完成。四个失败格均标记为 empty_model_response：Provider 以长度上限结束但没有可用正文，案例审计保留失败终态。该运行发生 123 次 Provider 请求；修复后的复测在 HTTP 401 前置检查时停止，未发起新的案例调用。",
        "",
        "失败案例 DSDEV-001、DSDEV-016、DSDEV-018、DSDEV-020 均属于 B 组；Provider 以长度上限终止且未返回正文。公开附件只保留构造案例号和结构化原因码，不含 trace、会话、提示或回答内容。",
        "",
        "M1 是真实 Provider 上的构造案例工程运行，不是学生试验。自动行为审计为 76/80 格通过且状态 needs_attention；通过不能替代教师对动作适切性、答案泄露或引用语义支持的判断。旧报告记录运行后续修复，不改写本次 4 个失败结果。",
        "",
        "## M2：学习历史与离线验收",
        "",
        "当前验收记录为 82 项定向验收、309 项 Python 全量回归、5 项 CLI 测试及 TypeScript 类型检查通过；定向 82 项已包含在 309 项回归中，不能把两组数相加。此前图表快照保留的 77/300/3 属于较早记录，F06 已按新验收结果更新。Python 全量回归有一条不阻断的 Starlette/AnyIO 弃用警告。",
        "",
        "离线检查覆盖 BKT 数值顺序、Attempt 幂等/资格、并发与事务回滚、跨会话恢复、A/B/C 隔离、题库/转换边界和契约守卫。这些是工程测试通过数，不是观测到的学习样本量；题库与参数教师审核待完成，BKT 参数尚未校准。",
        "",
        "## v0.6.2：独立发布验证",
        "",
        f"发布核验 run_id {e['release_validation']['run_id']} 与历史 M1–M3 批次分开记录。Windows 本地复核通过 Python 全量回归 {e['release_validation']['denominators']['python_regression']} 项、M2 定向 {e['release_validation']['denominators']['m2_focused']} 项、CLI {e['release_validation']['denominators']['cli_unit']} 项和类型检查；M2 定向集包含于同次 Python 全量回归，不相加。新建 Fake Provider 离线核验完成主矩阵 120、回放 120、消融 160 格；未发起付费模型请求。Windows/macOS/Linux 远程 CI 与公开 Release URL 端到端检查尚待 GitHub Release 工作流执行，不计作本地已通过项。",
        "",
        "## M3：离线框架与消融",
        "",
        f"运行 {offline['run_id']} 使用显式 Fake Provider、40 个构造案例及合成 Attempt 历史。主矩阵 A/B/C 各 40 格，共 120/120；新会话决策回放 120/120；检索 × 证据约束消融 160/160；合计 400 格均完成。决策字段完整 520/520，回放动作一致 120/120，策略动作族匹配 196/280（70.0%），可定位引用字段 189/189。引用可定位只验证引用字段存在和可解析，不证明引文支持回答。",
        "",
        f"BKT 的 120 个构造预测为 AUC {bkt['auc']['value']:.4f}、Brier {bkt['brier']['value']:.4f}、log loss {bkt['log_loss']['value']:.4f}、ECE {bkt['ece_10_bins']['value']:.4f}（正例 {bkt['positive_count']}、负例 {bkt['negative_count']}）。AUC 低于随机参照 0.5，ECE 较高；这是不利的预测表现，显示初始参数与构造标签不匹配，不能宣称 BKT 已校准或有效。消融中无检索且约束开启时 40/40 阻止生成；约束关闭时 26/40 触发生成，说明安全开关按预期影响路径，但不代表生成内容正确。",
        "",
        "## M3：真实模型试跑",
        "",
        f"运行 {live['run_id']} 使用 12 个构造案例 × A/B/C = 36 格，最多允许 36 次请求、每格至多一条、零重试；实际请求 29 次，另 7 格未触发生成。应用终态为 26/36 完成、10/36 失败；评测结果按 Provider 终止原因单独判定：29 次请求中 3 次完整生成、26 次因 finish_reason=length 截断失败。B/C 有应用终态完成但生成实际截断的格，故应用“完成”不等于完整生成。一次回报 513 个 completion tokens，高于所设 512 上限 1 个；原值保留且未重试。",
        "",
        "所有样本仍是开发构造案例，无学生参与或教师评分。B/C 的 100/100 引用定位字段只说明元数据可定位；C 组 36 条历史为合成 Attempt。该试跑验证请求上限、错误分类和截断判定，不支持模型回答质量或组间效果结论。F07 是更早的本地检索预检历史图，显示零模型调用；真实请求及截断结果见 F08。",
        "",
        "## 局限与下一步",
        "",
        "尚待教师盲评策略适切性、答案泄露和引用语义支持；核验课程覆盖并完成 BKT 参数校准；针对截断行为调整输出协议后，须先冻结新版本并在不混合旧批次的前提下复测。还没有教师评分一致性、真实学生参与者或学习增益测量。公开附件只提供可审计的聚合数据、脱敏格级结果、图表、字段说明与作图脚本，不含教材、标准答案、原始模型输出、提示正文、检索片段或凭据。",
        "",
        "## 图表索引与解释边界",
        "",
        "| 图 | 样本量与单位 | 数据来源 | 解释边界 |",
        "|---|---|---|---|",
        "| F01–F05 | 题目 N=30；构造案例 N=40；A/B 计划 80 格；脚本化回合 | experiment-snapshot 与 figure-data.csv | 材料、完成及工程运行描述，不是学习效果 |",
        "| F06 | Python 全量 309 项、M2 定向 82 项、CLI 5 项 | M2-offline-acceptance 与 m2-validation.csv | 定向子集包含于 Python 总集；不得相加 |",
        "| F07 | 12 案例 × A/B/C 预检，共 36 个计划格 | m3-live-pilot-preflight.* | 历史本地预检；0 次模型调用，不代表后来真实试跑 |",
        "| F08 | 36 个格、29 次请求；输出请求结果单位为次 | m3-live-pilot-live-cells.csv 与聚合 JSON | 3 次完整生成、26 次截断、7 格不适用；无真人样本 |",
        "| F09 | 主矩阵 120、回放 120、消融 160 格；预测 n=120 | F09-m3-offline-summary.csv 与离线 run summary | Fake Provider 与合成历史；BKT 指标为不利工程信号 |",
        "",
        "各图同时导出 PNG、SVG、PDF、TIFF。图注和实验数据字段说明见 [图表库](图表库.md)、[证据字段说明](source-data/M1-M3-evidence-fields.md)及[证据清单](source-data/M1-M3-evidence-manifest.json)。",
        "",
    ])


def build_styles() -> dict[str, ParagraphStyle]:
    sample = getSampleStyleSheet()
    return {
        "title": ParagraphStyle("DeepTitle", parent=sample["Title"], fontName="DeepCJK",
                                fontSize=25, leading=33, textColor=INK, alignment=TA_CENTER, spaceAfter=8),
        "subtitle": ParagraphStyle("DeepSub", parent=sample["Normal"], fontName="DeepCJK",
                                   fontSize=10, leading=16, textColor=MUTED, alignment=TA_CENTER),
        "h1": ParagraphStyle("DeepH1", parent=sample["Heading1"], fontName="DeepCJK",
                             fontSize=15, leading=21, textColor=INK, spaceBefore=5, spaceAfter=4),
        "h2": ParagraphStyle("DeepH2", parent=sample["Heading2"], fontName="DeepCJK",
                             fontSize=10.5, leading=15, textColor=BLUE, spaceBefore=5, spaceAfter=4),
        "body": ParagraphStyle("DeepBody", parent=sample["BodyText"], fontName="DeepCJK",
                               fontSize=9, leading=13, textColor=INK, alignment=TA_LEFT, spaceAfter=4),
        "small": ParagraphStyle("DeepSmall", parent=sample["BodyText"], fontName="DeepCJK",
                                fontSize=7.7, leading=11, textColor=MUTED, spaceAfter=4),
        "cell": ParagraphStyle("DeepCell", parent=sample["BodyText"], fontName="DeepCJK",
                               fontSize=7.2, leading=10, textColor=INK),
    }


def para(text: str, style: ParagraphStyle) -> Paragraph:
    return Paragraph(html.escape(text).replace("\n", "<br/>"), style)


def draw_footer(canvas: Any, doc: Any) -> None:
    canvas.saveState()
    width, _ = A4
    canvas.setStrokeColor(colors.HexColor("#D7E0E4"))
    canvas.line(18 * mm, 15 * mm, width - 18 * mm, 15 * mm)
    canvas.setFont("DeepCJK", 7)
    canvas.setFillColor(MUTED)
    canvas.drawString(18 * mm, 10 * mm, "DeepProf v0.6.2 · M1–M3 工程实验报告")
    canvas.drawRightString(width - 18 * mm, 10 * mm, str(doc.page))
    canvas.restoreState()


def plot_page(story: list[Any], fig_id: str, title: str, image_name: str,
              n_text: str, source: str, boundary: str, styles: dict[str, ParagraphStyle],
              page_break: bool = True) -> None:
    if page_break:
        story.append(PageBreak())
    story.extend([
        para(f"{fig_id} · {title}", styles["h1"]),
        para(f"样本量与单位：{n_text}", styles["small"]),
    ])
    image = Image(str(FIGURES / image_name))
    image._restrictSize(174 * mm, 119 * mm)
    story.extend([
        image, Spacer(1, 4 * mm),
        para(f"数据来源：{source}", styles["small"]),
        para(f"解释边界：{boundary}", styles["small"]),
    ])


def build_pdf(e: dict[str, Any], manifest: dict[str, Any]) -> None:
    font_path = Path(os.environ.get("DEEPPROF_CJK_FONT", "msyh.ttc"))
    if not font_path.exists():
        raise FileNotFoundError("Set DEEPPROF_CJK_FONT to an installed CJK TrueType font before building the PDF.")
    pdfmetrics.registerFont(TTFont("DeepCJK", str(font_path), subfontIndex=0))
    styles = build_styles()
    doc = SimpleDocTemplate(str(REPORT_PDF), pagesize=A4, rightMargin=18 * mm, leftMargin=18 * mm,
                            topMargin=18 * mm, bottomMargin=21 * mm, title="DeepProf M1–M3 工程实验报告",
                            author="DeepProf")
    story: list[Any] = [
        Spacer(1, 29 * mm),
        para("DeepProf", styles["title"]),
        para("M1–M3 工程实验报告", styles["title"]),
        para("v0.6.2 · 中文版 · 2026-09-24", styles["subtitle"]),
        Spacer(1, 15 * mm),
        para("本报告梳理 A/B/C 教学流程、BKT 工程验证、离线回放与真实模型截断试跑。数据仅用于工程验收与失败分析，没有真实学生参与或学习效果结论。", styles["body"]),
        Spacer(1, 8 * mm),
        para("核心结果：M1 完成 76/80 格；M2 历史批次 82 项定向与 309 项回归通过；发布核验 Python 318 项通过；M3 离线 400 格完成但 BKT 预测不佳；真实模型试跑仅 3 次完整生成，26 次截断。", styles["body"]),
        PageBreak(),
    ]
    sections = [
        ("研究问题", [
            "评估本地教学策略、学习历史/BKT、证据检索与回放的工程路径是否可运行、可隔离并能保留失败证据。",
            "构造案例和离线测试不能替代教师评审或真实学生研究；报告只支持工程验证与失败定位。",
        ]),
        ("系统与实验方法", [
            "系统路径：npm CLI → 本机 Gateway/API → 策略图与技能 → 课程/证据检索 → Attempt/BKT 存储与事件回放。A 为基线策略，B 为规则教学图，C 在 B 的教学流程中启用 BKT 学情读取。",
            "BKT 仅用可靠、明确归属、未提示的已评分 Attempt；重复或待评记录跳过。参数 P(L0)=0.20、P(T)=0.10、P(G)=0.20、P(S)=0.10 是开发初值，尚未拟合。",
            "每个批次保留 run_id、时间、样本类型、配置、分母、数据关联、图号和可用源指纹。历史运行不与本轮打包验证混合。",
        ]),
        ("M1 · A/B 工程运行", [
            f"运行 {M1_ID} 覆盖 40 个构造案例 × A/B 共 80 格，76 格完成、4 格失败；A 组 40/40，B 组 36/40。4 格均为 empty_model_response。审计运行记录完整，自动行为核验 76/80，通过状态为 needs_attention。",
            "失败案例 DSDEV-001、DSDEV-016、DSDEV-018、DSDEV-020 均属于 B 组；Provider 以长度上限终止且未返回正文。只保留构造案例号与结构化错误原因。",
            "原试验调用 123 次 Provider。之后修复后的复测在 HTTP 401 前置检查处终止，未发送案例。M1 审计没有导出可独立核验的指纹摘要，证据清单记录该限制。",
        ]),
        ("M2 · 工程验收", [
            "历史冻结 M2 批次的定向验收 82 项、Python 全量回归 309 项和 CLI 测试 5 项均通过，TypeScript 类型检查通过。82 项属于 309 项的子集，不得相加；更早的 77/300/3 也作为历史记录保留。",
            "测试覆盖 BKT 更新、Attempt 幂等/资格、SQLite 并发与事务、组间隔离、课程题库、文档转换和契约。教师审核和 BKT 参数校准尚未完成。",
        ]),
        ("v0.6.2 · 独立发布验证", [
            "本次发布验证与历史批次分开记录：Python 回归 318/318，M2 定向 82 项包含在这 318 项内；CLI 7/7、TypeScript 构建/类型检查通过。新增 Fake Provider 离线验证完成主矩阵 120、回放 120、消融 160 格，未调用付费模型。",
            "最终 CLI tar 在本机以 npx 执行版本、帮助、安装复用与 Gateway doctor 检查通过。跨平台 GitHub CI 和公开 Release URL 端到端安装尚待远程发布工作流执行；不计为已通过。",
        ]),
        ("M3 · Fake Provider 离线试跑", [
            "40 个构造案例产生主矩阵 120/120、回放 120/120、RAG 消融 160/160，共 400 格。动作族匹配 196/280；回放一致 120/120；决策字段完整 520/520；引用字段可定位 189/189。",
            f"BKT 预测 n=120：AUC {e['offline']['metrics']['bkt_prediction']['auc']['value']:.4f}，Brier {e['offline']['metrics']['bkt_prediction']['brier']['value']:.4f}，log loss {e['offline']['metrics']['bkt_prediction']['log_loss']['value']:.4f}，ECE {e['offline']['metrics']['bkt_prediction']['ece_10_bins']['value']:.4f}。AUC 低于 0.5 参照且校准误差偏高，是应保留的不利结果。",
            "无检索且证据约束启用时 40/40 格阻断生成；关闭约束时 26/40 格触发生成。此消融只确认路径开关生效。",
        ]),
        ("M3 · 真实模型试跑", [
            "12 案例 × A/B/C 共 36 格，29 次 Provider 请求，零重试，另 7 格未生成。应用终态完成 26/36、失败 10/36；评测完整生成 3/29、截断失败 26/29、无生成不适用 7/36。",
            "Provider finish_reason=length 按失败处理，即使应用终态为 completed。一次服务端用量报告 513 个 completion tokens，高于配置 512；保留该异常且未重试。",
        ]),
        ("局限与下一步", [
            "完成教师盲评、引用语义支持与答案泄露标注，校准 BKT 参数，修复生成截断并重新冻结协议后复测，再讨论真实学生试点设计。",
            "所有结果均来自软件用例、自动测试或单次小规模真实模型试跑。没有教师评分一致性数据，也没有真实学生学习效果证据。",
        ]),
    ]
    for title, paragraphs in sections:
        if paragraphs:
            story.append(KeepTogether([para(title, styles["h1"]), para(paragraphs[0], styles["body"]) ]))
            for text in paragraphs[1:]:
                story.append(para(text, styles["body"]))
        else:
            story.append(para(title, styles["h1"]))
    chart_info = [
        ("F01", "首批题库", "F01-question-bank.png", "N=30 道题；单位为题目", "figure-data.csv 与 experiment-snapshot.json", "描述题目及评分状态，不说明学习效果。"),
        ("F02", "M1 A/B 运行格", "F02-ab-acceptance.png", "40 个构造案例 × A/B；计划 N=80 格", "figure-data.csv 与 acceptance-audit.json", "4 个 B 组失败保留；无真人参与者。"),
        ("F03", "教材页码核对", "F03-textbook-page-audit.png", "N=347 个 PDF 页；人工核验 7 页", "figure-data.csv 与 experiment-snapshot.json", "340 页仍待人工核验。"),
        ("F04", "构造案例类型", "F04-constructed-case-suite.png", "N=40 个构造案例，8 类各 5 例", "figure-data.csv 与 dev case version", "开发分支测试样例，不是真人观察。"),
        ("F05", "A/B 运行性能", "F05-ab-runtime-overview.png", "76/80 格；脚本化回合延迟/Token", "turn-performance.csv 与 experiment-snapshot.json", "仅为工程运行测量；历史自动 PDF 扫描保留 needs_review。"),
        ("F06", "M2 自动验收", "F06-m2-validation.png", "309 Python、82 M2 定向、5 CLI 测试项", "m2-validation.csv 与 M2-offline-acceptance.md", "定向 82 项包含在 Python 回归中，不相加。"),
        ("F07", "真实试跑前本地预检", "F07-m3-live-pilot-preflight.png", "12 案例 × A/B/C，共 36 个计划格", "m3-live-pilot-preflight.csv 与历史 JSON", "历史预检为零 Provider 调用；不代表实际试跑。"),
        ("F08", "M3 真实模型试跑", "F08-m3-live-pilot-results.png", "36 格、29 次请求；单位为格和请求", "m3-live-pilot-live-cells.csv 与聚合 JSON", "3 次完整、26 次截断、7 格未触发生成。"),
        ("F09", "M3 离线矩阵与预测", "F09-m3-offline-results.png", "120 主矩阵 + 120 回放 + 160 消融；BKT n=120", "F09-m3-offline-summary.csv 与 M3-offline-summary-redacted.json", "Fake Provider 和合成 Attempt；BKT 未校准。"),
    ]
    for index, item in enumerate(chart_info):
        plot_page(story, *item, styles, page_break=index > 0)
    doc.build(story, onFirstPage=draw_footer, onLaterPages=draw_footer)


def make_bundle(e: dict[str, Any], manifest: dict[str, Any]) -> None:
    offline_safe = safe_offline(e["offline"], e["offline_manifest"])
    offline_safe.update(e["offline_provenance"])
    payloads: dict[str, bytes] = {
        "README.md": (
            "DeepProf v0.6.2 M1–M3 实验附件。所有数值来自报告引用的固定历史运行；"
            "F07 保留为零调用预检，F08 为真实模型试跑，F09 为 Fake Provider 离线实验。"
            "公开内容只含图表、脱敏聚合、脱敏逐格状态和重绘脚本。"
            "不含教材、标准答案、原始模型输出、提示正文、检索片段、凭据或用户本机绝对路径。\n"
        ).encode("utf-8"),
        "report/M1-M3-实验报告.md": REPORT_MD.read_bytes(),
        "report/M1-M3-experimental-report.pdf": REPORT_PDF.read_bytes(),
        "manifest/M1-M3-evidence-manifest.json": json_bytes(manifest),
        "data/M1-M3-evidence-fields.md": FIELDS.read_bytes(),
        "data/plot-runtime.txt": (SOURCE / "plot-runtime.txt").read_bytes(),
        "data/M1-failure-summary.csv": make_m1_failure_csv(e["m1"]).encode("utf-8"),
        "data/figure-data.csv": (SOURCE / "figure-data.csv").read_bytes(),
        "data/turn-performance.csv": (SOURCE / "turn-performance.csv").read_bytes(),
        "data/m2-validation.csv": (SOURCE / "m2-validation.csv").read_bytes(),
        "data/F09-m3-offline-summary.csv": (SOURCE / "F09-m3-offline-summary.csv").read_bytes(),
        "data/m3-live-pilot-live-cells.csv": LIVE_CSV.read_bytes(),
        "data/M2-offline-acceptance-redacted.json": json_bytes(safe_m2(e["m2"])),
        "data/v0.6.2-release-validation.json": json_bytes(e["release_validation"]),
        "data/v0.6.2-m2-revalidation-summary.json": json_bytes(e["m2_release"]),
        "data/v0.6.2-m3-offline-summary.json": json_bytes(e["m3_release"]),
        "data/M3-offline-summary-redacted.json": json_bytes(offline_safe),
        "data/M3-live-pilot-summary-redacted.json": json_bytes(safe_live(e["live"])),
        "data/F06-qa.json": (SOURCE / "F06-qa.json").read_bytes(),
        "data/F09-qa.json": (SOURCE / "F09-qa.json").read_bytes(),
    }
    scripts = [
        "generate_charts.R", "generate_m2_charts.R", "generate_m3_live_pilot.R",
        "generate_m3_live_pilot_live.R", "generate_m3_offline_results.R",
    ]
    for path in scripts:
        payloads[f"scripts/{path}"] = (DOCS / path).read_bytes()
    payloads["scripts/build_m1_m3_report.py"] = Path(__file__).read_bytes()
    payloads["scripts/export_release_acceptance_summaries.py"] = (
        ROOT / "scripts" / "export_release_acceptance_summaries.py").read_bytes()
    figure_map = [
        "F01-question-bank", "F02-ab-acceptance", "F03-textbook-page-audit",
        "F04-constructed-case-suite", "F05-ab-runtime-overview", "F06-m2-validation",
        "F07-m3-live-pilot-preflight", "F08-m3-live-pilot-results", "F09-m3-offline-results",
    ]
    for stem in figure_map:
        for suffix in ("png", "svg", "pdf", "tiff"):
            path = FIGURES / f"{stem}.{suffix}"
            if not path.is_file():
                raise FileNotFoundError(f"Missing figure asset {path.name}")
            payloads[f"figures/{path.name}"] = path.read_bytes()

    checksum = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(payloads.items())
    ).encode("utf-8")
    payloads["SHA256SUMS.txt"] = checksum
    BUNDLE.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(BUNDLE, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as archive:
        for name, data in sorted(payloads.items()):
            archive.writestr(name, data)
    with zipfile.ZipFile(BUNDLE) as archive:
        for name in archive.namelist():
            if name.startswith("/") or ".." in Path(name).parts:
                raise ValueError(f"Unsafe archive path: {name}")
            if name.endswith((".md", ".csv", ".json", ".R", ".txt", ".py")):
                text = archive.read(name).decode("utf-8", errors="replace")
                text = re.sub("https?://[^ ]+", "", text)
                path_pattern = re.compile(
                    r"(?:[A-Za-z]:[\\/]|"
                    + re.escape("/" + "Users" + "/")
                    + "|"
                    + re.escape("/" + "home" + "/")
                    + r"[^/]+/)"
                )
                if path_pattern.search(text):
                    raise ValueError(f"Absolute local path found in public attachment: {name}")


def main() -> None:
    e = load_evidence()
    verify(e)
    manifest = evidence_manifest(e)
    write_json(MANIFEST, manifest)
    FIELDS.write_text(
        "# M1–M3 公开证据字段说明\n\n"
        "- run_id / 时间：固定运行批次标识；同一 run 内的分母不得与其他历史批次拼接。\n"
        "- sample_type：全部标为构造开发 fixture；human_subjects=false 表示无真人参与者。\n"
        "- planned / observed / completed：计划格、存在记录的格和正常完成格，三者含义不同。\n"
        "- M2 passed/total：固定测试项数；82 项定向验收包含于 309 项 Python 全量回归。\n"
        "- provider_requests / finish_reason：真实模型请求次数和终止类型；length 记为截断失败。\n"
        "- application terminal：软件终态；不等价于评测完整生成或答案正确。\n"
        "- locatable references：引用定位字段是否完整；不表示语义支持度。\n"
        "- AUC / Brier / log loss / ECE：仅针对 120 条构造预测；较差指标如实保留，不代表学生预测效能。\n"
        "- code_fingerprint：优先采用运行清单中保存的 commit 与工作树哈希；M1 导出记录缺少可核验的哈希值。\n"
        "- F07 为历史预检，F08 为真实 Provider 试跑，F09 为 Fake Provider 离线试跑。\n"
        "- v0.6.2 发布核验是独立的软件回归批次，不替代或覆盖历史 M1–M3 指标。\n"
        "- v0.6.2 M2/M3 发布复核 JSON 由脱敏导出脚本生成；仅包含聚合值、测试文件名和源记录哈希。\n"
        "- 不在公开包内提供教材、答案、模型输出正文、提示正文、检索片段、密钥或绝对本机路径。\n",
        encoding="utf-8")
    md = markdown_report(e, manifest)
    REPORT_MD.write_text(md, encoding="utf-8")
    build_pdf(e, manifest)
    make_bundle(e, manifest)
    sum_path = DOCS / "releases" / "M1-M3-SHA256SUMS.txt"
    sum_files = [REPORT_MD, REPORT_PDF, BUNDLE, MANIFEST, FIELDS]
    sum_files += [FIGURES / f"{name}.{ext}"
                  for name in (
                      "F01-question-bank", "F02-ab-acceptance", "F03-textbook-page-audit",
                      "F04-constructed-case-suite", "F05-ab-runtime-overview", "F06-m2-validation",
                      "F07-m3-live-pilot-preflight", "F08-m3-live-pilot-results",
                      "F09-m3-offline-results")
                  for ext in ("png", "svg", "pdf", "tiff")]
    sum_path.write_text("".join(f"{digest(path)}  {path.relative_to(ROOT).as_posix()}\n"
                                for path in sorted(sum_files, key=lambda item: item.as_posix())),
                        encoding="utf-8")
    print(f"Markdown: {REPORT_MD}")
    print(f"PDF: {REPORT_PDF}")
    print(f"Evidence ZIP: {BUNDLE}")
    print(f"SHA-256 manifest: {sum_path}")


if __name__ == "__main__":
    main()
