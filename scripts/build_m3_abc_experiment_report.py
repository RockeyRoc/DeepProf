"""Build the privacy-filtered M3 A/B/C and BKT campaign report."""

from __future__ import annotations

import json
import argparse
from pathlib import Path
from typing import Any

from scripts.export_m3_live_pilot_aggregate import export_run
from models.learner.bkt import INITIAL_PARAMETERS


def _read(path: Path | None) -> dict[str, Any] | None:
    return json.loads(path.read_text(encoding="utf-8")) if path and path.is_file() else None


def _rag_application_completed(summary: dict[str, Any]) -> int:
    metric = (summary.get("metrics") or {}).get("application_terminal_completion") or {}
    if metric.get("numerator") is not None:
        return int(metric["numerator"])
    return int(((summary.get("application_terminal") or {}).get("completed") or 0))


def export_run_bundle(run_dir: Path, output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    return export_run(run_dir, output_dir / "cells.csv", output_dir / "summary.json")


def build_report(campaign_dir: Path, *, campaign_id: str, preflight: dict[str, Any] | None,
                 main: dict[str, Any] | None, calibration: dict[str, Any],
                 selected_parameters: dict[str, Any], promotion_applied: bool,
                 stopped_reason: str = "", rag: dict[str, dict[str, Any]] | None = None) -> Path:
    old = calibration["out_of_fold"]["old_parameters"]
    fitted = calibration["out_of_fold"]["fitted_parameters"]
    baseline = calibration["out_of_fold"]["constant_baseline"]
    improvement = calibration["out_of_fold"]["log_loss_improvement_vs_old"]
    log_loss_baseline = calibration["out_of_fold"]["log_loss_improvement_vs_baseline"]
    brier_old = calibration["out_of_fold"]["brier_change_vs_old"]
    brier_baseline = calibration["out_of_fold"]["brier_change_vs_baseline"]
    stability = calibration.get("parameter_stability") or {}

    lines = [
        "# M3 A/B/C 真实模型实验与 BKT 开发校准",
        "",
        f"实验批次：`{campaign_id}`。所有案例和历史作答均为构造开发数据；无真实学生参与、无教师评分。",
        "",
        "## BKT 五折留概念评测",
        "",
        "每折按概念分组，整段作答序列只出现在训练集或留出集其中一侧。三种历史模板限制了这些指标的解释范围。",
        "",
        "| 折外模型 | n | AUC | Brier | Log loss | ECE（10 箱） |",
        "|---|---:|---:|---:|---:|---:|",
        f"| 原始参数 | {old['n']} | {old['auc']:.4f} | {old['brier']:.4f} | {old['log_loss']:.4f} | {old['ece_10_bins']:.4f} |",
        f"| 拟合参数 | {fitted['n']} | {fitted['auc']:.4f} | {fitted['brier']:.4f} | {fitted['log_loss']:.4f} | {fitted['ece_10_bins']:.4f} |",
        f"| Laplace 常数基线 | {baseline['n']} | {baseline['auc']:.4f} | {baseline['brier']:.4f} | {baseline['log_loss']:.4f} | {baseline['ece_10_bins']:.4f} |",
        "",
        "折分与逐条折外预测见 [BKT 五折 CSV](bkt-calibration/bkt-folds.csv) 和 [BKT 预测 CSV](bkt-calibration/bkt-predictions.csv)。",
        "",
        "| 参数集 | P(L0) | P(T) | P(G) | P(S) | 版本 | 来源 |",
        "|---|---:|---:|---:|---:|---|---|",
        f"| 原始参数 | {INITIAL_PARAMETERS.p_l0:.2f} | {INITIAL_PARAMETERS.p_t:.2f} | {INITIAL_PARAMETERS.p_g:.2f} | {INITIAL_PARAMETERS.p_s:.2f} | `{INITIAL_PARAMETERS.model_version}` | 未拟合开发初值 |",
        f"| 全量开发拟合 | {calibration['full_data_fit']['parameters']['p_l0']:.2f} | {calibration['full_data_fit']['parameters']['p_t']:.2f} | {calibration['full_data_fit']['parameters']['p_g']:.2f} | {calibration['full_data_fit']['parameters']['p_s']:.2f} | `{calibration['full_data_fit']['parameters']['model_version']}` | 构造案例 SHA-256 `{calibration['source_data']['sha256']}` |",
        f"| 实验使用参数 | {selected_parameters['p_l0']:.2f} | {selected_parameters['p_t']:.2f} | {selected_parameters['p_g']:.2f} | {selected_parameters['p_s']:.2f} | `{selected_parameters['model_version']}` | {selected_parameters['source']} |",
        "",
        "| 参数 | 五折最小值 | 五折最大值 | 范围 |",
        "|---|---:|---:|---:|",
        *[f"| {name} | {stability[name]['min']:.2f} | {stability[name]['max']:.2f} | {stability[name]['range']:.2f} |"
          for name in ("p_l0", "p_t", "p_g", "p_s")],
        "",
        f"Log loss 改善（旧参数 / 常数基线）：{improvement:.6f} / {log_loss_baseline:.6f}；Brier 变化（旧参数 / 常数基线）：{brier_old:+.6f} / {brier_baseline:+.6f}。",
        f"拟合 AUC 为 {fitted['auc']:.4f}（旧参数 {old['auc']:.4f}，常数基线 {baseline['auc']:.4f}），均低于随机参照 0.5；ECE 为 {fitted['ece_10_bins']:.4f}，高于常数基线 {baseline['ece_10_bins']:.4f}。拟合的 P(T) 与 P(G) 落在候选网格边界，参数仍受三种构造历史模板限制。",
        f"构造数据研究指标门槛：{'通过' if calibration['gates'].get('research_metrics_eligible') else '未通过'}；本课程默认参数允许升级：{'是' if calibration['gates'].get('curriculum_promotion_eligible') else '否（需本课程数据与教师审核）'}；实际已升级：{'是' if promotion_applied else '否'}。",
        "",
        "完整训练拟合指标单独记录在 `bkt-calibration/bkt-calibration.json` 的 `full_data_fit` 中，不作为留出表现。参数及数据来源见 `bkt-calibration/bkt-candidate-parameters.json`。",
        "",
        "## 真实 Provider 预检",
        "",
    ]
    if preflight:
        p_counts = preflight.get("evaluation_judgment", {}).get("cell_outcomes", {})
        p_limits = preflight.get("limits", {})
        lines.extend([
            f"预检批次 `{preflight.get('run_id')}`：{preflight.get('sample', {}).get('observed_cells', 0)}/12 格；实际 Provider 请求 {p_limits.get('actual_provider_requests', 0)} 次；完整 {p_counts.get('completed', 0)}、截断 {p_counts.get('failed_truncated', 0)}、其他失败 {p_counts.get('failed_provider', 0) + p_counts.get('failed_incomplete', 0) + p_counts.get('application_failed', 0)}、未调用 {p_counts.get('not_applicable', 0)}。",
            "",
            "| 组 | 格数 | 应用完成 | 应用失败 | 完整生成 | 截断 | Provider/其他失败 | 未调用 | 输入 tokens | 输出 tokens | 可定位引用 / 引用 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for group in ("A", "B", "C"):
            row = (preflight.get("groups") or {}).get(group) or {}
            lines.append(f"| {group} | {row.get('observed_cells', 0)} | {row.get('terminal_completed', 0)} | {row.get('terminal_failed', 0)} | {row.get('evaluation_completed', 0)} | {row.get('evaluation_truncated_failed', 0)} | {row.get('evaluation_failed_other', 0)} | {row.get('evaluation_not_applicable', 0)} | {row.get('prompt_tokens', 0)} | {row.get('completion_tokens', 0)} | {row.get('locatable_references', 0)} / {row.get('references', 0)} |")
        lines.extend([
            "",
            "逐格脱敏数据：[预检 CSV](preflight/cells.csv)；聚合数据：[预检 JSON](preflight/summary.json)。",
        ])
    else:
        lines.append("预检尚未运行。")
    lines.extend(["", "## 正式 A/B/C 矩阵", ""])
    if main:
        groups = main.get("groups") or {}
        lines.extend([
            f"正式批次 `{main.get('run_id')}`：{main.get('sample', {}).get('observed_cells', 0)}/120 格；实际 Provider 请求 {main.get('limits', {}).get('actual_provider_requests', 0)} 次；评测终态 `{main.get('evaluation_run_status')}`。",
            "",
            "| 组 | 格数 | 应用完成 | 应用失败 | 完整生成 | 截断 | 其他失败 | 未调用 | 输入 tokens | 输出 tokens | 可定位引用 / 引用 | BKT 参数哈希 |",
            "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ])
        for group in ("A", "B", "C"):
            row = groups.get(group) or {}
            lines.append(f"| {group} | {row.get('observed_cells', 0)} | {row.get('terminal_completed', 0)} | {row.get('terminal_failed', 0)} | {row.get('evaluation_completed', 0)} | {row.get('evaluation_truncated_failed', 0)} | {row.get('evaluation_failed_other', 0)} | {row.get('evaluation_not_applicable', 0)} | {row.get('prompt_tokens', 0)} | {row.get('completion_tokens', 0)} | {row.get('locatable_references', 0)} / {row.get('references', 0)} | `{selected_parameters.get('config_hash', '')}` |")
        lines.extend([
            "",
            "逐格脱敏数据：[正式矩阵 CSV](main/cells.csv)；聚合数据：[正式矩阵 JSON](main/summary.json)。",
            "",
            ("本机隔离原始运行目录的 `main/scoring/rater-01.json` 与 `rater-02.json` 是两份匿名盲评分件，隐藏组别和案例编号。评分维度分别记录准确性、清晰度、连贯性、学习投入、语言自然度、个性化相关性、教学动作适切性、答案泄露、引用支持与不确定性处理；未提交评分前所有人工结论均为待评，不以动作匹配或引用定位率代替教学质量。"
             if main.get("sample", {}).get("observed_cells") == 120 else
             "正式矩阵未达到 120 格，未生成盲评分件；教学质量人工评分保持待评。"),
        ])
    else:
        lines.append(f"正式矩阵未启动。停止原因：{stopped_reason or '预检未满足启动条件'}。")
    lines.extend(["", "## RAG 检索与证据约束消融", ""])
    if rag:
        lines.extend([
            "各条件固定在 C 组、40 个构造案例和同一模型配置。证据约束开启且无检索时应确定性停止；此条件不发模型请求。",
            "",
            "| 条件 | 运行格 | 应用完成 | 完整生成 | 截断/失败 | Provider 请求 |",
            "|---|---:|---:|---:|---:|---:|",
        ])
        for condition, summary in rag.items():
            outcomes = summary.get("evaluation_judgment", {}).get("cell_outcomes", {})
            application_completed = _rag_application_completed(summary)
            lines.append(f"| {condition} | {summary.get('sample', {}).get('observed_cells', 0)} | "
                f"{application_completed} | "
                f"{outcomes.get('completed', 0)} | "
                f"{outcomes.get('failed_truncated', 0) + outcomes.get('failed_provider', 0) + outcomes.get('failed_incomplete', 0)} | "
                f"{summary.get('limits', {}).get('actual_provider_requests', 0)} |")
        lines.extend(["", "脱敏格表与聚合汇总分别保存在 `rag-ablation/<condition>/cells.csv` 和 `summary.json`。"])
    else:
        lines.append("正式矩阵未通过生成、预算、会话隔离或 BKT 快照守卫时，不启动 RAG 消融；新增 Provider 调用停止。")
    lines.extend([
        "",
        "## MAIC 与 OpenMAIC 的借鉴边界",
        "",
        "本批次保留 A（直接生成）、B（教学图）和 C（教学图加 Attempt/BKT）定义，沿用“材料检索—教学决策—生成—评价”路径，将索引快照、策略绑定、提示来源、采样参数、会话与事件指纹分别记录。MAIC 论文提出材料 Read/Plan 流程和面向学习者背景的适配，并以准确性、清晰度、连贯性、学习投入、自然度和个性化相关性开展盲评；这里借用其材料组织和评价维度，不把构造案例结果外推为学生学习效果。OpenMAIC 的显式 Provider 配置、阶段运行记录与可恢复会话也用于完善本实验协议；本轮不扩展多智能体课堂。参见 [MAIC 论文](https://doi.org/10.1007/s11390-025-6000-0) 与 [OpenMAIC](https://github.com/THU-MAIC/OpenMAIC)。",
        "",
    ])
    lines.extend([
        "",
        "## 解释边界与原始证据",
        "",
        "A/B/C 是构造案例上的真实 Provider 工程运行；动作匹配只对开发构造标签，不是教师评分。引用定位只验证字段可解析，不验证语义支持。BKT 指标来自构造历史，不代表真实学习者预测能力或学习成效。",
        "",
        "完整提示、教材片段、模型回答、会话与学习者 ID、逐事件追踪保存在本机隔离运行目录；本目录只含聚合字段白名单。",
        "",
        "旧试点记录继续保存在[旧试点报告](../M3-live-pilot.md)，已在[旧 M1–M3 报告](../M1-M3-实验报告.md)记载的数据不并入本批次。",
        "",
    ])
    target = campaign_dir / "M3-A-B-C-真实模型与BKT校准报告.md"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("\n".join(lines), encoding="utf-8")
    return target


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("campaign_dir", type=Path)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--preflight", type=Path)
    parser.add_argument("--main", dest="main_summary", type=Path)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--selected-parameters", type=Path, required=True)
    parser.add_argument("--promotion-applied", action="store_true")
    parser.add_argument("--stopped-reason", default="")
    args = parser.parse_args()
    report_path = build_report(args.campaign_dir, campaign_id=args.campaign_id,
        preflight=_read(args.preflight), main=_read(args.main_summary),
        calibration=_read(args.calibration) or {},
        selected_parameters=_read(args.selected_parameters) or {},
        promotion_applied=args.promotion_applied, stopped_reason=args.stopped_reason)
    print(report_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
