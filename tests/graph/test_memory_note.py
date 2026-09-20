"""学情记忆进入生成上下文的链路测试（DESIGNv0.4 §6.2 / §6.4 / §13.2）。

背景：跨轮记忆探针实验（tests/experiment/memory_probe.py）证实——
记忆读写每轮都在发生，但召回内容从未进入生成提示词，跨轮个性化是空转。
本文件锁住修复后的完整链路：

    UpdateProfile 写学生自述背景记录（有界、可撤回）
    → Assess 压缩召回为 memory_note（条数/字数上限在 policies）
    → teach / ask / correct 经 params 透传
    → 绑定把 memory_note 拼进提示词（Teach/Correct 模板、Socratic Skill input）

纪律：memory_note 只作个性化参考，模板里明确声明它不是教材依据（§7.3）；
摘录有界（STUDENT_CONTEXT_MAX_CHARS）且 revocable=True（§13.2）。
"""
from __future__ import annotations

import asyncio

from graph.education.bindings import ACTION_BINDINGS, CORRECT_PROMPT_TEMPLATE, TEACH_PROMPT_TEMPLATE
from graph.education.builder import run_teaching_turn
from graph.education.nodes.assess import assess
from graph.education.nodes.update_profile import update_profile
from graph.education.policies import (
    MEMORY_NOTE_MAX_CHARS,
    MEMORY_NOTE_MAX_RECORDS,
    STUDENT_CONTEXT_KEY_PREFIX,
    memory_note,
    student_context_key,
)
from graph.education.state import new_state
from runtime.testing import FakeRuntime

EVIDENCE = {
    "evidence_id": "doc1#c1@12",
    "document_id": "doc1",
    "chunk_id": "c1",
    "page": 12,
    "text": "导数刻画函数在某点的瞬时变化率。",
    "source": "教材A",
}

BASE_STATE = {
    "session_id": "sess_1",
    "learner_id": "learner_1",
    "trace_id": "trace_1",
    "current_concept": "导数",
    "learning_goal": "理解导数的定义",
}

MEMORY_RECORDS = [
    {
        "record_id": "r_ctx",
        "memory_type": "long_term",
        "key": f"{STUDENT_CONTEXT_KEY_PREFIX}导数",
        "content": "学生自述背景（原文摘录，可撤回）：我之前用牛顿迭代法算过题",
    },
    {
        "record_id": "r_obs",
        "memory_type": "long_term",
        "key": "concept:导数",
        "content": "知识点「导数」规则化观察：本轮动作=teach",
    },
]


def make_port(memories: list[dict] | None = None) -> FakeRuntime:
    return FakeRuntime(
        replies=["（模型回复）先回到定义。"],
        skill_results={
            "rag": {"status": "ok", "skill": "rag", "count": 1, "evidence": [dict(EVIDENCE)]},
            "socratic": {"status": "ok", "skill": "socratic", "question": "定义里每个条件起什么作用？"},
        },
        memories=list(memories or []),
        action_bindings=ACTION_BINDINGS,
    )


# ======================================================================
# 一、压缩策略：policies.memory_note
# ======================================================================
def test_memory_note_empty_without_records():
    """没有召回记录 → 空串（模板里不留一段空标题）。"""
    assert memory_note([]) == ""
    assert memory_note([{"record_id": "r1", "content": ""}]) == ""


def test_memory_note_caps_records_and_chars():
    """只取前 N 条、每条内容截断到上限（§6.4 状态不放大文本）。"""
    records = [
        {"record_id": f"r{i}", "content": "长" * (MEMORY_NOTE_MAX_CHARS + 50)}
        for i in range(MEMORY_NOTE_MAX_RECORDS + 2)
    ]
    note = memory_note(records)
    assert note.count("- 长") == MEMORY_NOTE_MAX_RECORDS
    for line in note.splitlines():
        if line.startswith("- "):
            assert len(line) <= len("- ") + MEMORY_NOTE_MAX_CHARS


def test_memory_note_declares_non_evidence_status():
    """摘要自带身份声明：个性化参考、不是教材依据（§7.3 引用纪律）。"""
    note = memory_note([{"record_id": "r1", "content": "任意内容"}])
    assert "学情记忆" in note
    assert "不是教材依据" in note
    assert "任意内容" in note


