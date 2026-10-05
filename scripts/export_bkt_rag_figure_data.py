"""Export public-safe, reviewable CSV sources for the M3 experiment figures."""
from __future__ import annotations
import csv, hashlib, json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "docs/experiments/bkt-rag-improvement-20261001"
DATA = OUT / "data"
OLD = ROOT / "docs/experiments/bkt-rag-improvement-20260929"
PRIVATE = ROOT.parent / "开发者测试.deepprof/experiments/m3-abc-research/m3-abc-bkt-20260928T134542Z-a216df/bkt-rag-improvement-20260929/citation-audit-v2"
REPAIR = ROOT / "docs/experiments/m3-abc-research/m3-abc-ragfix-20261001T120000Z-d1a9f4"


def load(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def write_csv(path: Path, rows: list[dict[str, Any]], columns: list[str] | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if columns is None:
        columns = list(rows[0]) if rows else []
    with path.open("w", encoding="utf-8-sig", newline="") as stream:
        writer = csv.DictWriter(stream, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(rows)


def sha(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def metric_rows(blob: dict[str, Any], scope: str) -> list[dict[str, Any]]:
    rows = []
    for method, metric in blob.items():
        if not isinstance(metric, dict) or "auc" not in metric:
            continue
        rows.append({"scope": scope, "method": method, "n": metric.get("n"),
            "positive": metric.get("positive"), "negative": metric.get("negative"),
            "auc": metric.get("auc"), "brier": metric.get("brier"),
            "log_loss": metric.get("log_loss"), "ece_10_bins": metric.get("ece_10_bins")})
    return rows


def main() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    constructed = load(OLD / "constructed-bkt-strata.json")
    public = load(OLD / "public-bkt-strata.json")
    nested = load(DATA / "assistments-nested-selection-100iter.json")
    citation = load(OLD / "citation-audit-summary.json")
    citation_secondary = load(OLD / "citation-audit-ai-review-summary.json")
    relevance_manifest = load(OLD / "rag-relevance-worksheet-manifest.json")
    annotation_manifest = load(ROOT.parent / "开发者测试.deepprof/experiments/m3-abc-research/bkt-rag-improvement-20261001/human-annotation/package-manifest.json")
    repair_state = load(REPAIR / "repair-state.json")
    nomiracl_path = DATA / "nomiracl-zh-evaluation.json"
    course_rag_path = DATA / "course-rag-offline-summary.json"
    nomiracl = load(nomiracl_path) if nomiracl_path.exists() else None
    course_rag = load(course_rag_path) if course_rag_path.exists() else None
    fulltext_path=DATA/'fulltext-rag-summary.json'
    fulltext=load(fulltext_path) if fulltext_path.exists() else None

    coverage = [
        {"domain": "构造集（编程诊断）", "unit": "序列", "count": constructed["n_sequences"], "split": "构造集", "status": "available"},
        {"domain": "构造集（编程诊断）", "unit": "作答", "count": constructed["n_attempts"], "split": "构造集", "status": "available"},
        {"domain": "ASSISTments 2009–2010（数学）", "unit": "训练学生", "count": public["partition_counts"]["development_learners"], "split": "development", "status": "historical_public_data"},
        {"domain": "ASSISTments 2009–2010（数学）", "unit": "训练作答", "count": public["partition_counts"]["development_attempts"], "split": "development", "status": "historical_public_data"},
        {"domain": "ASSISTments 2009–2010（数学）", "unit": "测试学生", "count": public["partition_counts"]["locked_test_learners"], "split": "locked_test", "status": "historical_test_viewed"},
        {"domain": "ASSISTments 2009–2010（数学）", "unit": "测试作答", "count": public["partition_counts"]["locked_test_attempts"], "split": "locked_test", "status": "historical_test_viewed"},
        {"domain": "Algebra I 2005–2006 KDD", "unit": "官方步骤数", "count": 813661, "split": "registered download required", "status": "pending_full_master_access"},
        {"domain": "Algebra I 2006–2007 KDD", "unit": "官方步骤数", "count": 2289726, "split": "registered download required", "status": "pending_full_master_access"},
        {"domain": "课程 RAG 旧题", "unit": "问题", "count": relevance_manifest["questions"], "split": "old benchmark", "status": "relevance_labels_pending"},
        {"domain": "课程 RAG 新题", "unit": "问题", "count": annotation_manifest["counts"]["new_questions"], "split": "development+sealed_test", "status": "draft_gold_pending_human"},
        {"domain": "NoMIRACL 中文", "unit": "测试查询", "count": nomiracl["counts"]["test_queries"] if nomiracl else "", "split": "official test; fixed candidates", "status": "candidate_conditional_eval_complete" if nomiracl else "not_run"},
        {"domain": "NoMIRACL 中文", "unit": "匹配候选文档", "count": nomiracl["counts"]["candidate_documents_unique"] if nomiracl else "", "split": "official dev/test; judged candidates", "status": "candidate_qrel_missingness_reported" if nomiracl else "not_run"},
        {"domain": "MIRACL 中文全库", "unit": "语料检索", "count": "", "split": "full corpus", "status": "not_run"},
    ]
    if fulltext:
        for row in coverage:
            if row['domain'].startswith('课程 RAG'):row['status']='ai_reviewed_personnel_pending'
        coverage.extend([
            {'domain':'教材 OCR','unit':'PDF页','count':fulltext['ocr_coverage']['recognized_pages'],'split':'full textbook','status':'ai_reviewed_personnel_pending'},
            {'domain':'全文 RAG 四条件','unit':'有效格','count':fulltext['formal_eligible_cells'],'split':'120 queries x 4 conditions','status':'available'}])
    if nomiracl:
        coverage.extend([
            {'domain':'NoMIRACL 中文','unit':'开发查询','count':nomiracl['counts']['dev_queries'],'split':'official dev; threshold selection','status':'candidate_conditional_eval_complete'},
            {'domain':'NoMIRACL 中文','unit':'候选配对','count':nomiracl['counts']['candidate_pairs'],'split':'complete dev/test pairs','status':'candidate_conditional_eval_complete'}])
    write_csv(DATA / "coverage.csv", coverage)

    strata_rows = []
    axes = {"full_sequence_length": public["locked_test"]["by_full_sequence_length"],
            "prior_attempts": public["locked_test"]["by_prior_attempts"],
            "training_concept_coverage": public["locked_test"]["by_training_concept_coverage"]}
    axis_order = {"full_sequence_length": ["1-3", "4-10", "11-20", "21+"],
                  "prior_attempts": ["0-2", "3-5", "6-9", "10+"],
                  "training_concept_coverage": ["0", "1-39", "40-199", "200+"]}
    for axis, bins in axes.items():
        for bin_name in axis_order[axis]:
            record = bins.get(bin_name)
            if not record:
                continue
            for method, metric in record.items():
                if isinstance(metric, dict) and "auc" in metric:
                    auc_delta = record.get("auc_delta_vs_global_constant", {}).get(method)
                    auc_delta_concept = record.get("auc_delta_vs_concept_constant", {}).get(method)
                    learner_coverage = record.get("training_learners_per_concept", {})
                    # The source stores paired student-cluster intervals for the
                    # hierarchical model at the stratum level, not a per-method map.
                    # Keep other methods' intervals blank rather than inventing CIs.
                    auc_ci_global = record.get("auc_delta_ci_vs_global_constant", {}) if method == "hierarchical_shrinkage" else {}
                    auc_ci_concept = record.get("auc_delta_ci_vs_concept_constant", {}) if method == "hierarchical_shrinkage" else {}
                    ll_ci_global = record.get("log_loss_improvement_ci_vs_global_constant", {}) if method == "hierarchical_shrinkage" else {}
                    ll_ci_concept = record.get("log_loss_improvement_ci_vs_concept_constant", {}) if method == "hierarchical_shrinkage" else {}
                    strata_rows.append({"axis": axis, "stratum": bin_name, "method": method,
                        "n": metric.get("n"), "positive": metric.get("positive"), "negative": metric.get("negative"),
                        "auc": metric.get("auc"), "delta_auc_vs_global_constant": auc_delta,
                        "delta_auc_vs_concept_constant": auc_delta_concept,
                        "delta_auc_global_ci_lower": auc_ci_global.get("lower_95"),
                        "delta_auc_global_ci_upper": auc_ci_global.get("upper_95"),
                        "delta_auc_global_evidence": auc_ci_global.get("evidence_status"),
                        "delta_auc_concept_ci_lower": auc_ci_concept.get("lower_95"),
                        "delta_auc_concept_ci_upper": auc_ci_concept.get("upper_95"),
                        "delta_auc_concept_evidence": auc_ci_concept.get("evidence_status"),
                        "log_loss_gain_global_ci_lower": ll_ci_global.get("lower_95"),
                        "log_loss_gain_global_ci_upper": ll_ci_global.get("upper_95"),
                        "log_loss_gain_global_evidence": ll_ci_global.get("evidence_status"),
                        "log_loss_gain_concept_ci_lower": ll_ci_concept.get("lower_95"),
                        "log_loss_gain_concept_ci_upper": ll_ci_concept.get("upper_95"),
                        "log_loss_gain_concept_evidence": ll_ci_concept.get("evidence_status"),
                        "unique_concepts": record.get("unique_concepts"),
                        "train_students_per_concept_min": learner_coverage.get("minimum"),
                        "train_students_per_concept_mean": learner_coverage.get("mean"),
                        "train_students_per_concept_max": learner_coverage.get("maximum"),
                        "log_loss": metric.get("log_loss"), "brier": metric.get("brier"),
                        "ece_10_bins": metric.get("ece_10_bins")})
    write_csv(DATA / "public-bkt-strata.csv", strata_rows)

    nested_metrics = metric_rows(nested["nested_development_oof"], "ASSISTments nested development OOF")
    write_csv(DATA / "nested-bkt-methods.csv", nested_metrics)
    em_rows = []
    for fold in nested["outer_fold_selection"]:
        fit = fold.get("item_difficulty", {})
        convergence = fit.get("converged_by_concept", {})
        em_rows.append({"outer_fold": fold["outer_fold"], "selected_shrinkage": fold.get("selected_shrinkage"),
            "selected_forgetting": fold.get("selected_opportunity_forgetting"),
            "em_max_iterations": fit.get("max_em_iterations"), "em_iterations_used": fit.get("iterations_used"),
            "em_converged": fit.get("converged"), "eligible_concept_item_pairs": fit.get("eligible_concept_item_pairs"),
            "converged_concepts": sum(bool(v) for v in convergence.values()), "fit_concepts": len(convergence)})
    write_csv(DATA / "item-em-convergence.csv", em_rows)

    citation_rows = []
    for name, obj, value_name in [
        ("字段可解析率", citation["field_parse_rate"], "540 fields"),
        ("来源实际可定位率", citation["source_actual_locatable_rate"], "answer-visible citation occurrences"),
        ("语义完整支持率", citation["semantic_support_rate"], "claim-citation pairs"),
        ("事实主张证据覆盖率", citation["claim_evidence_coverage"], "factual-claim candidates")]:
        citation_rows.append({"metric": name, "numerator": obj.get("numerator", obj.get("fully_supported_claims")),
            "denominator": obj.get("denominator", obj.get("factual_claim_candidates")), "rate": obj.get("rate"),
            "unit": value_name, "review_source": "AI-assisted, uncalibrated; not human rating"})
    write_csv(DATA / "citation-rates.csv", citation_rows)
    pair_count = 0
    try:
        with (PRIVATE / "review-worksheet-ai-reviewed.csv").open(encoding="utf-8-sig", newline="") as stream:
            pair_count = sum(1 for row in csv.DictReader(stream) if row.get("record_type") == "claim_citation_pair")
    except FileNotFoundError:
        pass
    reconciliation = {
        "primary_summary": "citation-audit-summary.json",
        "primary_reason": "its 119 semantic pairs and judgment counts match the raw AI-reviewed worksheet's 119 claim_citation_pair rows",
        "raw_pair_rows": pair_count,
        "primary_semantic": citation["semantic_support_rate"],
        "secondary_summary": "citation-audit-ai-review-summary.json",
        "secondary_semantic": citation_secondary["semantic_support_rate"],
        "secondary_claim_coverage": citation_secondary["claim_evidence_coverage"],
        "status": "conflicting_legacy_summary_preserved_but_not_combined",
        "note": "The secondary summary reports 67 semantic pairs and 252 claim candidates, which do not match the 119 pair rows and 261 claim candidates in the raw worksheet / primary summary. The discrepancy remains explicit for manual audit."
    }
    (DATA / "citation-summary-reconciliation.json").write_text(json.dumps(reconciliation, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    conditions = ["retrieval-on_constraint-on", "retrieval-on_constraint-off",
                  "retrieval-off_constraint-on", "retrieval-off_constraint-off"]
    ablation = [{"condition": condition, "planned_cells": 40,
                 "planned_generation_requests": 0 if condition == "retrieval-off_constraint-on" else 40,
                 "formal_status": "not_run_prompt_fingerprint_guard_stopped_batch",
                 "preflight_requests": repair_state.get("preflight_generation_requests", 1),
                 "formal_cells_completed": repair_state.get("formal_cells_completed", 0),
                 "performance_metric": "N/A"} for condition in conditions]
    # The stopped matrix is retained in the archive; never regenerate its active placeholder.

    latency_rows: list[dict[str, Any]] = []
    try:
        with (PRIVATE / "rag-relevance-worksheet.csv").open(encoding="utf-8-sig", newline="") as stream:
            seen: set[str] = set()
            for row in csv.DictReader(stream):
                case = row.get("case_id", "")
                if case in seen:
                    continue
                seen.add(case)
                for method, key in [("Hash dense", "hash_dense_latency_ms"), ("BM25", "bm25_latency_ms"),
                                    ("RRF merge", "rrf_merge_latency_ms"), ("Total retrieval", "total_retrieval_latency_ms")]:
                    value = row.get(key, "")
                    latency_rows.append({"case_id": case, "method": method, "latency_ms": value,
                        "retrieval_labels_status": "pending_human_annotation", "pool_is_candidate_union_not_corpus_recall": True})
    except FileNotFoundError:
        pass
    write_csv(DATA / "rag-latency-per-query.csv", latency_rows)

    retrieval_rows: list[dict[str, Any]] = []
    if nomiracl:
        for key, value in nomiracl.get("ranking_metrics", {}).items():
            split, method = key.split(":", 1)
            retrieval_rows.append({"scope": "NoMIRACL Chinese fixed judged candidates", "split": split,
                "method": method, "metric": "mean_nDCG@5", "value": value.get("mean_nDCG_at_5"),
                "n": value.get("relevant_query_count"), "note": "candidate-conditional; not corpus-wide Recall"})
        gate = nomiracl.get("test_evidence_gate", {})
        for metric, value, n in [
            ("false_accept_rate", gate.get("false_accept_rate"), gate.get("non_relevant_queries")),
            ("false_rejection_rate", gate.get("false_rejection_rate"), gate.get("relevant_queries")),
            ("reranker_max_score_auc", gate.get("reranker_max_score_auc"), gate.get("relevant_queries", 0) + gate.get("non_relevant_queries", 0)),
        ]:
            retrieval_rows.append({"scope": "NoMIRACL Chinese fixed judged candidates", "split": "test",
                "method": "BGE reranker v2-m3 evidence gate", "metric": metric, "value": value, "n": n,
                "note": "threshold selected on dev with FAR <= 5%; fixed-candidate gate"})
    write_csv(DATA / "rag-retrieval-metrics.csv", retrieval_rows,
        ["scope", "split", "method", "metric", "value", "n", "note"])

    latency_summary: list[dict[str, Any]] = []
    if course_rag:
        for method, stats in course_rag.get("latency_ms_summary", {}).items():
            latency_summary.append({"scope": "course 40-query frozen candidate pool", "method": method,
                "median_ms": stats.get("median"), "p95_ms": stats.get("p95"), "n": course_rag.get("fixed_queries"),
                "note": "retrieval stage only; no generation; relevance labels pending"})
    if nomiracl:
        for method, total, mean_ms, n, note in [
            ("NoMIRACL four-method scoring, ranking and metrics", nomiracl["latency_seconds"].get("bge_m3_query_candidate_scoring"), "", nomiracl["counts"].get("queries_total"), "total wall seconds for all four-method ranking/metrics; not a median"),
            ("NoMIRACL BGE reranker scoring", nomiracl["latency_seconds"].get("bge_reranker_pair_scoring"), nomiracl["latency_milliseconds"].get("reranker_mean_per_candidate_pair"), nomiracl["counts"].get("candidate_pairs"), "separate total seconds and mean ms/pair; not a median"),
        ]:
            latency_summary.append({"scope": "NoMIRACL Chinese fixed judged candidates", "method": method,
                "median_ms": "", "p95_ms": "", "total_seconds": total, "mean_ms_per_pair": mean_ms, "n": n, "note": note})
    write_csv(DATA / "rag-latency-summary.csv", latency_summary,
              ["scope","method","median_ms","p95_ms","total_seconds","mean_ms_per_pair","n","note"])

    provenance = {
        "created_by": "scripts/export_bkt_rag_figure_data.py",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "sources": {}, "outputs": {}
    }
    sources = [OLD / "constructed-bkt-strata.json", OLD / "public-bkt-strata.json", OLD / "citation-audit-summary.json",
               OLD / "citation-audit-ai-review-summary.json", OLD / "rag-relevance-worksheet-manifest.json",
               DATA / "assistments-nested-selection-100iter.json", REPAIR / "repair-state.json",
               ROOT.parent / "开发者测试.deepprof/experiments/m3-abc-research/bkt-rag-improvement-20261001/human-annotation/package-manifest.json",
               ROOT / "scripts/export_bkt_rag_figure_data.py", ROOT / "scripts/build_bkt_rag_experiment_report.py",
               ROOT / "scripts/build_bkt_rag_delivery_manifest.py",
               OUT / "figures/make_figures.R", OUT / "tools/rag-requirements.lock", OUT / "tools/models/model-snapshots.json",
               OUT / "validation/validation-summary.json"]
    if nomiracl_path.exists(): sources.append(nomiracl_path)
    if course_rag_path.exists(): sources.append(course_rag_path)
    for path in sources:
        provenance["sources"][str(path.relative_to(ROOT) if path.is_relative_to(ROOT) else path)] = sha(path)
    for path in sorted(DATA.glob("*.csv")):
        provenance["outputs"][path.name] = {"sha256": sha(path), "bytes": path.stat().st_size}
    for path in sorted(DATA.glob("*.json")):
        if path.name != "figure-data-manifest.json":
            provenance["outputs"][path.name] = {"sha256": sha(path), "bytes": path.stat().st_size}
    for path in sorted((OUT / "figures").glob("figure-*")):
        if path.is_file():
            provenance["outputs"][f"figures/{path.name}"] = {"sha256": sha(path), "bytes": path.stat().st_size}
    (DATA / "figure-data-manifest.json").write_text(json.dumps(provenance, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"csv_files": list(provenance["outputs"]), "public_strata_rows": len(strata_rows),
        "nested_metrics": len(nested_metrics), "citation_metrics": len(citation_rows), "latency_rows": len(latency_rows)}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
