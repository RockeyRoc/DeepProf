"""作答事实（Attempt）产出测试（DESIGNv0.4 §16.3 / §18.2 / §13.1）。

要证明的命题：教育组在 Test / Correct 节点把一次作答按契约交给数据组，
且**只在"归属明确 + 判分可靠 + 题目可追踪"三条同时成立时**才产出——
任一不满足就显式跳过，并在决策事件里写明原因。

三条纪律（§13.1 / §18.2）：
- 判分缺失（None）不得当成答错，也不得写进学情；
- 题目不可追踪（题库未接入 → item_id 为空）宁可不产出，也不编造 id；
- 无法归属到 learner_id 的作答会污染别人的画像，一律不产出。

同步测试内部用 asyncio.run 包一层，不依赖 pytest-asyncio 配置。
"""
from __future__ import annotations

import asyncio

from graph.education.bindings import ACTION_BINDINGS
from graph.education.builder import run_teaching_turn
from graph.education.nodes import EVENT_ATTEMPT, EVENT_DECISION
from graph.education.nodes import emit_attempt
from graph.education.nodes.correct import correct
from graph.education.nodes.test import test as quiz_node
from graph.education.state import new_state
from models.learner import Attempt
from runtime.core.events import EventType
from runtime.testing import FakeRuntime

EVIDENCE = {
    "evidence_id": "doc1#c1@12",
    "document_id": "doc1",
    "chunk_id": "c1",
    "page": 12,
    "text": "梯度下降沿负梯度方向迭代更新参数。",
    "source": "教材A",
}

BASE_STATE = {
    "session_id": "sess_1",
    "learner_id": "learner_1",
    "trace_id": "trace_1",
    "current_concept": "梯度下降",
    "learning_goal": "理解梯度下降的迭代条件",
    "attempt_count": 3,
    "wrong_streak": 0,
}

QUIZ_RESULT = {
    "status": "ok",
    "skill": "quiz",
    "item": {"stem": "请说明梯度下降的更新方向。"},
    "evaluation": {"feedback": "先回到定义核对更新方向。"},
    "item_bank_connected": False,
}


def make_port() -> FakeRuntime:
    """FakeRuntime：带一条可定位教材证据与 Quiz 结果；绑定表显式注入（§4.4）。"""
    return FakeRuntime(
        replies=["（模型回复）先看你的思路。"],
        skill_results={"rag": {"status": "ok", "skill": "rag", "count": 1, "evidence": [dict(EVIDENCE)]},
                       "quiz": dict(QUIZ_RESULT)},
        action_bindings=ACTION_BINDINGS,
    )


def attempt_events(port: FakeRuntime) -> list[dict]:
    return [event for event in port.events if event.get("type") == EVENT_ATTEMPT]


def decision_of(port: FakeRuntime, node: str) -> dict:
    return next(
        event["payload"]
        for event in port.events
        if event.get("type") == EVENT_DECISION and event["payload"]["node"] == node
    )


# ======================================================================
# 一、闸门本身：policies.attempt_gate 的三条必要条件
# ======================================================================
def test_emit_attempt_records_when_all_conditions_met():
    """归属明确 + 判分可靠 + 题目可追踪 → 发出一条合法的 Attempt。"""
    port = make_port()
    state = new_state(**BASE_STATE, item_id="item_001", hint_level=2)
    payload, skip_reason = asyncio.run(emit_attempt(port, state, correct=True))

    assert skip_reason == ""
    assert payload is not None
    # 契约由数据组定义：字段漂移在这里就要报错，而不是写进数据组仓库才发现（§18.2）
    restored = Attempt.model_validate(payload)
    assert restored.learner_id == "learner_1"
    assert restored.item_id == "item_001"
    assert restored.correct is True
    assert restored.concept_ids == ["梯度下降"]
    assert restored.hint_count == 2

    events = attempt_events(port)
    assert len(events) == 1
    # Attempt 本身不带 session_id / trace_id，两条事实靠事件外层字段对齐（§18.2）
    assert events[0]["session_id"] == "sess_1"
    assert events[0]["trace_id"] == "trace_1"
    assert events[0]["payload"]["attempt_id"] == payload["attempt_id"]


def test_emit_attempt_skips_without_trackable_item():
    """题库未接入 → 没有 item_id → 不产出，也不编造无法与题库对齐的 id（§18.2）。"""
    port = make_port()
    state = new_state(**BASE_STATE, item_id="")
    payload, skip_reason = asyncio.run(emit_attempt(port, state, correct=True))

    assert payload is None
    assert "item_id" in skip_reason
    assert attempt_events(port) == []


def test_emit_attempt_skips_without_reliable_judgement():
    """判分缺失（None）→ 不产出：不得把"判不了"学成"答错了"（§13.1）。"""
    port = make_port()
    state = new_state(**BASE_STATE, item_id="item_001")
    payload, skip_reason = asyncio.run(emit_attempt(port, state, correct=None))

    assert payload is None
    assert "判分" in skip_reason
    assert attempt_events(port) == []


