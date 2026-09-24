"""教学图分支与退出条件测试（DESIGNv0.6 §6.2 / §7.3 / §16.3 验收）。

覆盖验收要点：
- 六类教学动作分支各至少一条：Teach / Ask / Hint / Correct / Test / Reflect；
- 学生停止时退出，且不再产出教学动作、不再检索；
- 达到 max_turns 不死循环，回退边有界收敛；
- 节点事件 schema 完整且类型与 runtime 的 EventType 一致。

同步测试内部用 asyncio.run 包一层，不依赖 pytest-asyncio 的 asyncio_mode 配置。
"""
from __future__ import annotations

import asyncio

import pytest

from graph.education.bindings import ACTION_BINDINGS
from graph.education.builder import build_education_graph, run_teaching_turn
from graph.education.nodes import (
    EVENT_DECISION,
    EVENT_NODE_ENTERED,
    EVENT_NODE_EXITED,
    GRAPH_SOURCE,
)
from graph.education.policies import (
    HINT_LEVEL_TEMPLATES,
    HINT_MAX_LEVEL,
    MAX_TURNS_DEFAULT,
    PRIOR_GAP_TEACH_NOTE,
    PRIOR_OK_TEACH_NOTE,
)
from runtime.core.events import EventType
from runtime.testing import FakeRuntime

#: 一条可定位的教材证据（§18.2 Evidence 契约）
EVIDENCE = {
    "evidence_id": "doc1#c1@12",
    "document_id": "doc1",
    "chunk_id": "c1",
    "page": 12,
    "text": "梯度下降沿负梯度方向迭代更新参数。",
    "source": "教材A",
}

MODEL_REPLY = "（模型回复）先看你的思路：第一步你会先确定什么？"

BASE_STATE = {
    "session_id": "sess_1",
    "learner_id": "learner_1",
    "trace_id": "trace_1",
    "current_concept": "梯度下降",
    "learning_goal": "理解梯度下降的迭代条件",
}


def make_port(*, with_evidence: bool = True) -> FakeRuntime:
    """构造 FakeRuntime；with_evidence 控制 RAG 是否有可定位命中。

    绑定表必须显式注入：Runtime 侧不内置任何教学动作 → 能力的映射（§4.4），
    真实装配由组合根 api/app.py 完成；不注入的话节点会拿到 status=no_binding。
    """
    skill_results = {}
    if with_evidence:
        skill_results["rag"] = {
            "status": "ok",
            "skill": "rag",
            "count": 1,
            "evidence": [dict(EVIDENCE)],
        }
    return FakeRuntime(
        replies=[MODEL_REPLY] * 20,
        skill_results=skill_results,
        action_bindings=ACTION_BINDINGS,
    )


def run_turn(port: FakeRuntime, **overrides):
    return asyncio.run(run_teaching_turn(port, {**BASE_STATE, **overrides}))


def entered_nodes(port: FakeRuntime) -> list[str]:
    return [
        str(event["payload"].get("node"))
        for event in port.events
        if event.get("type") == EVENT_NODE_ENTERED
    ]


# ======================================================================
# 六类教学动作分支
# ======================================================================
def test_teach_branch_when_student_asks_for_explanation():
    """主动求讲解 + 有证据 → Teach，并带上可定位引用。"""
    port = make_port(with_evidence=True)
    result = run_turn(port, user_input="请讲讲什么是梯度下降")
    assert result["action"] == "teach"
    assert result["response_text"] == MODEL_REPLY
    assert result["citations"], "有证据时应当给出引用"
    assert "证据不足" not in result["response_text"]
    assert entered_nodes(port) == ["assess", "teach"]


