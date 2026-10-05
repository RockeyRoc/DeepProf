from scripts.source_fingerprint import _included


def test_autoresearch_control_artifacts_do_not_change_source_fingerprint():
    assert not _included("autoresearch-results/run.json")
    assert not _included("autoresearch-results/archive/20260928-062546/events.jsonl")
    assert _included("scripts/run_m3_abc_research.py")