def test_emit_attempt_skips_without_learner_identity():
    """缺少 learner_id → 无法归属 → 不产出，避免跨学习者污染（§13.2）。"""
    port = make_port()
    state = new_state(**{**BASE_STATE, "learner_id": ""}, item_id="item_001")
    payload, skip_reason = asyncio.run(emit_attempt(port, state, correct=True))

    assert payload is None
    assert "learner_id" in skip_reason
    assert attempt_events(port) == []


def test_attempt_event_type_matches_runtime_enum():
    """事件类型与 runtime 的 EventType 冻结契约一致（§5.4 / §18.2）。"""
    assert EVENT_ATTEMPT == EventType.PEDAGOGY_ATTEMPT.value


# ======================================================================
# 二、节点产出：Test / Correct 都交出作答事实，并写明"这一轮为什么没产出"
# ======================================================================
def test_test_node_records_attempt_and_reports_it():
    """Test 评价作答且题目可追踪 → 产出 Attempt，决策事件记录 attempt_id。"""
    port = make_port()
    state = new_state(
        **BASE_STATE,
        user_input="我的答案是沿负梯度方向更新",
        item_id="item_001",
        last_answer_correct=True,
    )
    asyncio.run(quiz_node(state, port))

    events = attempt_events(port)
    assert len(events) == 1, "判对且有 item_id 时应当产出一条作答事实"
    assert events[0]["payload"]["correct"] is True

    decision = decision_of(port, "test")
    assert decision["attempt_recorded"] is True
    assert decision["attempt_id"] == events[0]["payload"]["attempt_id"]
    assert decision["attempt_skip_reason"] == ""


def test_test_node_skips_attempt_when_item_bank_absent():
    """当前生产真实状态：题库未接入（item_id 为空）→ 不产出，并写明原因。"""
    port = make_port()
    state = new_state(
        **BASE_STATE,
        user_input="我的答案是沿负梯度方向更新",
        last_answer_correct=True,
    )
    asyncio.run(quiz_node(state, port))

    assert attempt_events(port) == []
    decision = decision_of(port, "test")
    assert decision["attempt_recorded"] is False
    assert "item_id" in decision["attempt_skip_reason"], "跳过必须给出显式原因，不能静默"


def test_test_node_skips_attempt_when_judgement_missing():
    """判分缺失 → 不产出；原因指向"缺少可靠判分"而不是"答错"（§13.1）。"""
    port = make_port()
    state = new_state(
        **BASE_STATE,
        user_input="我不确定这样对不对",
        item_id="item_001",
        last_answer_correct=None,
    )
    asyncio.run(quiz_node(state, port))

    assert attempt_events(port) == []
    decision = decision_of(port, "test")
    assert "判分" in decision["attempt_skip_reason"]


def test_correct_node_records_attempt_from_reliable_judgement():
    """Correct 同样产出作答事实，但只认可靠的 last_answer_correct。"""
    port = make_port()
    state = new_state(
        **{
            **BASE_STATE,
            "user_input": "我认为是沿正梯度方向走",
            "item_id": "item_002",
            "last_answer_correct": False,
            "wrong_streak": 3,
            "hint_level": 3,
            "misconceptions": ["把下降方向当成正梯度方向"],
        }
    )
    asyncio.run(correct(state, port))

    events = attempt_events(port)
    assert len(events) == 1
    assert events[0]["payload"]["correct"] is False
    assert events[0]["payload"]["item_id"] == "item_002"

    decision = decision_of(port, "correct")
    assert decision["attempt_recorded"] is True


def test_correct_node_skips_attempt_when_judgement_missing():
    """Correct 不拿"连续答错次数"反推一条作答事实（§13.1 不贴永久标签）。"""
    port = make_port()
    state = new_state(
        **BASE_STATE,
        user_input="我这样理解对吗",
        item_id="item_002",
        last_answer_correct=None,
        misconceptions=["把偏导当成全微分", "忽略学习率的量纲"],
    )
    asyncio.run(correct(state, port))

    assert attempt_events(port) == []
    assert decision_of(port, "correct")["attempt_recorded"] is False


# ======================================================================
# 三、整轮通路：图跑完后事件里能看到作答事实
# ======================================================================
def test_full_turn_emits_attempt_event_for_trackable_item():
    """整轮 Test 分支：题目可追踪时，作答事实随事件落盘供数据组消费（§18.2）。"""
    port = make_port()
    result = asyncio.run(
        run_teaching_turn(
            port,
            {
                **BASE_STATE,
                "user_input": "我的答案是沿负梯度方向更新",
                "item_id": "item_007",
                "last_answer_correct": True,
            },
        )
    )

    assert result["action"] == "test"
    events = attempt_events(port)
    assert len(events) == 1
    payload = Attempt.model_validate(events[0]["payload"])
    assert payload.item_id == "item_007"
    assert payload.correct is True
    assert payload.learner_id == "learner_1"