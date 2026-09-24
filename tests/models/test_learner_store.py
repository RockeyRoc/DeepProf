from __future__ import annotations

import threading
from concurrent.futures import ThreadPoolExecutor

import pytest

from models.learner.store import SqliteLearnerStore
from runtime.storage.migrations import connect


def _attempt(store: SqliteLearnerStore, attempt_id: str, *, item: str = "q1", learner: str = "student",
             course: str = "course-v1", version: str = "bank-v1", hint: int = 0,
             correct: bool | None = True, grading: str = "exact_normalized_match", confidence: float = 1.0,
             enabled: bool = True, unambiguous: bool = True,
             concept_ids: list[str] | None = None) -> dict:
    return store.record_attempt(
        attempt_id=attempt_id, learner_id=learner, session_id=f"session-{attempt_id}", trace_id=f"trace-{attempt_id}",
        course_id=course, item_id=item, concept_id="concept-1", bank_version=version, concept_ids=concept_ids,
        correct=correct, hint_count=hint, grading_source=grading, confidence=confidence,
        bkt_enabled=enabled, concept_unambiguous=unambiguous,
    )


def test_first_unhinted_reliable_attempt_updates_once_and_records_skip_reasons(tmp_path):
    store = SqliteLearnerStore.open(tmp_path / "learner.sqlite")
    first = _attempt(store, "first")
    assert first["eligible"] is True
    assert first["predicted_correct"] == pytest.approx(0.34)
    assert first["learner_estimate"]["status"] == "insufficient_data"

    replay = _attempt(store, "first")
    assert replay["replayed"] is True
    retry = _attempt(store, "retry")
    hinted = _attempt(store, "hinted", item="q2", hint=1)
    pending = _attempt(store, "pending", item="q3", correct=None, grading="manual_pending", confidence=0)
    ambiguous = _attempt(store, "ambiguous", item="q4", unambiguous=False, concept_ids=["concept-1", "concept-2"])
    other_group = _attempt(store, "disabled", item="q5", enabled=False)
    assert retry["skip_reason"] == "repeat_attempt"
    assert hinted["skip_reason"] == "hinted_attempt"
    assert pending["correct"] is None and pending["skip_reason"] == "grading_unreliable"
    assert ambiguous["skip_reason"] == "ambiguous_concept"
    assert ambiguous["concept_ids"] == ["concept-1", "concept-2"]
    assert store._conn.execute("SELECT concept_ids FROM attempts WHERE attempt_id='ambiguous'").fetchone()[0] == '["concept-1","concept-2"]'
    assert store.pending_events()[-2]["payload"]["concept_ids"] == ["concept-1", "concept-2"]
    assert other_group["skip_reason"] == "group_disabled"
    assert store._conn.execute("SELECT COUNT(*) FROM bkt_observations").fetchone()[0] == 1
    assert store._conn.execute("SELECT answer_value FROM attempts WHERE attempt_id='first'").fetchone()[0] == ""
    assert len(store.pending_events()) == 6


def test_estimates_are_isolated_and_become_available_at_three_evidence(tmp_path):
    db = tmp_path / "learner.sqlite"
    store = SqliteLearnerStore.open(db)
    for index in range(3):
        result = _attempt(store, f"q{index}", item=f"q{index}")
    assert result["learner_estimate"]["status"] == "available"
    assert result["learner_estimate"]["evidence_count"] == 3
    assert result["learner_estimate"]["uncertainty_kind"] == "binary_entropy_bits; not a confidence interval"

    other_learner = _attempt(store, "other-student", learner="student-2", item="q0")
    other_course = _attempt(store, "other-course", course="course-v2", item="q0")
    assert other_learner["learner_estimate"]["evidence_count"] == 1
    assert other_course["learner_estimate"]["evidence_count"] == 1

    reopened = SqliteLearnerStore.open(db)
    estimate = reopened.get_estimate("student", "course-v1", "concept-1")
    assert estimate["status"] == "available" and estimate["evidence_count"] == 3


def test_concurrent_first_attempts_only_apply_one_bkt_update(tmp_path):
    db = tmp_path / "concurrent.sqlite"
    stores = [SqliteLearnerStore.open(db) for _ in range(4)]
    barrier = threading.Barrier(len(stores))

    def submit(index: int):
        barrier.wait(timeout=3)
        return _attempt(stores[index], f"parallel-{index}")

    with ThreadPoolExecutor(max_workers=len(stores)) as pool:
        results = list(pool.map(submit, range(len(stores))))
    assert sum(bool(result["eligible"]) for result in results) == 1
    assert sum(result["skip_reason"] == "repeat_attempt" for result in results) == len(stores) - 1
    check = SqliteLearnerStore.open(db)
    assert check._conn.execute("SELECT COUNT(*) FROM bkt_observations").fetchone()[0] == 1


def test_outbox_failure_rolls_back_attempt_and_estimate(tmp_path):
    store = SqliteLearnerStore.open(tmp_path / "rollback.sqlite")
    store._conn.execute("CREATE TRIGGER fail_outbox BEFORE INSERT ON learner_event_outbox BEGIN SELECT RAISE(ABORT, 'injected'); END")
    store._conn.commit()
    try:
        _attempt(store, "rollback")
    except Exception as exc:
        assert "injected" in str(exc)
    else:
        raise AssertionError("outbox failure did not abort the transaction")
    assert store._conn.execute("SELECT COUNT(*) FROM attempts").fetchone()[0] == 0
    assert store._conn.execute("SELECT COUNT(*) FROM learner_estimates").fetchone()[0] == 0


def test_old_attempt_schema_migrates_to_support_unscored_manual_attempt(tmp_path):
    path = tmp_path / "legacy.sqlite"
    connection = connect(path)
    connection.execute("DROP INDEX IF EXISTS idx_attempts_learner_concept")
    connection.execute("DROP INDEX IF EXISTS idx_attempts_session")
    connection.execute("DROP INDEX IF EXISTS idx_attempts_first_eligible")
    connection.execute("ALTER TABLE attempts RENAME TO attempts_newer")
    connection.execute("""CREATE TABLE attempts (
        attempt_id TEXT PRIMARY KEY, learner_id TEXT NOT NULL, session_id TEXT NOT NULL,
        trace_id TEXT NOT NULL DEFAULT '', item_id TEXT NOT NULL, scored_concept_id TEXT NOT NULL,
        is_correct INTEGER NOT NULL CHECK (is_correct IN (0,1)), answer_value TEXT NOT NULL DEFAULT '',
        hint_count INTEGER NOT NULL DEFAULT 0, grading_source TEXT NOT NULL, confidence REAL NOT NULL,
        bank_version TEXT NOT NULL, created_at TEXT NOT NULL
    )""")
    connection.execute("DROP TABLE attempts_newer")
    connection.commit()
    connection.close()

    migrated = connect(path)
    migrated.execute("""INSERT INTO attempts (
        attempt_id,learner_id,session_id,trace_id,item_id,scored_concept_id,is_correct,answer_value,
        hint_count,grading_source,confidence,bank_version,created_at,course_id,eligible,skip_reason
    ) VALUES ('pending','student','session','trace','q','concept',NULL,'',0,'manual_pending',0,'bank','now','course',0,'grading_unreliable')""")
    migrated.commit()
    assert migrated.execute("SELECT is_correct FROM attempts WHERE attempt_id='pending'").fetchone()[0] is None
