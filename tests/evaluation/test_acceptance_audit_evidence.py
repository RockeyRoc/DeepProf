from evaluation.audit_acceptance_run import _evidence_check_failures, _safe_failure_reason


def event(kind, trace_id="trace-1", **payload):
    return {"type": kind, "trace_id": trace_id, "payload": payload}


def test_explicit_non_generative_evidence_gaps_pass_without_citations():
    events = [
        event("teaching.decision", action="evidence_gap", reason_codes=["insufficient_evidence"]),
        event("pedagogy.decision", action="reflect", evidence_sufficient=False, reason_codes=["reflect"]),
        event("pedagogy.decision", action="reflect", evidence_sufficient=False,
              reason_codes=["insufficient_evidence"]),
    ]

    assert _evidence_check_failures(events) == (3, 0)


def test_evidence_gap_fails_if_the_same_trace_calls_the_model():
    events = [
        event("pedagogy.decision", action="reflect", evidence_sufficient=False,
              reason_codes=["insufficient_evidence"]),
        event("model.requested"),
    ]

    assert _evidence_check_failures(events) == (1, 1)


def test_sufficient_evidence_requires_a_complete_locator():
    events = [
        event("pedagogy.decision", action="teach", evidence_sufficient=True,
              evidence_refs=[{"document_id": "doc", "chunk_id": "chunk", "page": 4}]),
        event("pedagogy.decision", action="teach", evidence_sufficient=True, evidence_refs=[]),
    ]

    assert _evidence_check_failures(events) == (2, 1)


def test_failure_reason_exports_only_fixed_safe_codes():
    assert _safe_failure_reason({"status": "failed"}, [event("agent.failed", error={
        "code": "runtime_error", "message": "empty_model_response",
    })]) == "empty_model_response"
    assert _safe_failure_reason({"status": "failed", "failure": {"reason_code": "runtime_error"}}, [
        event("agent.failed", error={"message": "sk-live-secret"}),
    ]) == "runtime_error"