# ======================================================================
# 二、写入侧：UpdateProfile 落学生自述背景记录
# ======================================================================
def test_update_profile_writes_student_context_record():
    """有 user_input → 追加一条 context: 前缀的 long_term 背景记录（可撤回、有界）。"""
    port = make_port()
    state = new_state(**BASE_STATE, user_input="我之前用牛顿迭代法算过题")
    asyncio.run(update_profile(state, port))

    context_records = [
        r for r in port.written_memories if str(r.get("key", "")).startswith(STUDENT_CONTEXT_KEY_PREFIX)
    ]
    assert len(context_records) == 1
    record = context_records[0]
    assert record["memory_type"] == "long_term"
    assert record["key"] == student_context_key("导数")
    assert "牛顿迭代法" in record["content"]
    assert record["revocable"] is True
    assert len(record["content"]) < 200, "摘录必须有界，不能把整轮输入复制进记忆"


def test_update_profile_skips_context_without_user_input():
    """无 user_input → 不写背景记录（无中生有的背景比没有更糟）。"""
    port = make_port()
    state = new_state(**BASE_STATE, user_input="")
    asyncio.run(update_profile(state, port))

    assert not [
        r for r in port.written_memories if str(r.get("key", "")).startswith(STUDENT_CONTEXT_KEY_PREFIX)
    ]


# ======================================================================
# 三、压缩与透传：Assess 构造 memory_note，节点放进 params
# ======================================================================
def test_assess_writes_memory_note_into_state():
    """召回有内容 → 状态增量携带压缩后的 memory_note。"""
    port = make_port(memories=[dict(r) for r in MEMORY_RECORDS])
    update = asyncio.run(assess(new_state(**BASE_STATE), port))

    note = str(update.get("memory_note") or "")
    assert "牛顿迭代法" in note
    assert "学情记忆" in note


def test_assess_memory_note_empty_without_records():
    """召回为空 → memory_note 为空串（而不是缺键）。"""
    port = make_port()
    update = asyncio.run(assess(new_state(**BASE_STATE), port))
    assert update.get("memory_note") == ""


# ======================================================================
# 四、拼装侧：绑定把 memory_note 拼进提示词
# ======================================================================
def test_teach_template_declares_memory_note_placeholder():
    """模板必须含 {memory_note} 占位符——绑定与节点靠同一字段名对齐。"""
    assert "{memory_note}" in TEACH_PROMPT_TEMPLATE
    assert "{memory_note}" in CORRECT_PROMPT_TEMPLATE


def test_full_teach_turn_passes_memory_note_to_prompt():
    """整轮 Teach：召回的背景内容出现在发给模型的提示词里（修复的核心命题）。"""
    port = make_port(memories=[dict(r) for r in MEMORY_RECORDS])
    result = asyncio.run(
        run_teaching_turn(
            port,
            {
                **BASE_STATE,
                "user_input": "给我讲讲导数的定义",
            },
        )
    )

    assert result["action"] == "teach"
    generate_calls = port.calls_of("generate")
    assert generate_calls, "teach 分支应当调用模型生成"
    user_content = str(generate_calls[0]["request"]["messages"][-1]["content"])
    assert "牛顿迭代法" in user_content, "学情记忆内容必须进入生成上下文"
    assert "不是教材依据" in user_content


def test_ask_passes_memory_note_to_socratic_skill():
    """整轮 Ask：Socratic Skill 的 input 携带 memory_note（非空才拼接由 Skill 决定）。"""
    port = make_port(memories=[dict(r) for r in MEMORY_RECORDS])
    result = asyncio.run(
        run_teaching_turn(
            port,
            {
                **BASE_STATE,
                "learning_goal": "",
                "user_input": "我猜导数就是斜率，对吗",
            },
        )
    )

    assert result["action"] == "ask"
    skill_calls = [c for c in port.calls_of("invoke_skill") if c["name"] == "socratic"]
    assert skill_calls, "ask 分支应当调用 socratic skill"
    assert "牛顿迭代法" in str(skill_calls[0]["input"].get("memory_note") or "")


__all__ = []