def test_teach_branch_when_prior_knowledge_missing():
    """先验不足（学生自述前置概念没学过）→ Teach 讲前置概念，而不是苏格拉底追问。

    §16.3 教学样例之二。与"主动求讲解"的区别在**起讲点**：
    求讲解是学生想听这个知识点，先验不足是前置概念还没学——
    此时追问等于要求他推理还没学过的内容，只会强化挫败。
    """
    port = make_port(with_evidence=True)
    result = run_turn(port, user_input="我没学过前面的导数，这块还能听吗")

    assert result["action"] == "teach"
    assert entered_nodes(port) == ["assess", "teach"]

    assess_decision = next(
        event["payload"]
        for event in port.events
        if event.get("type") == EVENT_DECISION and event["payload"]["node"] == "assess"
    )
    assert assess_decision["prior_knowledge_gap"] is True
    assert "先验" in assess_decision["reason"]

    # 起讲点真的前移了：送给模型的提示词要求先补前置概念，而不是精简基础步骤
    prompt = port.calls_of("generate")[0]["request"]["messages"][1]["content"]
    assert PRIOR_GAP_TEACH_NOTE in prompt
    assert PRIOR_OK_TEACH_NOTE not in prompt


def test_ask_branch_by_default():
    """学生给出推理但无错误轨迹 → Ask（苏格拉底追问）。"""
    port = make_port(with_evidence=True)
    result = run_turn(port, user_input="我觉得可以先求偏导再更新参数")
    assert result["action"] == "ask"
    assert result["emotion"] == "curious"
    assert result["citations"] == []
    assert entered_nodes(port) == ["assess", "ask"]


def test_hint_branch_escalates_level_on_wrong_streak():
    """尝试受阻 → Hint，并按"连续答错升级提示"给出更重一级提示。"""
    port = make_port(with_evidence=True)
    result = run_turn(port, user_input="还是算不出来", attempt_count=1, wrong_streak=1, hint_level=1)
    assert result["action"] == "hint"
    assert result["hint_level"] == 2, "连续答错应当把提示升级到第 2 级"
    assert result["response_text"] == HINT_LEVEL_TEMPLATES[2].format(concept="梯度下降")


def test_hint_level_is_capped_and_never_leaks_answer():
    """提示级别封顶在 HINT_MAX_LEVEL，且不会出现最终答案。"""
    port = make_port(with_evidence=True)
    result = run_turn(port, user_input="还是不对", attempt_count=4, wrong_streak=1, hint_level=HINT_MAX_LEVEL)
    assert result["action"] == "reflect"
    assert result["hint_level"] == HINT_MAX_LEVEL
    assert "最终答案" not in result["response_text"]


def test_hint_ladder_reaches_every_level_before_correcting():
    """提示阶梯必须能真实走完全部档位，且纠错只在阶梯用尽后介入。

    防回归：若 Correct 只看 wrong_streak、不看提示是否走完，
    高等级提示模板就会变成永远走不到的死代码（定义了却走不到）。
    这里逐轮让状态自然演进，而不是直接注入 hint_level——
    注入式断言对可达性无感知，正是它掩盖过该缺陷。
    """
    port = make_port(with_evidence=True)
    state = {
        **BASE_STATE,
        "user_input": "还是推不出来",
        "wrong_streak": 0,
        "turn_count": 0,
        "max_turns": 99,  # 隔离"提示阶梯"这一个变量，避免被轮次上限打断
    }

    seen: list[int] = []
    for _ in range(HINT_MAX_LEVEL):
        state["wrong_streak"] += 1  # 本轮又答错
        state = asyncio.run(run_teaching_turn(port, state))
        assert state["action"] == "hint", f"提示未走完就不应纠错，实际为 {state['action']}"
        seen.append(int(state["hint_level"]))

    assert seen == list(range(1, HINT_MAX_LEVEL + 1)), f"提示级别未逐级升到顶：{seen}"
    assert state["response_text"] == HINT_LEVEL_TEMPLATES[HINT_MAX_LEVEL].format(
        concept="梯度下降"
    )

    # 阶梯用尽后继续答错，才转入纠错
    state["wrong_streak"] += 1
    state = asyncio.run(run_teaching_turn(port, state))
    assert state["action"] == "correct", "提示阶梯用尽后应转入纠错"


