from scripts.build_m3_abc_experiment_report import _rag_application_completed


def test_rag_application_completion_reads_current_aggregate_metric_schema():
    assert _rag_application_completed({
        "metrics": {"application_terminal_completion": {"numerator": 40, "denominator": 40}},
    }) == 40


def test_rag_application_completion_keeps_legacy_summary_compatibility():
    assert _rag_application_completed({"application_terminal": {"completed": 39}}) == 39
