from scripts.run_ab_acceptance import turn_observation


def test_turn_observation_counts_failed_model_requests_and_sums_safe_usage():
    observed = turn_observation([
        {"type": "model.requested", "payload": {"model": "fixture"}},
        {"type": "model.failed", "payload": {
            "error": {"code": "model_truncated", "kind": "model_truncated"},
            "usage": {"prompt_tokens": 31, "completion_tokens": 8, "total_tokens": 39},
        }},
        {"type": "pedagogy.decision", "payload": {"action": "teach"}},
    ])

    assert observed["model_calls"] == 1
    assert observed["usage"] == {"prompt_tokens": 31, "completion_tokens": 8, "total_tokens": 39}
    assert observed["actions"] == ["teach"]
    assert observed["model_failures"] == [{"code": "model_truncated", "kind": "model_truncated"}]