def test_correct_branch_on_stable_error():
    """连续答错达到阈值 → Correct，指出冲突并重置错误计数。"""
    port = make_port(with_evidence=True)
    result = run_turn(
        port,
        user_input="我认为梯度下降是沿正梯度方向走",
        attempt_count=3,
        wrong_streak=3,
        hint_level=3,
        misconceptions=["把下降方向当成正梯度方向"],
    )
    assert result["action"] == "correct"
    assert result["citations"], "有证据的纠错应当给出引用"
    assert result["wrong_streak"] == 0 and result["hint_level"] == 0, "纠错后清零连续错误与提示级别"
    assert entered_nodes(port) == ["assess", "correct"]


def test_correct_branch_on_misconception_confusion():
    """误解条目达到阈值 → Correct（不依赖连续答错计数）。"""
    port = make_port(with_evidence=True)
    result = run_turn(
        port,
        user_input="我这样理解对吗",
        misconceptions=["把偏导当成全微分", "忽略学习率的量纲"],
    )
    assert result["action"] == "correct"


def test_test_branch_reports_missing_bank_after_enough_attempts():
    """题库未配置时 Test 只显式报告缺口，不能生成替代题或判分。"""
    port = make_port(with_evidence=True)
    result = run_turn(port, user_input="我的答案是沿负梯度更新", attempt_count=3, wrong_streak=0)
    assert result["action"] == "test"
    assert result["attempt_count"] == 3, "缺少题库与可靠判分时不累计作答事实"
    assert result["wrong_streak"] == 0, "缺少可靠判分时不得当成答错"
    assert entered_nodes(port) == ["assess", "test"]
    assert "题库未配置" in result["response_text"]


@pytest.mark.parametrize(
    ("mastery", "item_id", "expected"),
    [
        (0.299, "", "teach"),
        (0.30, "", "ask"),
        (0.59, "active-item", "hint"),
        (0.60, "", "ask"),
        (0.849, "", "ask"),
        (0.85, "", "test"),
    ],
)
def test_c_group_mastery_ranges_choose_documented_action(mastery, item_id, expected):
    result = run_turn(make_port(with_evidence=True), user_input="继续检查这个知识点",
                      current_concept_id="DS-LIN-01", experiment_group="C",
                      learner_estimate={"status": "available", "mastery": mastery,
                                        "model_version": "bkt-four-parameter-dev-v1", "evidence_count": 3},
                      item_id=item_id)
    assert result["action"] == expected


def test_c_group_keeps_cold_start_and_corrects_repeated_errors_before_mastery_override():
    cold = run_turn(make_port(), user_input="先检查一下", current_concept_id="DS-LIN-01",
                    experiment_group="C", learner_estimate={"status": "insufficient_data", "mastery": None,
                                                              "evidence_count": 2})
    assert cold["action"] == "ask"

    corrected = run_turn(make_port(), user_input="这条推理还是不对吗", current_concept_id="DS-LIN-01",
                         experiment_group="C", learner_estimate={"status": "available", "mastery": 0.9,
                                                                  "evidence_count": 4}, wrong_streak=2,
                         hint_level=3, attempt_count=3, misconceptions=["概念条件混淆"])
    assert corrected["action"] == "correct"


def test_reflect_branch_at_turn_limit():
    """达到最大轮次 → Reflect，并给出策略调整建议。"""
    port = make_port(with_evidence=True)
    result = run_turn(port, user_input="还是不会", turn_count=MAX_TURNS_DEFAULT)
    assert result["action"] == "reflect"
    assert result["next_action"] == "end"
    assert "回退" in result["response_text"] and "老师" in result["response_text"]
    assert entered_nodes(port).count("assess") == 1


