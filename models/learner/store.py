"""Transactional local Attempt, BKT estimate and audit outbox storage."""

from __future__ import annotations

import json
import sqlite3
import threading
from pathlib import Path
from typing import Any

from models.learner.attempt import Attempt
from models.learner.bkt import DEFAULT_PARAMETERS, MIN_EVIDENCE, BKTParameters, binary_entropy, initial_mastery, update
from runtime.core.events import new_id, utc_now
from runtime.storage.migrations import connect


class SqliteLearnerStore:
    def __init__(self, connection: sqlite3.Connection) -> None:
        self._conn = connection
        self._lock = threading.RLock()

    @classmethod
    def open(cls, path: str | Path) -> "SqliteLearnerStore":
        return cls(connect(path))

    def record_attempt(self, *, attempt_id: str, learner_id: str, session_id: str, trace_id: str,
                       course_id: str, item_id: str, concept_id: str, bank_version: str,
                       concept_ids: list[str] | tuple[str, ...] | None = None,
                       correct: bool | None, hint_count: int, grading_source: str, confidence: float,
                       parameters: BKTParameters = DEFAULT_PARAMETERS, bkt_enabled: bool = True,
                       concept_unambiguous: bool = True) -> dict[str, Any]:
        # Validate and normalize through the public Attempt contract before mapping it
        # to SQLite columns. This keeps the DB row, event payload and DTO in sync.
        attempt = Attempt(
            attempt_id=attempt_id, learner_id=learner_id, session_id=session_id, trace_id=trace_id,
            course_id=course_id, item_id=item_id, scored_concept_id=concept_id,
            concept_ids=concept_ids or [concept_id], correct=correct,
            question_bank_version=bank_version, grading_source=grading_source,
            confidence=confidence, timestamp=utc_now(), hint_count=hint_count,
        )
        attempt_id = attempt.attempt_id
        learner_id = attempt.learner_id
        session_id = attempt.session_id
        trace_id = attempt.trace_id
        course_id = attempt.course_id
        item_id = attempt.item_id
        concept_id = attempt.scored_concept_id
        bank_version = attempt.question_bank_version
        correct = attempt.correct
        hint_count = attempt.hint_count
        grading_source = attempt.grading_source
        confidence = attempt.confidence
        normalized_concepts = attempt.concept_ids
        encoded_concepts = json.dumps(normalized_concepts, ensure_ascii=False, separators=(",", ":"))
        with self._lock:
            connection = self._conn
            connection.execute("BEGIN IMMEDIATE")
            try:
                existing = connection.execute("SELECT * FROM attempts WHERE attempt_id=?", (attempt_id,)).fetchone()
                if existing is not None:
                    expected_correct = int(correct) if correct is not None else None
                    identity = ("learner_id", "session_id", "trace_id", "course_id", "item_id", "scored_concept_id",
                                "concept_ids", "bank_version", "is_correct", "hint_count", "grading_source", "confidence")
                    expected = {"learner_id": learner_id, "session_id": session_id, "course_id": course_id,
                                "trace_id": trace_id,
                                "item_id": item_id, "scored_concept_id": concept_id, "concept_ids": encoded_concepts,
                                "bank_version": bank_version,
                                "is_correct": expected_correct, "hint_count": int(hint_count),
                                "grading_source": grading_source, "confidence": float(confidence)}
                    if any(existing[key] != expected[key] for key in identity):
                        raise ValueError("attempt_id_conflict")
                    result = self._attempt_dict(existing)
                    result["replayed"] = True
                    result["learner_estimate"] = self.get_estimate(learner_id, course_id, concept_id, parameters) if bkt_enabled else None
                    connection.commit()
                    return result

                skip_reason = self._skip_reason(connection, learner_id=learner_id, course_id=course_id,
                    item_id=item_id, concept_id=concept_id, bank_version=bank_version, hint_count=hint_count,
                    correct=correct, grading_source=grading_source, confidence=confidence, bkt_enabled=bkt_enabled,
                    concept_unambiguous=concept_unambiguous)
                eligible = not skip_reason
                now = attempt.timestamp
                connection.execute(
                    "INSERT INTO attempts (attempt_id,learner_id,session_id,trace_id,item_id,scored_concept_id,concept_ids,"
                    "is_correct,answer_value,hint_count,grading_source,confidence,bank_version,created_at,course_id,eligible,skip_reason) "
                    "VALUES (?,?,?,?,?,?,?,?, '',?,?,?,?,?,?,?,?)",
                     (attempt_id, learner_id, session_id, trace_id, item_id, concept_id, encoded_concepts,
                     int(correct) if correct is not None else None, int(hint_count),
                     grading_source, float(confidence), bank_version, now, course_id, int(eligible), skip_reason),
                )
                estimate = None
                predicted = None
                if eligible:
                    row = connection.execute(
                        "SELECT mastery,evidence_count,config_hash FROM learner_estimates "
                        "WHERE learner_id=? AND course_id=? AND concept_id=? AND model_version=?",
                        (learner_id, course_id, concept_id, parameters.model_version),
                    ).fetchone()
                    if row is not None and str(row["config_hash"]) != parameters.config_hash:
                        raise ValueError("BKT parameters changed within the frozen model version")
                    before = float(row["mastery"]) if row else initial_mastery(parameters)
                    evidence_count = int(row["evidence_count"]) if row else 0
                    predicted, after = update(before, bool(correct), parameters)
                    evidence_count += 1
                    encoded_parameters = json.dumps(parameters.to_dict(), ensure_ascii=False, sort_keys=True)
                    connection.execute(
                        "INSERT INTO learner_estimates (learner_id,course_id,concept_id,model_version,config_hash,parameters,mastery,evidence_count,updated_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?) ON CONFLICT(learner_id,course_id,concept_id,model_version,config_hash) "
                        "DO UPDATE SET mastery=excluded.mastery,evidence_count=excluded.evidence_count,updated_at=excluded.updated_at,parameters=excluded.parameters",
                        (learner_id, course_id, concept_id, parameters.model_version, parameters.config_hash,
                         encoded_parameters, after, evidence_count, now),
                    )
                    connection.execute(
                        "INSERT INTO bkt_observations (attempt_id,learner_id,course_id,concept_id,predicted_correct,mastery_before,mastery_after,evidence_count,model_version,config_hash,created_at) "
                        "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                        (attempt_id, learner_id, course_id, concept_id, predicted, before, after, evidence_count,
                         parameters.model_version, parameters.config_hash, now),
                    )
                    estimate = self._estimate_dict(learner_id, course_id, concept_id, parameters,
                                                   after, evidence_count, now)
                record = {
                    "attempt_id": attempt_id, "learner_id": learner_id, "session_id": session_id,
                    "trace_id": trace_id, "course_id": course_id, "item_id": item_id,
                    "scored_concept_id": concept_id, "question_bank_version": bank_version,
                    "concept_ids": normalized_concepts,
                    "correct": bool(correct) if correct is not None else None,
                    "hint_count": int(hint_count), "grading_source": grading_source,
                    "confidence": float(confidence), "timestamp": now,
                    "eligible": eligible, "skip_reason": skip_reason,
                    "predicted_correct": predicted, "learner_estimate": estimate, "replayed": False,
                }
                event_type = "pedagogy.attempt" if eligible else "pedagogy.attempt_skipped"
                event_payload = {key: record[key] for key in (
                    "attempt_id", "course_id", "item_id", "scored_concept_id", "concept_ids", "question_bank_version",
                    "correct", "hint_count", "grading_source", "confidence", "eligible", "skip_reason",
                    "predicted_correct", "learner_estimate")}
                connection.execute(
                    "INSERT INTO learner_event_outbox (event_id,session_id,trace_id,event_type,payload,created_at) VALUES (?,?,?,?,?,?)",
                    (new_id("evt"), session_id, trace_id, event_type,
                     json.dumps(event_payload, ensure_ascii=False, separators=(",", ":"), default=str), now),
                )
                connection.commit()
                return record
            except BaseException:
                connection.rollback()
                raise

    @staticmethod
    def _skip_reason(connection: sqlite3.Connection, *, learner_id: str, course_id: str, item_id: str,
                     concept_id: str, bank_version: str, hint_count: int,
                     correct: bool | None, grading_source: str, confidence: float, bkt_enabled: bool,
                     concept_unambiguous: bool) -> str:
        if not bkt_enabled:
            return "group_disabled"
        if not concept_id.strip():
            return "scored_concept_missing"
        if not concept_unambiguous:
            return "ambiguous_concept"
        if correct is None:
            return "grading_unreliable"
        if grading_source != "exact_normalized_match" or confidence < 0.99:
            return "grading_unreliable"
        if hint_count > 0:
            return "hinted_attempt"
        repeated = connection.execute(
            "SELECT 1 FROM attempts WHERE learner_id=? AND course_id=? AND item_id=? AND bank_version=? LIMIT 1",
            (learner_id, course_id, item_id, bank_version),
        ).fetchone()
        if repeated:
            return "repeat_attempt"
        return ""

    def get_estimate(self, learner_id: str, course_id: str, concept_id: str,
                     parameters: BKTParameters = DEFAULT_PARAMETERS) -> dict[str, Any]:
        if not learner_id or not course_id or not concept_id:
            return {"status": "insufficient_data", "learner_id": learner_id, "course_id": course_id,
                    "concept_id": concept_id, "model_type": "bkt", "model_version": parameters.model_version,
                    "evidence_count": 0, "mastery": None, "uncertainty": None}
        row = self._conn.execute(
            "SELECT mastery,evidence_count,updated_at,config_hash FROM learner_estimates "
            "WHERE learner_id=? AND course_id=? AND concept_id=? AND model_version=? AND config_hash=?",
            (learner_id, course_id, concept_id, parameters.model_version, parameters.config_hash),
        ).fetchone()
        if row is None:
            return {"status": "insufficient_data", "learner_id": learner_id, "course_id": course_id,
                    "concept_id": concept_id, "model_type": "bkt", "model_version": parameters.model_version,
                    "config_hash": parameters.config_hash, "evidence_count": 0, "mastery": None,
                    "uncertainty": None, "parameter_status": parameters.source}
        return self._estimate_dict(learner_id, course_id, concept_id, parameters,
                                   float(row["mastery"]), int(row["evidence_count"]), str(row["updated_at"]))

    def list_estimates(self, learner_id: str, course_id: str,
                       parameters: BKTParameters = DEFAULT_PARAMETERS) -> list[dict[str, Any]]:
        rows = self._conn.execute(
            "SELECT concept_id,mastery,evidence_count,updated_at FROM learner_estimates "
            "WHERE learner_id=? AND course_id=? AND model_version=? AND config_hash=? ORDER BY concept_id",
            (learner_id, course_id, parameters.model_version, parameters.config_hash),
        ).fetchall()
        return [self._estimate_dict(learner_id, course_id, str(row["concept_id"]), parameters,
                                    float(row["mastery"]), int(row["evidence_count"]), str(row["updated_at"]))
                for row in rows]

    def delete_session_data(self, session_id: str) -> dict[str, int]:
        """Remove a session's learning records and replay affected estimates atomically."""
        with self._lock:
            connection = self._conn
            connection.execute("BEGIN IMMEDIATE")
            try:
                affected = connection.execute(
                    "SELECT DISTINCT a.learner_id,a.course_id,a.scored_concept_id "
                    "FROM attempts a WHERE a.session_id=? AND a.eligible=1", (session_id,)
                ).fetchall()
                attempt_count = int(connection.execute(
                    "SELECT COUNT(*) FROM attempts WHERE session_id=?", (session_id,)
                ).fetchone()[0])
                observation_count = int(connection.execute(
                    "SELECT COUNT(*) FROM bkt_observations WHERE attempt_id IN "
                    "(SELECT attempt_id FROM attempts WHERE session_id=?)", (session_id,)
                ).fetchone()[0])
                outbox_count = int(connection.execute(
                    "SELECT COUNT(*) FROM learner_event_outbox WHERE session_id=?", (session_id,)
                ).fetchone()[0])
                snapshots: dict[tuple[str, str, str], list[sqlite3.Row]] = {}
                for row in affected:
                    key = (str(row["learner_id"]), str(row["course_id"]), str(row["scored_concept_id"]))
                    snapshots[key] = connection.execute(
                        "SELECT model_version,config_hash,parameters FROM learner_estimates "
                        "WHERE learner_id=? AND course_id=? AND concept_id=?", key
                    ).fetchall()
                connection.execute("DELETE FROM bkt_observations WHERE attempt_id IN "
                                    "(SELECT attempt_id FROM attempts WHERE session_id=?)", (session_id,))
                connection.execute("DELETE FROM attempts WHERE session_id=?", (session_id,))
                connection.execute("DELETE FROM learner_event_outbox WHERE session_id=?", (session_id,))

                for (learner_id, course_id, concept_id), estimates in snapshots.items():
                    for estimate in estimates:
                        version, config_hash = str(estimate["model_version"]), str(estimate["config_hash"])
                        parameters = BKTParameters(**json.loads(str(estimate["parameters"])))
                        connection.execute(
                            "DELETE FROM learner_estimates WHERE learner_id=? AND course_id=? AND concept_id=? "
                            "AND model_version=? AND config_hash=?",
                            (learner_id, course_id, concept_id, version, config_hash),
                        )
                        mastery = initial_mastery(parameters)
                        evidence = 0
                        attempts = connection.execute(
                            "SELECT attempt_id,is_correct,created_at FROM attempts WHERE learner_id=? AND course_id=? "
                            "AND scored_concept_id=? AND eligible=1 AND is_correct IS NOT NULL "
                            "ORDER BY created_at,attempt_id", (learner_id, course_id, concept_id)
                        ).fetchall()
                        for attempt in attempts:
                            before = mastery
                            predicted, mastery = update(before, bool(attempt["is_correct"]), parameters)
                            evidence += 1
                            connection.execute(
                                "UPDATE bkt_observations SET predicted_correct=?,mastery_before=?,mastery_after=?, "
                                "evidence_count=? WHERE attempt_id=? AND model_version=? AND config_hash=?",
                                (predicted, before, mastery, evidence, str(attempt["attempt_id"]), version, config_hash),
                            )
                        if evidence:
                            connection.execute(
                                "INSERT INTO learner_estimates (learner_id,course_id,concept_id,model_version,config_hash,parameters,mastery,evidence_count,updated_at) "
                                "VALUES (?,?,?,?,?,?,?,?,?)",
                                (learner_id, course_id, concept_id, version, config_hash,
                                 json.dumps(parameters.to_dict(), ensure_ascii=False, sort_keys=True), mastery,
                                 evidence, utc_now()),
                            )
                connection.commit()
                return {"attempts_deleted": attempt_count, "observations_deleted": observation_count,
                        "outbox_deleted": outbox_count, "estimates_replayed": sum(len(rows) for rows in snapshots.values())}
            except BaseException:
                connection.rollback()
                raise

    def close(self) -> None:
        """Release the SQLite handle owned by this store."""
        with self._lock:
            self._conn.close()

    @staticmethod
    def _estimate_dict(learner_id: str, course_id: str, concept_id: str, parameters: BKTParameters,
                      mastery: float, evidence_count: int, updated_at: str) -> dict[str, Any]:
        ready = evidence_count >= MIN_EVIDENCE
        return {"status": "available" if ready else "insufficient_data", "learner_id": learner_id,
                "course_id": course_id, "concept_id": concept_id, "model_type": "bkt",
                "model_version": parameters.model_version, "config_hash": parameters.config_hash,
                "mastery": round(mastery, 8) if ready else None, "evidence_count": evidence_count,
                "uncertainty": round(binary_entropy(mastery), 8) if ready else None,
                "uncertainty_kind": "binary_entropy_bits; not a confidence interval" if ready else None,
                "updated_at": updated_at, "parameter_status": parameters.source}

    def pending_events(self, *, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM learner_event_outbox WHERE delivered_at IS NULL ORDER BY created_at LIMIT ?",
                (max(1, min(int(limit), 500)),),
            ).fetchall()
        return [{"event_id": row["event_id"], "session_id": row["session_id"], "trace_id": row["trace_id"],
                 "type": row["event_type"], "payload": json.loads(row["payload"]), "timestamp": row["created_at"]}
                for row in rows]

    def mark_event_delivered(self, event_id: str) -> None:
        with self._lock:
            self._conn.execute("UPDATE learner_event_outbox SET delivered_at=? WHERE event_id=?",
                               (utc_now(), event_id))
            self._conn.commit()

    @staticmethod
    def _attempt_dict(row: sqlite3.Row) -> dict[str, Any]:
        return {"attempt_id": row["attempt_id"], "learner_id": row["learner_id"],
                "course_id": row["course_id"], "session_id": row["session_id"], "trace_id": row["trace_id"],
                "item_id": row["item_id"], "scored_concept_id": row["scored_concept_id"],
                "concept_ids": json.loads(row["concept_ids"] or "[]"),
                "correct": bool(row["is_correct"]) if row["is_correct"] is not None else None,
                "hint_count": int(row["hint_count"]),
                "grading_source": row["grading_source"], "confidence": float(row["confidence"]),
                "question_bank_version": row["bank_version"], "timestamp": row["created_at"],
                "eligible": bool(row["eligible"]),
                "skip_reason": row["skip_reason"]}


__all__ = ["SqliteLearnerStore"]
