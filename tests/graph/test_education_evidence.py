"""证据纪律与学情写入测试（DESIGNv0.4 §7.3 / §12 / §5.5 / §13.2）。

覆盖两条硬性验收：
- 证据不足时 Teach / Correct **不编造引用**：不调用模型、citations 为空、
  状态里没有证据引用、正文明确写出"证据不足"；
- UpdateProfile 写入的记忆必须带 source / confidence / revocable，
  且缺少 learner_id 时宁可不写，避免跨学习者污染。
"""
from __future__ import annotations

import asyncio

from graph.education.bindings import ACTION_BINDINGS
from graph.education.builder import run_teaching_turn
from graph.education.policies import HINT_MAX_LEVEL, RULE_MODEL_VERSION
from runtime.testing import FakeRuntime

#: 与 test_education_routing.py 保持同一套样例；此处独立定义，
#: 避免两个测试模块之间产生导入依赖。
BASE_STATE = {
    "session_id": "sess_1",
    "learner_id": "learner_1",
    "trace_id": "trace_1",
    "current_concept": "梯度下降",
    "learning_goal": "理解梯度下降的迭代条件",
}
EVIDENCE = {
    "evidence_id": "doc1#c1@12",
    "document_id": "doc1",
    "chunk_id": "c1",
    "page": 12,
    "text": "梯度下降沿负梯度方向迭代更新参数。",
    "source": "教材A",
}
MODEL_REPLY = "（模型回复）这一步的依据是教材第 12 页。"


def make_port(skill_results: dict | None = None) -> FakeRuntime:
    """绑定表必须显式注入（Runtime 侧不内置教学动作映射，§4.4）。"""
    return FakeRuntime(
        replies=[MODEL_REPLY] * 10,
        skill_results=skill_results or {},
        action_bindings=ACTION_BINDINGS,
    )


def run_turn(port: FakeRuntime, **overrides):
    return asyncio.run(run_teaching_turn(port, {**BASE_STATE, **overrides}))


def assess_decision(port: FakeRuntime) -> dict:
    """取出 Assess 的决策事件负载（复盘字段）。"""
    return next(
        event["payload"]
        for event in port.events
        if event.get("type") == "pedagogy.decision"
        and event["payload"].get("node") == "assess"
    )


def rag_result(status: str, evidence: list[dict] | None = None, note: str = "") -> dict:
    return {
        "status": status,
        "skill": "rag",
        "count": len(evidence or []),
        "evidence": evidence or [],
        "note": note,
    }


# ======================================================================
# 一、证据不足不编造引用
# ======================================================================
def test_teach_without_evidence_gives_no_source_and_no_model_call():
    """RAG 未注册/无命中 → Teach 不调用模型、不给任何来源，并明确说明证据不足。"""
    port = make_port()  # FakeRuntime 默认没有 rag 结果
    result = run_turn(port, user_input="请讲讲什么是梯度下降")
    assert result["action"] == "teach"
    assert "证据不足" in result["response_text"]
    assert result["citations"] == []
    assert result["retrieved_evidence_refs"] == []
    assert result["evidence_sufficient"] is False
    assert port.calls_of("generate") == [], "证据不足时不得调用模型，避免生成不可验证来源"


def test_teach_with_insufficient_evidence_status_gives_no_source():
    """RAG 明确返回 insufficient_evidence 时同样不给来源。"""
    port = make_port({"rag": rag_result("insufficient_evidence", note="无命中")})
    result = run_turn(port, user_input="请讲讲什么是梯度下降")
    assert "证据不足" in result["response_text"]
    assert result["citations"] == []
    assert port.calls_of("generate") == []


def test_correct_without_evidence_gives_no_source():
    """Correct 在证据不足时同样不调用模型、不给出任何引用。"""
    port = make_port()
    result = run_turn(
        port,
        user_input="我觉得方向应该取正的",
        attempt_count=3,
        wrong_streak=3,
        hint_level=HINT_MAX_LEVEL,  # 提示阶梯已用尽，才会转入纠错
        misconceptions=["把下降方向当成正梯度方向"],
    )
    assert result["action"] == "correct"
    assert "证据不足" in result["response_text"]
    assert result["citations"] == []
    assert "把下降方向当成正梯度方向" in result["response_text"]
    assert port.calls_of("generate") == []


def test_teach_with_evidence_returns_locatable_citations_only():
    """有证据时：引用只含定位字段（不含原文），且模型确实被调用。"""
    port = make_port({"rag": rag_result("ok", [dict(EVIDENCE)])})
    result = run_turn(port, user_input="请讲讲什么是梯度下降")
    assert result["response_text"] == MODEL_REPLY
    assert len(result["citations"]) == 1
    citation = result["citations"][0]
    assert citation["document_id"] == "doc1"
    assert citation["chunk_id"] == "c1"
    assert citation["page"] == 12
    assert citation["source"] == "教材A"
    assert "text" not in citation, "状态/引用里不得复制教材原文（§6.4）"
    assert result["retrieved_evidence_refs"] == result["citations"]
    assert port.calls_of("generate")


