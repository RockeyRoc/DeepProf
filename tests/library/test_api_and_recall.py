from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi.testclient import TestClient

from api.app import create_app
from config.settings import Settings
from graph.education.bindings import ACTION_BINDINGS
from library.service import ResourceLibrary
from runtime.storage.migrations import connect_memory
from runtime.storage.resource_store import SqliteResourceStore
from runtime.testing import make_service
from skills import register_default_skills
from tools.retrieval import build_search_textbook_tool


def test_twenty_query_recall_benchmark() -> None:
    root = Path("data/course_manifest")
    library = ResourceLibrary(
        SqliteResourceStore(connect_memory()),
        library_root=Path("tmp/mvp4-recall-test"),
    )
    library.import_path(
        root / "mini_math.md",
        metadata={"course_id": "deepprof.math101", "license": "CC0", "visibility": "public"},
        activate=True,
    )
    queries = json.loads((root / "queries.json").read_text(encoding="utf-8"))
    hits = 0
    for item in queries:
        result = library.search(item["query"], course_id="deepprof.math101", top_k=5)
        hits += int(any(hit["section"] == item["section"] for hit in result["evidence"]))
    assert len(queries) == 20
    assert hits == 20


def test_library_commands_and_events_are_wired(tmp_path: Path) -> None:
    service = make_service(settings=Settings(sqlite_path=str(tmp_path / "runtime.sqlite")))
    app = create_app(service=service)
    service.sandbox.allowed_dirs.append(tmp_path.resolve())
    source = tmp_path / "lesson.md"
    source.write_text("# Lesson\nA derivative is a slope.", encoding="utf-8")
    with TestClient(app) as client:
        response = client.post(
            "/commands",
            json={
                "command_id": "library-import-1",
                "surface": "cli",
                "learner_id": "alice",
                "type": "library.import",
                "payload": {
                    "path": str(source),
                    "activate": True,
                    "metadata": {"course_id": "math", "license": "CC0"},
                },
            },
        )
        assert response.status_code == 200
        resource_id = response.json()["result"]["resource_id"]
        assert client.get("/library/resources?course_id=math&owner_id=alice").json()[0]["resource_id"] == resource_id
        assert client.get("/library/search?query=derivative%20slope&owner_id=alice").json()["status"] == "ok"
        assert client.get(f"/library/resources/{resource_id}/preview?owner_id=alice").json()
        events = service.history("")
        assert [event["type"] for event in events] == ["library.imported", "library.indexed"]
        assert "text" not in events[0]["payload"]


def test_real_rag_chain_keeps_text_out_of_graph_capability_evidence(tmp_path: Path) -> None:
    source = tmp_path / "lesson.md"
    source.write_text("# Derivatives\nA derivative is a slope.", encoding="utf-8")
    library = ResourceLibrary(
        SqliteResourceStore(connect_memory()),
        library_root=tmp_path / "library",
    )
    library.import_path(source, metadata={"owner_id": "local", "course_id": "math"}, activate=True)

    service = make_service(bindings=ACTION_BINDINGS)
    service.library = library
    register_default_skills(service.skills)
    service.tools.register(build_search_textbook_tool(library.search))

    raw = asyncio.run(
        service.invoke_skill(
            "rag",
            {"query": "derivative slope", "top_k": 5},
            {"learner_id": "local", "trace_id": "rag-test"},
        )
    )
    assert raw["status"] == "ok"
    assert raw["evidence"][0]["text"]

    result = asyncio.run(
        service.execute(
            {
                "action": "teach",
                "concept": "derivative",
                "require_evidence": True,
                "params": {
                    "course_id": "math",
                    "query": "derivative slope",
                    "learning_goal": "understand",
                    "user_input": "explain",
                    "prior_gap_note": "",
                },
            },
            {"learner_id": "local", "trace_id": "teach-test"},
        )
    )
    assert result["status"] == "success"
    assert result["evidence"]
    assert "text" not in result["evidence"][0]
