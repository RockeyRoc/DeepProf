from __future__ import annotations

from runtime.core.events import RuntimeEvent
from runtime.core.message import Message
from runtime.core.session import Session
from runtime.storage.command_store import SqliteCommandStore
from runtime.storage.sqlite_store import SqliteEventStore, SqliteSessionStore


def test_sqlite_session_delete_removes_private_history_events_and_scrubs_receipts(tmp_path):
    database = str(tmp_path / "history.sqlite")
    sessions = SqliteSessionStore.open(database)
    events = SqliteEventStore.open(database)
    commands = SqliteCommandStore.open(database)
    parent = Session(title="chat", metadata={"session_mode": "chat"})
    parent.append(Message(role="assistant", content="answer", metadata={"reasoning_content": "private thought"}))
    child = parent.fork(title="branch")
    sessions.save(parent)
    sessions.save(child)
    events.append(RuntimeEvent(type="test.event", payload={"secret_reasoning": "private thought"}, session_id=parent.session_id))
    assert commands.reserve("sent", "trace")
    commands.complete("sent", status="accepted", session_id=parent.session_id, result={"private": "receipt"})

    assert sessions.delete(parent.session_id)
    assert sessions.load(parent.session_id) is None
    assert sessions.load(child.session_id).parent_id is None
    assert events.replay(parent.session_id) == []
    receipt = commands.get("sent")
    assert receipt == {"command_id": "sent", "trace_id": "trace", "session_id": None, "status": "deleted", "result": {}}

    sessions.close()
    events.close()
    commands.close()
