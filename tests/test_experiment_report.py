import re
import json
import sqlite3

from scripts.build_experiment_report import (
    acceptance_qa_status_line,
    automation_validation_line,
    descriptions,
)
from scripts.build_experiment_snapshot import enrich_turns_from_events, next_steps_for


def test_ab_chart_description_uses_live_completion_counts_without_stale_zero_copy():
    snapshot = {
        "real_ab_acceptance": {
            "run_id": "run-test-final",
            "completed_cells": 80,
            "planned_cells": 80,
        },
        "acceptance_audit": {"status": "passed"},
        "performance_by_group": {
            "A": {"latency_ms": {"n": 40}},
            "B": {"latency_ms": {"n": 40}},
        },
    }

    description = "\n".join(descriptions(snapshot)["F02"])

    assert "80/80" in description
    assert re.search(r"(?<!\d)0/80(?!\d)", description) is None
    assert "Provider 未配置" not in description


def test_snapshot_adds_failed_trace_usage_from_the_local_event_store(tmp_path):
    db_path = tmp_path / "sessions.sqlite"
    with sqlite3.connect(db_path) as db:
        db.execute("CREATE TABLE events (session_id TEXT, trace_id TEXT, sequence INTEGER, type TEXT, payload TEXT)")
        rows = [
            ("s1", "t1", 1, "model.requested", {"model": "m"}),
            ("s1", "t1", 2, "model.completed", {"usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}),
            ("s1", "t1", 3, "agent.turn.completed", {"status": "ok"}),
            ("s1", "t2", 4, "model.requested", {"model": "m"}),
            ("s1", "t2", 5, "model.completed", {"usage": {"prompt_tokens": 20, "completion_tokens": 7, "total_tokens": 27}}),
            ("s1", "t2", 6, "agent.turn.completed", {"status": "failed"}),
        ]
        db.executemany("INSERT INTO events VALUES (?, ?, ?, ?, ?)", [(*row[:4], json.dumps(row[4])) for row in rows])

    cells, model_calls, source = enrich_turns_from_events([{
        "session_id": "s1", "group": "B", "status": "failed", "trace_ids": ["t1", "t2"],
        "turns": [{"trace_id": "t1", "turn_index": 1, "elapsed_ms": 100, "terminal_status": "ok"}],
    }], db_path)

    assert model_calls == 2
    assert source == "session_events:model.requested"
    assert len(cells[0]["turns"]) == 2
    assert cells[0]["turns"][1]["terminal_status"] == "failed"
    assert cells[0]["turns"][1].get("elapsed_ms") is None
    assert cells[0]["turns"][1]["usage"]["total_tokens"] == 27


def test_acceptance_summary_formats_fault_injection_without_python_dict_repr():
    line = acceptance_qa_status_line({
        "acceptance_audit": {"status": "needs_attention"},
        "validation": {
            "fault_injection": {"status": "passed", "tests_passed": 30},
            "process_recovery": "passed",
        },
    })

    assert "故障注入 passed（30 项）" in line
    assert "进程恢复 passed" in line
    assert "{'status':" not in line


def test_markdown_automation_summary_uses_current_validation_counts():
    line = automation_validation_line({
        "validation": {
            "python": {"passed": 273, "skipped": 0},
            "cli": {"tests_passed": 3, "typecheck": "passed"},
        },
    })

    assert "Python 273 项通过 / 0 项跳过" in line
    assert "CLI 3 项通过" in line
    assert "类型检查 passed" in line


def test_user_confirmed_docx_acceptance_does_not_remain_a_pending_next_step():
    steps = next_steps_for({
        "question_bank": {"teacher_approval": "approved"},
        "textbook_page_audit": {"pending_manual_pages": 0, "total_pdf_pages": 347},
        "course_coverage": {"coverage": []},
        "acceptance_audit": {"status": "passed"},
        "validation": {
            "docx_visual_qa": "accepted_by_user",
            "figures": {
                "pdf_text_audit": {"status": "passed"},
                "pdf_collision_audit": {"status": "passed"},
            },
        },
    })

    assert not any("DOCX" in step for step in steps)