def test_evidence_missing_locators_is_treated_as_insufficient():
    """RAG 命中缺少 document_id/chunk_id/page 时不可作为引用。"""
    port = make_port(
        {"rag": rag_result("ok", [{"text": "没有定位标识的片段"}])}
    )
    result = run_turn(port, user_input="请讲讲什么是梯度下降")
    assert result["evidence_sufficient"] is False
    assert result["citations"] == []


def test_ask_without_evidence_declares_no_citation():
    """Ask 不引用原文，但证据不足时必须说明"不含引用"。"""
    port = make_port()
    result = run_turn(port, user_input="我觉得可以先求偏导")
    assert result["action"] == "ask"
    assert result["citations"] == []
    assert "不含引用" in result["response_text"]


# ======================================================================
# 二、UpdateProfile 的写入纪律（§5.5）
# ======================================================================
def test_update_profile_writes_audited_memory_records():
    """每条学情记录都要带 source / confidence / revocable / model_version。"""
    port = make_port({"rag": rag_result("ok", [dict(EVIDENCE)])})
    run_turn(port, user_input="请讲讲什么是梯度下降")
    assert port.written_memories, "UpdateProfile 必须写入学情增量"

    for record in port.written_memories:
        assert record["learner_id"] == "learner_1"
        assert record["memory_type"] in ("long_term", "episodic")
        assert str(record["content"]).strip()
        assert str(record["source"]).startswith("graph:update_profile")
        assert "sess_1" in record["source"] and "trace_1" in record["source"]
        assert isinstance(record["confidence"], float)
        assert 0.0 <= record["confidence"] <= 1.0
        assert record["revocable"] is True, "长期记忆必须可撤回（§13.2）"
        assert record["expires_at"] == "", "无过期策略时显式留空，由用户撤回"
        assert record["model_version"] == RULE_MODEL_VERSION
        assert record["metadata"]["graph_source"] == "deepprof.graph.education"

    types = {record["memory_type"] for record in port.written_memories}
    assert types == {"long_term", "episodic"}
    assert len(port.calls_of("write_memory")) == 1, "本轮只写一次，避免碎片化写入"


def test_update_profile_records_misconceptions_separately():
    """疑似错误概念要单独成条，并标明未经学情模型确认。"""
    port = make_port({"rag": rag_result("ok", [dict(EVIDENCE)])})
    run_turn(
        port,
        user_input="我认为方向取正是对的",
        attempt_count=3,
        wrong_streak=3,
        misconceptions=["把下降方向当成正梯度方向"],
    )
    misconception_records = [
        record
        for record in port.written_memories
        if str(record["key"]).startswith("misconception:")
    ]
    assert len(misconception_records) == 1
    record = misconception_records[0]
    assert record["metadata"]["misconception"] == "把下降方向当成正梯度方向"
    assert "未经学情模型确认" in record["content"]
    assert record["confidence"] <= 0.4


def test_update_profile_skips_write_without_learner_id():
    """缺 learner_id 时宁可不写，也不制造无法归属的学情结论（§13.2）。"""
    port = make_port({"rag": rag_result("ok", [dict(EVIDENCE)])})
    result = asyncio.run(
        run_teaching_turn(port, {**BASE_STATE, "learner_id": "", "user_input": "请讲讲什么是梯度下降"})
    )
    assert port.written_memories == []
    assert result["next_action"] == "end"
    reasons = [
        event["payload"].get("reason", "")
        for event in port.events
        if event.get("type") == "pedagogy.decision"
    ]
    assert any("learner_id" in str(reason) for reason in reasons)


def test_update_profile_reads_misconceptions_from_memory():
    """Assess 要把记忆里的历史错误模式合并进状态，供 Correct 判断概念混淆。"""
    port = FakeRuntime(
        replies=["（模型回复）"],
        action_bindings=ACTION_BINDINGS,
        memories=[
            {
                "record_id": "mem_1",
                "learner_id": "learner_1",
                "memory_type": "long_term",
                "key": "misconception:梯度下降:忽略学习率",
                "content": "疑似错误概念：忽略学习率",
                "source": "graph:update_profile",
                "confidence": 0.4,
                "revocable": True,
                "metadata": {"misconception": "忽略学习率"},
            },
            {
                "record_id": "mem_2",
                "learner_id": "learner_1",
                "memory_type": "long_term",
                "key": "misconception:梯度下降:把偏导当全微分",
                "content": "疑似错误概念：把偏导当全微分",
                "source": "graph:update_profile",
                "confidence": 0.4,
                "revocable": True,
                "metadata": {"misconception": "把偏导当全微分"},
            },
        ],
    )
    result = run_turn(port, user_input="我这样理解对吗")
    assert sorted(result["misconceptions"]) == ["忽略学习率", "把偏导当全微分"]
    assert result["action"] == "correct", "两条历史误解应触发概念混淆分支"
    assert result["learner_state_ref"] == "mem_1"
    assert assess_decision(port)["memory_refs"] == ["mem_1", "mem_2"]