# ======================================================================
# 退出条件与防无限追问
# ======================================================================
def test_student_stopped_exits_without_teaching_action():
    """学生停止 → 直接 END：不进入任何教学动作节点，也不发起检索。"""
    port = make_port(with_evidence=True)
    result = run_turn(port, user_input="我不想学了", student_stopped=True)
    assert result["action"] == "end"
    assert "本轮先到这里" in result["response_text"]
    assert entered_nodes(port) == ["assess"]
    invoked = [call["name"] for call in port.calls_of("invoke_skill")]
    assert "rag" not in invoked, "已停止时不应再检索教材"


def test_test_does_not_infer_grading_or_loop_back():
    """没有可靠判分时，Test 结束本轮且不伪造错误序列。"""
    port = make_port(with_evidence=True)
    result = run_turn(
        port,
        user_input="这样对吗",
        attempt_count=3,
        wrong_streak=0,
        last_answer_correct=False,
        hint_level=0,
    )
    nodes = entered_nodes(port)
    assert nodes == ["assess", "test"]
    assert result["action"] == "test"
    assert result["wrong_streak"] == 0


def test_max_turns_never_loops_forever():
    """连续多轮都无进展时：turn_count 有界、单轮内 Assess 进入次数有界、图必然收敛。"""
    port = make_port(with_evidence=True)
    max_turns = 3
    state = {**BASE_STATE, "user_input": "我不会", "max_turns": max_turns}
    for _ in range(10):
        before = entered_nodes(port).count("assess")
        state = asyncio.run(run_teaching_turn(port, state))
        assert entered_nodes(port).count("assess") - before <= 1
        assert state["turn_count"] <= max_turns, "turn_count 不得越过 max_turns"
        assert state["action"] in ("teach", "ask", "hint", "correct", "test", "reflect", "end")
    assert state["action"] == "reflect" or state["next_action"] == "end"
    assert state["turn_count"] == max_turns, "达到上限后 turn_count 不再增长"
    assert len(port.events) < 200, "事件数量必须有界，说明没有死循环"


def test_build_education_graph_returns_compiled_graph():
    """入口一：build_education_graph 返回编译后的图。"""
    graph = build_education_graph(make_port())
    assert hasattr(graph, "ainvoke")


# ======================================================================
# 事件 schema（可复盘、可解释性分析的基础）
# ======================================================================
def test_pedagogy_events_schema_and_types():
    """每个节点进出与决策都要发事件，且类型与 runtime EventType 一致。"""
    port = make_port(with_evidence=True)
    run_turn(port, user_input="请讲讲什么是梯度下降")
    types = set(port.event_types())
    assert types <= {EVENT_NODE_ENTERED, EVENT_DECISION, EVENT_NODE_EXITED}
    for event in port.events:
        assert event["source"] == GRAPH_SOURCE
        assert event["session_id"] == "sess_1"
        assert event["trace_id"] == "trace_1"
        assert isinstance(event["payload"], dict)
        assert event["payload"].get("node")
    assert EVENT_NODE_ENTERED == EventType.PEDAGOGY_NODE_ENTERED.value
    assert EVENT_DECISION == EventType.PEDAGOGY_DECISION.value
    assert EVENT_NODE_EXITED == EventType.PEDAGOGY_NODE_EXITED.value


def test_decision_event_carries_decision_evidence():
    """决策事件必须带 action / reason / 证据与尝试次数，便于复盘与实验对比。"""
    port = make_port(with_evidence=True)
    run_turn(port, user_input="请讲讲什么是梯度下降")
    decisions = [
        event["payload"]
        for event in port.events
        if event.get("type") == EVENT_DECISION
    ]
    assess_decision = next(payload for payload in decisions if payload["node"] == "assess")
    assert assess_decision["action"] == "teach"
    assert assess_decision["reason"]
    assert assess_decision["evidence_count"] == 1
    assert assess_decision["evidence_sufficient"] is True
    assert assess_decision["max_turns"] == MAX_TURNS_DEFAULT
    teach_decision = next(payload for payload in decisions if payload["node"] == "teach")
    assert teach_decision["source_attached"] is True
    assert teach_decision["citation_count"] == 1
