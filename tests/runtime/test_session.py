"""Session 生命周期测试（§5.1：load / append / fork / compact）。"""
from __future__ import annotations

from runtime.core.message import Message
from runtime.core.session import Session


def test_session_append_and_persist_roundtrip():
    session = Session(learner_id="learner_a")
    session.append(Message.user("在吗"))
    session.append(Message.assistant("在的，今天想学点什么？"))

    restored = Session.load(session.to_dict())
    assert restored.session_id == session.session_id
    assert [m.content for m in restored.messages] == ["在吗", "在的，今天想学点什么？"]


def test_session_append_refreshes_updated_at():
    session = Session()
    before = session.updated_at
    session.append(Message.user("hi"))
    assert session.updated_at >= before


def test_session_compact_keeps_recent_and_summarizes():
    """压缩后只保留最近 N 条，其余折叠为摘要，避免上下文无限膨胀。"""
    session = Session()
    for index in range(10):
        session.append(Message.user(f"第 {index} 轮提问"))

    session.compact(keep_last=3)

    assert len(session.messages) == 3
    assert session.messages[0].content == "第 7 轮提问"
    assert "第 0 轮提问" in session.compacted_summary


def test_session_compact_is_noop_when_short():
    session = Session()
    session.append(Message.user("只有一个问题"))
    assert len(session.compact(keep_last=12).messages) == 1
    assert session.compacted_summary == ""


def test_session_fork_keeps_history_and_records_parent():
    """分支会话保留历史，并记录来源便于回溯（探索不同教学策略）。"""
    parent = Session(learner_id="learner_a")
    parent.append(Message.user("原会话"))

    child = parent.fork()

    assert child.session_id != parent.session_id
    assert child.parent_session_id == parent.session_id
    assert child.learner_id == "learner_a"
    assert child.messages[0].content == "原会话"
    # fork 后互不影响
    child.append(Message.user("分支新增"))
    assert len(parent.messages) == 1


def test_last_user_message_helper():
    session = Session()
    session.append(Message.user("第一个"))
    session.append(Message.assistant("回答"))
    session.append(Message.user("第二个"))
    assert session.last_user_message().content == "第二个"