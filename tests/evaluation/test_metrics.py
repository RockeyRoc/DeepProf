from evaluation.metrics import aggregate_events


def event(sequence, kind, timestamp, trace="t1", payload=None, surface="cli"):
    return {
        "sequence": sequence,
        "type": kind,
        "timestamp": timestamp,
        "trace_id": trace,
        "session_id": "s1",
        "surface": surface,
        "payload": payload or {},
    }


def test_metrics_pair_ttft_latency_usage_and_cost():
    result = aggregate_events(
        [
            event(1, "agent.started", "2026-01-01T00:00:00+00:00"),
            event(2, "model.requested", "2026-01-01T00:00:00.050000+00:00", payload={"provider_profile": "p", "model": "m"}),
            event(3, "model.stream.delta", "2026-01-01T00:00:00.120000+00:00", payload={"text": "a"}),
            event(4, "model.completed", "2026-01-01T00:00:00.500000+00:00", payload={"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}),
            event(5, "agent.turn.completed", "2026-01-01T00:00:00.600000+00:00", payload={"status": "ok"}),
        ],
        {"profiles": {"p": {"models": {"m": {"input_per_million": 1, "output_per_million": 2}}}}},
    )
    row = result["traces"][0]
    assert row["ttft_ms"] == 70
    assert row["provider_latency_ms"] == 450
    assert row["e2e_latency_ms"] == 600
    assert row["total_tokens"] == 15
    assert row["estimated_cost"] == 0.00002


def test_metrics_leave_unknown_cost_and_record_failure():
    result = aggregate_events(
        [
            event(1, "model.requested", "2026-01-01T00:00:00+00:00", payload={"provider_profile": "p", "model": "m"}),
            event(2, "agent.failed", "2026-01-01T00:00:01+00:00", payload={"error": {"code": "provider_error", "details": {"kind": "rate_limited"}}}),
        ]
    )
    assert result["traces"][0]["estimated_cost"] is None
    assert result["traces"][0]["status"] == "failed"
    assert result["failures"][0]["kind"] == "rate_limited"


def test_metrics_keeps_multiple_model_calls_in_one_trace():
    result = aggregate_events(
        [
            event(1, "model.requested", "2026-01-01T00:00:00+00:00", payload={"provider_profile": "p1", "model": "m1"}),
            event(2, "model.stream.delta", "2026-01-01T00:00:00.100000+00:00"),
            event(3, "model.completed", "2026-01-01T00:00:00.200000+00:00", payload={"usage": {"total_tokens": 3}}),
            event(4, "model.requested", "2026-01-01T00:00:00.300000+00:00", payload={"provider_profile": "p2", "model": "m2"}),
            event(5, "model.stream.delta", "2026-01-01T00:00:00.450000+00:00"),
            event(6, "model.completed", "2026-01-01T00:00:00.500000+00:00", payload={"usage": {"total_tokens": 4}}),
        ]
    )
    assert [(row["call_index"], row["provider_profile"]) for row in result["traces"]] == [(0, "p1"), (1, "p2")]
    assert [row["ttft_ms"] for row in result["traces"]] == [100, 150]
