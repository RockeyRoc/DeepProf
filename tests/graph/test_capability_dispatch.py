"""声明式决策分发测试（DESIGNv0.4 §4.4 / §6.2 / §6.3 / §7.1）。

要证明的命题：教学图只要产出 `PedagogicalDecision`，Runtime 就能把它执行出
与教学策略一致的结果——而且 Runtime 里没有一行教学动作分支。

三部分：
- **分发器行为**（直接调 `RuntimeService.execute`）：占位解析、证据前置条件、
  确定性兜底、装配失败要响亮地错；
- **节点改判对照**：节点产出的正文必须与"同一条决策直接交给 Runtime 执行"
  完全一致（节点已瘦身成"只产出决策"，因此两条路径应当同源）；
- **节点瘦身守卫**：节点源码里不得再出现提示模板、提示词拼装、证据获取调用。
"""
from __future__ import annotations

import asyncio
import re
from pathlib import Path
from typing import Any

import pytest

from api.app import create_app
from graph.education.bindings import ACTION_BINDINGS
from graph.education.builder import run_teaching_turn
from graph.education.contracts import (
    STATUS_CAPABILITY_NOT_FOUND,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_INVALID_REQUEST,
    STATUS_NO_BINDING,
    STATUS_SUCCESS,
    CapabilityResult,
    PedagogicalDecision,
)
from graph.education.nodes.ask import ask
from graph.education.nodes.assess import assess
from graph.education.nodes.correct import correct
from graph.education.nodes.hint import hint
from graph.education.nodes.reflect import reflect
from graph.education.nodes.teach import teach
from graph.education.nodes.test import test as quiz_node
from graph.education.nodes.update_profile import update_profile
from graph.education.policies import (
    ACTION_ASK,
    ACTION_ASSESS,
    ACTION_CORRECT,
    ACTION_DIAGNOSE,
    ACTION_END,
    ACTION_HINT,
    ACTION_NODES,
    ACTION_RECALL,
    ACTION_REFLECT,
    ACTION_TEACH,
    ACTION_TEST,
    ACTION_UPDATE_PROFILE,
    ASK_FALLBACK_QUESTION,
    ASK_NO_EVIDENCE_NOTE,
    CORRECT_INSUFFICIENT_EVIDENCE_TEXT,
    END_ACTIONS,
    GENERATION_FAILED_TEXT,
    HINT_LEVEL_TEMPLATES,
    QUIZ_FALLBACK_ITEM,
    QUIZ_FRAME_CORRECT,
    QUIZ_FRAME_INCORRECT,
    QUIZ_FRAME_UNKNOWN,
    QUIZ_ITEM_PREFIX,
    QUIZ_NO_JUDGEMENT_NOTE,
    QUIZ_NOT_CONNECTED_NOTE,
    REFLECT_STRATEGY_OPTIONS,
    REFLECT_TEXT,
    REPLY_FRAME_QUIZ,
    REPLY_FRAME_UNKNOWN,
    STOPPED_TEXT,
    TEACH_INSUFFICIENT_EVIDENCE_TEXT,
    next_hint_level,
    teach_prior_note,
)
from graph.education.state import new_state
from runtime.providers.fake import FakeProvider
from runtime.sandbox.policy import SandboxPolicy
from runtime.service import RuntimeService
from runtime.skills import Skill, SkillDescriptor, SkillRegistry
from runtime.storage.sqlite_store import SqliteDatabase
from runtime.testing import FakeRuntime

BASE_STATE = {
    "session_id": "sess_1",
    "learner_id": "learner_1",
    "trace_id": "trace_1",
    "current_concept": "梯度下降",
    "learning_goal": "理解梯度下降的迭代条件",
}
CTX = {
    "session_id": "sess_1",
    "learner_id": "learner_1",
    "trace_id": "trace_1",
    "source": "deepprof.graph.education",
    "metadata": {"graph": "education"},
}
QUERY = "请讲讲什么是梯度下降"
STUDENT_INPUT = "我觉得可以先求偏导"
MODEL_REPLY = "（模型回复）这一步的依据是教材第 12 页。"
EVIDENCE = {
    "evidence_id": "doc1#c1@12",
    "document_id": "doc1",
    "chunk_id": "c1",
    "page": 12,
    "text": "梯度下降沿负梯度方向迭代更新参数。",
    "source": "教材A",
}


# ======================================================================
# 测试替身
# ======================================================================
class _StubSkill(Skill):
    """固定返回某个结果的 Skill 桩，并记录收到的 payload（用于断言参数映射）。"""

    def __init__(self, name: str, result: dict) -> None:
        self.descriptor = SkillDescriptor(name=name, description="测试桩")
        self._result = dict(result)
        self.payloads: list[dict] = []

    async def handle(self, payload, ctx, port):  # type: ignore[override]
        self.payloads.append(dict(payload or {}))
        return dict(self._result)

    @property
    def last_payload(self) -> dict:
        return self.payloads[-1] if self.payloads else {}


def rag_result(status: str, evidence: list[dict] | None = None) -> dict:
    return {
        "status": status,
        "skill": "rag",
        "count": len(evidence or []),
        "evidence": evidence or [],
    }


def make_service(
    skill_results: dict[str, dict] | None = None,
    *,
    script: list[dict] | None = None,
    bindings: dict[str, dict] | None = None,
) -> RuntimeService:
    """装配一个只含测试桩 Skill 的 RuntimeService（不触网）。"""
    registry = SkillRegistry()
    for name, result in (skill_results or {}).items():
        registry.register(_StubSkill(name, result))
    return RuntimeService(
        provider=FakeProvider(script=script or []),
        db=SqliteDatabase(":memory:"),
        sandbox=SandboxPolicy.from_settings(),
        skill_registry=registry,
        action_bindings=ACTION_BINDINGS if bindings is None else bindings,
    )


def node_port(skill_results: dict[str, dict] | None = None, **kwargs) -> FakeRuntime:
    """图侧测试用的 FakeRuntime：显式注入绑定表（Runtime 侧不内置，§4.4）。"""
    return FakeRuntime(
        skill_results=skill_results or {},
        action_bindings=ACTION_BINDINGS,
        **kwargs,
    )


def run_turn(port: FakeRuntime, **overrides):
    """跑一轮完整教学图（用于验证跨节点行为，如"同一轮只检索一次"）。"""
    return asyncio.run(run_teaching_turn(port, {**BASE_STATE, **overrides}))


def ask_decision(evidence_sufficient: bool = False) -> dict:
    return PedagogicalDecision(
        action=ACTION_ASK,
        concept=BASE_STATE["current_concept"],
        level=2,
        evidence_sufficient=evidence_sufficient,
        params={
            "learning_goal": BASE_STATE["learning_goal"],
            "user_input": STUDENT_INPUT,
            "attempt_count": 2,
        },
        reason="学生具备推理基础，转入追问",
    ).to_dict()


def teach_decision(evidence_sufficient: bool = True) -> dict:
    return PedagogicalDecision(
        action=ACTION_TEACH,
        concept=BASE_STATE["current_concept"],
        require_evidence=True,
        evidence_sufficient=evidence_sufficient,
        params={
            "learning_goal": BASE_STATE["learning_goal"],
            "user_input": QUERY,
            "query": QUERY,
            # 起讲点说明：Teach 的提示词按它分层（先验不足 / 先验正常），
            # 由 policies.teach_prior_note 产出（§16.3 先验不足样例）
            "prior_gap_note": teach_prior_note(False),
        },
        reason="概念缺失且适合直接解释",
    ).to_dict()


# ======================================================================
# 一、两份契约本身
# ======================================================================
def test_decision_roundtrip_ignores_unknown_keys():
    decision = PedagogicalDecision(
        action=ACTION_HINT,
        concept="TCP 三次握手",
        level=2,
        reveal_answer=False,
        require_evidence=True,
        require_student_reply=True,
        evidence_sufficient=False,
        params={"attempt_count": 2},
        reason="连续答错升级提示",
    )
    payload = decision.to_dict()

    assert PedagogicalDecision.from_dict(payload) == decision
    assert PedagogicalDecision.from_dict({**payload, "future_field": 1}) == decision
    assert PedagogicalDecision.from_dict(None).action == ""


def test_capability_result_roundtrip_keeps_status_and_locators():
    result = CapabilityResult(
        content="先回到定义。",
        evidence=[dict(EVIDENCE)],
        status=STATUS_SUCCESS,
        action=ACTION_HINT,
        capability="render_template",
        metadata={"key": "2"},
    )
    payload = result.to_dict()

    restored = CapabilityResult.from_dict(payload)
    assert restored == result
    assert restored.ok is True
    assert CapabilityResult.from_dict({"status": STATUS_INSUFFICIENT_EVIDENCE}).ok is False
    assert CapabilityResult.from_dict(None) == CapabilityResult()


# ======================================================================
# 二、Hint：确定性渲染，不得经过模型
# ======================================================================
def test_hint_dispatch_matches_node_and_never_calls_model():
    state = new_state(**BASE_STATE, hint_level=1, wrong_streak=1)
    port = node_port()
    node_result = asyncio.run(hint(state, port))
    level = next_hint_level(1, 1)

    service = make_service()
    result = asyncio.run(
        service.execute(
            PedagogicalDecision(
                action=ACTION_HINT,
                concept=BASE_STATE["current_concept"],
                level=level,
                reason="尝试受阻，给一级提示",
            ).to_dict(),
            CTX,
        )
    )

    assert result["status"] == STATUS_SUCCESS
    assert result["capability"] == "render_template"
    assert result["content"] == node_result["response_text"]
    assert result["content"] == HINT_LEVEL_TEMPLATES[level].format(
        concept=BASE_STATE["current_concept"]
    )
    assert result["evidence"] == []
    # 提示强度是教学实验的可控自变量：走模型就会被措辞噪声污染（§3.4）
    assert service.provider.calls == [], "Hint 必须确定性渲染，不得经过模型"
    assert port.calls_of("generate") == [], "图侧同样不得为提示调用模型"


def test_hint_dispatch_reports_missing_template():
    service = make_service(
        bindings={
            ACTION_HINT: {
                "capability": "render_template",
                "params": {"templates": {"1": "只问定义"}, "select": "level"},
            }
        }
    )
    result = asyncio.run(
        service.execute(
            PedagogicalDecision(action=ACTION_HINT, concept="梯度下降", level=3).to_dict(), CTX
        )
    )

    assert result["status"] == "error"
    assert result["error"]["code"] == "template_not_found"


# ======================================================================
# 三、Ask：Skill 优先，策略模板兜底，证据不足时说明"不含引用"
# ======================================================================
def test_ask_dispatch_matches_node_and_maps_typed_params():
    skill_result = {
        "status": "ok",
        "skill": "socratic",
        "question": "你觉得第一步该先确定什么？",
        "source": "model_generated",
    }
    state = new_state(
        **BASE_STATE, evidence_sufficient=False, hint_level=2, attempt_count=2, user_input=STUDENT_INPUT
    )
    node_result = asyncio.run(ask(state, node_port({"socratic": skill_result})))

    service = make_service({"socratic": skill_result})
    result = asyncio.run(service.execute(ask_decision(), CTX))

    assert result["status"] == STATUS_SUCCESS
    assert result["content"] == node_result["response_text"]
    assert result["content"] == f"{skill_result['question']}\n{ASK_NO_EVIDENCE_NOTE}"
    assert result["metadata"]["skill"] == "socratic"

    # ${...} 整串占位必须保留原类型，否则 Skill 侧 int() 会炸
    payload = service.skills.get("socratic").last_payload  # type: ignore[attr-defined]
    assert payload["hint_level"] == 2 and isinstance(payload["hint_level"], int)
    assert payload["prior_attempts"] == 2 and isinstance(payload["prior_attempts"], int)
    assert payload["concept"] == BASE_STATE["current_concept"]
    assert payload["avoid_answer"] is True
    assert service.provider.calls == [], "Ask 走 Skill，不直接调 Provider"


def test_ask_dispatch_falls_back_to_policy_template_when_skill_missing():
    state = new_state(**BASE_STATE, evidence_sufficient=False, hint_level=2, attempt_count=2)
    # FakeRuntime 默认的 socratic 结果里没有 question 字段 → 分发器走兜底
    node_result = asyncio.run(ask(state, node_port()))

    service = make_service()  # 没有注册 socratic → skill_not_found
    result = asyncio.run(service.execute(ask_decision(), CTX))

    assert result["status"] == STATUS_SUCCESS
    assert result["content"] == node_result["response_text"]
    assert result["content"] == (
        ASK_FALLBACK_QUESTION.format(concept=BASE_STATE["current_concept"])
        + "\n"
        + ASK_NO_EVIDENCE_NOTE
    )
    assert result["metadata"]["degraded_from"] == "error"
    assert node_result["strategy_note"] == "ask:policy_template"


# ======================================================================
# 四、Teach：先取证再生成；无证据不调用模型
# ======================================================================
def test_teach_declared_insufficient_skips_retrieval_and_model():
    state = new_state(**BASE_STATE, evidence_sufficient=False, user_input=QUERY)
    port = node_port({"rag": rag_result("ok", [dict(EVIDENCE)])})
    node_result = asyncio.run(teach(state, port))

    service = make_service({"rag": rag_result("ok", [dict(EVIDENCE)])}, script=[{"content": MODEL_REPLY}])
    result = asyncio.run(service.execute(teach_decision(evidence_sufficient=False), CTX))

    assert result["status"] == STATUS_INSUFFICIENT_EVIDENCE
    assert result["content"] == node_result["response_text"] == TEACH_INSUFFICIENT_EVIDENCE_TEXT
    assert result["evidence"] == []
    # 策略层已判定无证据：连检索都不发起（“没证据也不硬找”）
    assert port.calls_of("invoke_skill") == []
    assert port.calls_of("generate") == []
    assert service.provider.calls == [], "证据不足时禁止调用模型（§7.3 不编造引用）"


def test_teach_with_unlocatable_evidence_matches_node_and_skips_model():
    """RAG 有命中但缺少定位标识 → 与"没有证据"同等待遇。"""
    state = new_state(**BASE_STATE, evidence_sufficient=True, user_input=QUERY)
    port = node_port({"rag": rag_result("ok", [{"text": "没有定位标识的片段"}])})
    node_result = asyncio.run(teach(state, port))
    assert port.calls_of("generate") == []

    service = make_service(
        {"rag": rag_result("ok", [{"text": "没有定位标识的片段"}])},
        script=[{"content": MODEL_REPLY}],
    )
    result = asyncio.run(service.execute(teach_decision(), CTX))

    assert result["status"] == STATUS_INSUFFICIENT_EVIDENCE
    assert result["content"] == node_result["response_text"] == TEACH_INSUFFICIENT_EVIDENCE_TEXT


def test_teach_with_evidence_matches_node_and_keeps_locators_only():
    skill_result = rag_result("ok", [dict(EVIDENCE)])
    state = new_state(**BASE_STATE, evidence_sufficient=True, user_input=QUERY)
    port = node_port({"rag": skill_result}, replies=[MODEL_REPLY])
    node_result = asyncio.run(teach(state, port))

    service = make_service({"rag": skill_result}, script=[{"content": MODEL_REPLY}])
    result = asyncio.run(service.execute(teach_decision(), CTX))

    assert result["status"] == STATUS_SUCCESS
    assert result["content"] == node_result["response_text"] == MODEL_REPLY
    assert len(result["evidence"]) == 1 == len(node_result["citations"])

    reference = result["evidence"][0]
    assert "text" not in reference, "引用里不得复制教材原文（§6.4）"
    for field in ("document_id", "chunk_id", "page", "source"):
        assert reference[field] == node_result["citations"][0][field]
    assert node_result["retrieved_evidence_refs"] == node_result["citations"]
    assert service.provider.calls, "有证据时必须真的调用模型"
    assert port.calls_of("generate"), "图侧同样应经分发器调用模型"


def test_teach_model_failure_matches_node_and_drops_citations():
    skill_result = rag_result("ok", [dict(EVIDENCE)])
    state = new_state(**BASE_STATE, evidence_sufficient=True, user_input=QUERY)
    port = node_port({"rag": skill_result}, replies=[""])  # FakeRuntime 返回空内容
    node_result = asyncio.run(teach(state, port))

    service = make_service(
        {"rag": skill_result}, script=[{"content": "", "finish_reason": "stop"}]
    )
    result = asyncio.run(service.execute(teach_decision(), CTX))

    assert result["status"] == STATUS_SUCCESS
    assert result["content"] == node_result["response_text"] == GENERATION_FAILED_TEXT
    assert result["evidence"] == [] == node_result["citations"]
    assert result["metadata"]["degraded_from"] == "error"


def test_correct_dispatch_matches_node_and_reports_conflicts():
    skill_result = rag_result("ok", [dict(EVIDENCE)])
    state = new_state(
        **BASE_STATE,
        evidence_sufficient=True,
        user_input="我认为梯度下降是沿正梯度方向走",
        misconceptions=["把下降方向当成正梯度方向"],
        wrong_streak=3,
        hint_level=3,
    )
    port = node_port({"rag": skill_result}, replies=[MODEL_REPLY])
    node_result = asyncio.run(correct(state, port))

    service = make_service({"rag": skill_result}, script=[{"content": MODEL_REPLY}])
    result = asyncio.run(
        service.execute(
            PedagogicalDecision(
                action=ACTION_CORRECT,
                concept=BASE_STATE["current_concept"],
                require_evidence=True,
                evidence_sufficient=True,
                params={
                    "conflicts": "把下降方向当成正梯度方向",
                    "user_input": "我认为梯度下降是沿正梯度方向走",
                    "query": "我认为梯度下降是沿正梯度方向走",
                },
            ).to_dict(),
            CTX,
        )
    )

    assert result["content"] == node_result["response_text"] == MODEL_REPLY
    # 纠错后清零错误计数，下一轮从轻提示重新开始
    assert node_result["wrong_streak"] == 0 and node_result["hint_level"] == 0


def test_correct_without_evidence_uses_conflict_specific_text():
    state = new_state(
        **BASE_STATE,
        evidence_sufficient=False,
        misconceptions=["把下降方向当成正梯度方向"],
    )
    node_result = asyncio.run(correct(state, node_port()))

    service = make_service()
    result = asyncio.run(
        service.execute(
            PedagogicalDecision(
                action=ACTION_CORRECT,
                concept=BASE_STATE["current_concept"],
                require_evidence=True,
                evidence_sufficient=False,
                params={
                    "conflicts": "把下降方向当成正梯度方向",
                    "user_input": "我认为方向取正是对的",
                    "query": "我认为方向取正是对的",
                },
            ).to_dict(),
            CTX,
        )
    )

    expected = CORRECT_INSUFFICIENT_EVIDENCE_TEXT.format(
        concept=BASE_STATE["current_concept"], conflicts="把下降方向当成正梯度方向"
    )
    assert result["status"] == STATUS_INSUFFICIENT_EVIDENCE
    assert result["content"] == node_result["response_text"] == expected
    assert "把下降方向当成正梯度方向" in result["content"]
    assert service.provider.calls == []


def test_teach_dispatch_reports_missing_request_fields():
    service = make_service()
    decision = teach_decision()
    del decision["params"]["query"]

    result = asyncio.run(service.execute(decision, CTX))

    assert result["status"] == STATUS_INVALID_REQUEST
    assert result["metadata"]["missing_fields"] == ["params.query"]


# ======================================================================
# 五、装配失败必须响亮地错
# ======================================================================
def test_execute_without_injected_bindings_fails_loudly():
    service = make_service(bindings={})

    result = asyncio.run(service.execute(PedagogicalDecision(action=ACTION_HINT).to_dict(), CTX))

    assert result["status"] == STATUS_NO_BINDING
    assert result["metadata"]["known_actions"] == []


def test_node_without_injected_bindings_does_not_fabricate_text():
    """图侧同样响亮地错：不给内容，也不自带一份兜底文案（否则 HOW 又回到图里）。

    装配漏了是组合根的 bug，应当在决策事件的 capability_status 与
    /health 的 action_bindings 上看得见，而不是让节点悄悄兜一段话把问题遮住。
    """
    state = new_state(**BASE_STATE, hint_level=1, wrong_streak=1)
    port = FakeRuntime()  # 未注入绑定表
    result = asyncio.run(hint(state, port))

    assert result["response_text"] == ""
    assert result["strategy_note"] == "hint:level=2:no_binding"
    decisions = [
        event["payload"]
        for event in port.events
        if event["type"] == "pedagogy.decision"
    ]
    assert decisions[0]["capability_status"] == STATUS_NO_BINDING


def test_execute_reports_unregistered_capability():
    service = make_service(bindings={ACTION_HINT: {"capability": "not_registered"}})

    result = asyncio.run(service.execute(PedagogicalDecision(action=ACTION_HINT).to_dict(), CTX))

    assert result["status"] == STATUS_CAPABILITY_NOT_FOUND
    assert "render_template" in result["metadata"]["known_capabilities"]


def test_composition_root_injects_action_bindings():
    service = make_service(bindings={})

    create_app(runtime=service)

    assert set(service.action_bindings) == set(ACTION_BINDINGS)


# ======================================================================
# 六、节点瘦身守卫：HOW 不得再出现在节点源码里
# ======================================================================
#: 迁走的动作节点 → 不允许再出现的符号（回复正文的素材、提示词、证据获取、模型流收集）。
#: 它们的"主人"已经变成 graph/education/bindings.py 与 runtime/capabilities.py，
#: 节点里再出现一次就意味着同一件事有两个主人（改一处忘另一处）。
#:
#: **端口调用不在这里**：那一条由 tests/test_architecture_boundaries.py 的
#: `test_graph_only_touches_the_narrow_port_face` 全图扫描兜住（更强）。
#:
#: 不在禁止之列的是 policies 里的**决策依据常量**（阈值、策略方向清单…）：
#: 节点本来就要读它们来判断与写事件，那属于 WHAT（reflect 读
#: REFLECT_STRATEGY_OPTIONS 填决策事件即是此例）。
MIGRATED_NODE_SYMBOLS = {
    "assess": (
        "fetch_evidence",
        "evidence_ref",
        "EVIDENCE_TOP_K",
        "STOPPED_TEXT",
    ),
    "hint": ("HINT_LEVEL_TEMPLATES",),
    "ask": ("ASK_FALLBACK_QUESTION", "ASK_NO_EVIDENCE_NOTE"),
    "reflect": ("REFLECT_TEXT", "STOPPED_TEXT"),
    "test": (
        "invoke_skill",
        "QUIZ_FALLBACK_ITEM",
        "QUIZ_NOT_CONNECTED_NOTE",
        "QUIZ_NO_JUDGEMENT_NOTE",
        "_compose_item",
        "_compose_evaluation",
    ),
    "teach": (
        "build_evidence_block",
        "collect_stream",
        "fetch_evidence",
        "EVIDENCE_MAX_TEXT_CHARS",
        "TEACH_SYSTEM_PROMPT",
    ),
    "correct": (
        "build_evidence_block",
        "collect_stream",
        "fetch_evidence",
        "CORRECT_INSUFFICIENT_EVIDENCE_TEXT",
    ),
}


def test_migrated_nodes_no_longer_contain_how(project_root: Path) -> None:
    """所有教学节点只产出决策，正文素材都不在节点里。"""
    violations: list[str] = []
    for node, symbols in MIGRATED_NODE_SYMBOLS.items():
        source = (project_root / "graph" / "education" / "nodes" / f"{node}.py").read_text(
            encoding="utf-8"
        )
        for symbol in symbols:
            # 按词边界匹配：否则 evidence_ref 会被 evidence_refs 这种变量名误伤
            if re.search(rf"\b{re.escape(symbol)}\b", source):
                violations.append(f"nodes/{node}.py 仍引用 {symbol}")

    assert not violations, (
        "节点瘦身被回退：这些符号应只在 graph/education/bindings.py 或 "
        "runtime/capabilities.py 里出现。\n" + "\n".join(violations)
    )


def test_reflect_dispatch_matches_node_and_stays_deterministic():
    """换策略建议与收束语都由绑定渲染；两种分支都不经模型。"""
    service = make_service()

    turn_limited = asyncio.run(
        service.execute(
            PedagogicalDecision(
                action=ACTION_REFLECT,
                concept=BASE_STATE["current_concept"],
                params={"stopped": False},
            ).to_dict(),
            CTX,
        )
    )
    assert turn_limited["status"] == STATUS_SUCCESS
    assert turn_limited["capability"] == "render_template"
    assert turn_limited["content"] == REFLECT_TEXT.format(
        concept=BASE_STATE["current_concept"]
    )
    assert service.provider.calls == [], "换策略建议是模板，不经模型"

    port = node_port()
    node_result = asyncio.run(
        reflect(new_state(**BASE_STATE, turn_count=6, max_turns=6), port)
    )
    assert node_result["response_text"] == turn_limited["content"]
    assert node_result["next_action"] == ACTION_END

    # stopped=True 是防御性分支（正常链路走不到，见 bindings 注释），仍然可用
    stopped = asyncio.run(
        service.execute(
            PedagogicalDecision(
                action=ACTION_REFLECT,
                concept=BASE_STATE["current_concept"],
                params={"stopped": True},
            ).to_dict(),
            CTX,
        )
    )
    assert stopped["content"] == STOPPED_TEXT


def test_reflect_decision_event_keeps_strategy_options_from_policies():
    """决策事件里的三条换策略方向来自 policies，不再由节点硬编码一份。"""
    port = node_port()
    asyncio.run(reflect(new_state(**BASE_STATE, turn_count=6, max_turns=6), port))

    decision = next(
        event["payload"]
        for event in port.events
        if event["type"] == "pedagogy.decision" and event["payload"]["node"] == "reflect"
    )
    assert decision["strategy_options"] == list(REFLECT_STRATEGY_OPTIONS)
    assert decision["capability_status"] == STATUS_SUCCESS


# ======================================================================
# 十、Test：出题与评价的正文拼装全部由绑定声明
# ======================================================================
QUIZ_ITEM = {
    "status": "ok",
    "skill": "quiz",
    "item": {"item_id": "", "stem": "请说明收敛判断的条件。", "structured": False},
    "item_bank_connected": False,
    "note": "题库未接入",
    "source": "model_generated",
}
QUIZ_EVALUATION = {
    "status": "ok",
    "skill": "quiz",
    "evaluation": {"correct": None, "auto_grading_connected": False, "feedback": "你少了收敛判断这一步。"},
    "item_bank_connected": False,
    "note": "题库未接入",
    "source": "model_generated",
}


def test_test_generate_mode_composes_item_from_binding():
    """出题：正文取自 Skill 结果的 item.stem，前后框架由绑定按 reply_frame 选。"""
    port = node_port({"quiz": QUIZ_ITEM})
    result = asyncio.run(quiz_node(new_state(**BASE_STATE, attempt_count=3), port))

    assert result["response_text"] == (
        QUIZ_ITEM_PREFIX + QUIZ_ITEM["item"]["stem"] + "\n" + QUIZ_NOT_CONNECTED_NOTE
    )
    assert result["strategy_note"] == "test:generate:not_applicable"
    assert result["attempt_count"] == 3, "本轮没有作答可评价，不增加尝试次数"

    payload = port.calls_of("invoke_skill")[0]["input"]
    assert payload["mode"] == "generate"
    assert payload["difficulty"] == "intermediate", "尝试 3 次且无连错 → 中等难度"
    assert payload["item_type"] == "short_answer"
    assert payload["evidence_refs"] == []


@pytest.mark.parametrize(
    ("correct", "head", "tail"),
    [
        (True, QUIZ_FRAME_CORRECT, QUIZ_NOT_CONNECTED_NOTE),
        (False, QUIZ_FRAME_INCORRECT, QUIZ_NOT_CONNECTED_NOTE),
        # 判分缺失必须与"答错"分开措辞（§13.1 不把"判不了"当成"答错"）
        (None, QUIZ_FRAME_UNKNOWN, QUIZ_NO_JUDGEMENT_NOTE),
    ],
)
def test_test_evaluate_mode_frames_match_the_old_composition(correct, head, tail):
    """评价：三态判分走三个框架键，正文取自 evaluation.feedback，措辞各不同。"""
    port = node_port({"quiz": QUIZ_EVALUATION})
    state = new_state(
        **BASE_STATE, user_input="我认为条件是梯度足够小", attempt_count=3, last_answer_correct=correct
    )
    result = asyncio.run(quiz_node(state, port))

    assert result["response_text"] == (
        head + QUIZ_EVALUATION["evaluation"]["feedback"] + "\n" + tail
    )
    assert result["attempt_count"] == 4, "本轮有作答应记为一次尝试"
    if correct is True:
        assert result["wrong_streak"] == 0
    elif correct is False:
        assert result["wrong_streak"] == 1
    else:
        assert result["wrong_streak"] == 0, "判分缺失不得当成答错"


def test_test_falls_back_when_skill_gives_nothing():
    """Quiz Skill 不可用：正文退回策略模板，框架文案照旧（学生仍看得到"题库未接入"）。"""
    port = node_port()  # 没有 quiz 结果 → SkillCapability 判为不可用
    result = asyncio.run(quiz_node(new_state(**BASE_STATE, attempt_count=3), port))

    assert result["response_text"] == (
        QUIZ_ITEM_PREFIX
        + QUIZ_FALLBACK_ITEM.format(concept=BASE_STATE["current_concept"])
        + "\n"
        + QUIZ_NOT_CONNECTED_NOTE
    )
    assert port.calls_of("invoke_skill") != [], "确实尝试过调用 Skill，再兜底"


def test_test_reports_item_bank_flag_from_the_skill():
    """item_bank_connected 按白名单透传进决策事件（True 才证明真的带回来了）。"""
    port = node_port({"quiz": {**QUIZ_ITEM, "item_bank_connected": True}})
    asyncio.run(quiz_node(new_state(**BASE_STATE, attempt_count=3), port))

    decision = next(
        event["payload"]
        for event in port.events
        if event["type"] == "pedagogy.decision" and event["payload"]["node"] == "test"
    )
    assert decision["item_bank_connected"] is True
    assert decision["quiz_skill_status"] == "ok"


def quiz_decision(**params: Any) -> dict:
    """按节点会发出的样子构造一条 test 决策（绑定引用的字段一个不少）。"""
    base = {
        "mode": "generate",
        "reply_frame": REPLY_FRAME_QUIZ,
        "difficulty": "basic",
        "item_type": "short_answer",
        "student_answer": "",
        "learning_goal": BASE_STATE["learning_goal"],
        "evidence_refs": [],
    }
    return PedagogicalDecision(
        action=ACTION_TEST,
        concept=BASE_STATE["current_concept"],
        params={**base, **params},
    ).to_dict()


def test_test_frame_typo_fails_loudly_instead_of_dropping_the_frame():
    """reply_frame 写歪时必须显式失败：少一段框架会变成"半截回复"且没人发现。"""
    service = make_service({"quiz": QUIZ_ITEM})
    result = asyncio.run(service.execute(quiz_decision(reply_frame="typo"), CTX))

    assert result["status"] == "error"
    assert result["error"]["code"] == "template_not_found"


def test_invoke_skill_content_field_supports_dotted_paths():
    """content_field 走 a.b 点路径：绑定据此读 Skill 结果的深层字段。"""
    service = make_service({"quiz": QUIZ_EVALUATION})
    result = asyncio.run(
        service.execute(quiz_decision(mode="evaluate", reply_frame=REPLY_FRAME_UNKNOWN), CTX)
    )

    assert QUIZ_EVALUATION["evaluation"]["feedback"] in result["content"]


# ======================================================================
# 十一、学情记忆：读 / 写 / 问模型都走决策
# ======================================================================
def test_recall_decision_returns_records_and_takes_identity_from_ctx():
    """读学情记忆：记录经 records 通道回来；learner_id 由能力层从 ctx 补。"""
    service = make_service(
        bindings={
            ACTION_RECALL: {
                "capability": "read_memory",
                "params": {"query": {"memory_type": "long_term", "key": "${concept}"}},
            }
        }
    )
    reader = _recording_reader(service.memory.read)
    service.memory.read = reader

    result = asyncio.run(
        service.execute(
            PedagogicalDecision(action=ACTION_RECALL, concept="梯度下降").to_dict(), CTX
        )
    )

    assert result["status"] == STATUS_SUCCESS
    assert result["records"] == []
    assert reader.last_query["learner_id"] == CTX["learner_id"], "身份取自上下文"
    assert reader.last_query["key"] == "梯度下降", "检索口径取自绑定"


def test_write_decision_skips_storage_on_empty_records():
    """空记录 = 本轮没有可写的增量：算成功，且不惊动存储（§5.5）。"""
    service = make_service()
    writer = _recording_writer(service.memory.write)
    service.memory.write = writer

    result = asyncio.run(
        service.execute(
            PedagogicalDecision(
                action=ACTION_UPDATE_PROFILE, concept="梯度下降", params={"records": []}
            ).to_dict(),
            CTX,
        )
    )

    assert result["status"] == STATUS_SUCCESS
    assert result["metadata"]["written"] == 0
    assert writer.writes == []


def test_write_decision_persists_records_verbatim():
    service = make_service()
    writer = _recording_writer(service.memory.write)
    service.memory.write = writer
    record = {
        "learner_id": "learner_1",
        "memory_type": "long_term",
        "key": "concept:梯度下降",
        "content": "规则化观察：本轮动作=teach。",
        "source": "graph:update_profile/session:sess_1/trace:trace_1/turn:1",
        "confidence": 0.3,
        "revocable": True,
    }

    result = asyncio.run(
        service.execute(
            PedagogicalDecision(
                action=ACTION_UPDATE_PROFILE, params={"records": [record]}
            ).to_dict(),
            CTX,
        )
    )

    assert result["metadata"]["written"] == 1
    assert writer.writes == [[record]]


def diagnose_decision(**params: Any) -> dict:
    """按节点会发出的样子构造一条 diagnose 决策（绑定引用的字段一个不少）。"""
    base = {
        "learner_id": BASE_STATE["learner_id"],
        "attempt_count": 0,
        "wrong_streak": 0,
        "hint_level": 0,
        "misconceptions": [],
        "last_answer_correct": None,
        "recent_actions": [],
        "evidence_count": 0,
    }
    return PedagogicalDecision(
        action=ACTION_DIAGNOSE,
        concept=BASE_STATE["current_concept"],
        params={**base, **params},
    ).to_dict()


def test_diagnose_reports_skill_status_without_treating_it_as_failure():
    """问学情模型是"只问状态"的调用：模型未接入（not_implemented）不算执行失败。

    否则决策事件会把"模型尚未接入"记成 capability_status=error，
    审计与验收就看不出"这一环是主动降级还是真的坏了"（§18.1 诚实原则）。
    """
    service = make_service({"diagnosis": {"status": "not_implemented", "skill": "diagnosis"}})

    result = asyncio.run(service.execute(diagnose_decision(), CTX))

    assert result["status"] == STATUS_SUCCESS
    assert result["content"] == ""
    assert result["metadata"]["skill_status"] == "not_implemented"


def test_diagnose_status_survives_into_the_decision_event():
    """同一条语义经节点落到事件上：diagnosis_status 记的是 Skill 的原话。"""
    port = node_port({"diagnosis": {"status": "not_implemented", "skill": "diagnosis"}})
    asyncio.run(update_profile(new_state(**BASE_STATE, action=ACTION_TEACH), port))

    decision = next(
        event["payload"]
        for event in port.events
        if event["type"] == "pedagogy.decision"
        and event["payload"]["node"] == "update_profile"
    )
    assert decision["diagnosis_status"] == "not_implemented"
    assert decision["memory_written"] > 0, "降级不等于不写：规则化观察照常落盘（§18.1）"


def test_update_profile_reports_when_memory_write_fails():
    """落盘失败必须说清楚，不能悄悄让下一轮读不到记忆。"""
    bindings = {**ACTION_BINDINGS, ACTION_UPDATE_PROFILE: {"capability": "不存在的能力"}}
    port = FakeRuntime(
        replies=[MODEL_REPLY] * 5,
        skill_results={"rag": rag_result("ok", [dict(EVIDENCE)])},
        action_bindings=bindings,
    )

    asyncio.run(update_profile(new_state(**BASE_STATE, action=ACTION_TEACH), port))

    decision = next(
        event["payload"]
        for event in port.events
        if event["type"] == "pedagogy.decision"
        and event["payload"]["node"] == "update_profile"
    )
    assert decision["memory_written"] == 0, "没写成功就不在事件里声称写了"
    assert "未成功" in decision["reason"]
    assert port.written_memories == []


def _recording_reader(original):
    """包一层记录收到的 query，用于断言"谁提供了什么"。"""

    async def reader(query, ctx):
        reader.last_query = dict(query)
        return await original(query, ctx)

    return reader


def _recording_writer(original):
    """包一层记录每次落盘的记录列表。"""
    writes: list[list[dict]] = []

    async def writer(records, ctx):
        writes.append([dict(item) for item in records])
        return await original(records, ctx)

    writer.writes = writes
    return writer


def test_evidence_is_retrieved_once_per_turn():
    """同一轮内只检索一次：Assess 探路与 Teach 取原文复用同一结果。

    两件事一起保证：① 不多花一次检索；② 不会出现"Assess 判证据充分、
    Teach 却说证据不足"的自相矛盾（两次检索结果不一致时就会这样）。
    """
    port = node_port({"rag": rag_result("ok", [dict(EVIDENCE)])}, replies=[MODEL_REPLY] * 5)
    run_turn(port, user_input=QUERY)

    rag_calls = [call for call in port.calls_of("invoke_skill") if call["name"] == "rag"]
    assert len(rag_calls) == 1, f"同一轮内应只检索一次，实际 {len(rag_calls)} 次"


def test_evidence_memo_does_not_leak_across_turns():
    """跨轮不复用：trace_id 变了就必须重新检索，否则会拿到上一轮的陈旧证据。"""
    port = node_port({"rag": rag_result("ok", [dict(EVIDENCE)])}, replies=[MODEL_REPLY] * 5)
    run_turn(port, user_input=QUERY, trace_id="trace_1")
    run_turn(port, user_input=QUERY, trace_id="trace_2")

    rag_calls = [call for call in port.calls_of("invoke_skill") if call["name"] == "rag"]
    assert len(rag_calls) == 2, "两轮应各自检索一次"


def test_evidence_memo_is_skipped_without_trace_id():
    """没有 trace_id 就无法判断是否同一轮：宁可不备忘，也不冒拿到陈旧证据的风险。"""
    service = make_service(
        {"rag": rag_result("ok", [dict(EVIDENCE)])}, script=[{"content": MODEL_REPLY}]
    )
    ctx_without_trace = {**CTX, "trace_id": ""}

    for _ in range(2):
        asyncio.run(service.execute(teach_decision(), ctx_without_trace))

    rag_skill = service.skills.get("rag")
    assert rag_skill is not None, "前置条件：rag 桩已注册"
    assert len(rag_skill.payloads) == 2, "无 trace_id 时每次都真检索"  # type: ignore[attr-defined]


def test_side_effect_capabilities_are_never_memoized():
    """默认不备忘：有副作用的能力（写学情）即使参数完全相同也必须每次都真调用。

    这是 `memo` 声明式开关的危险方向——若默认备忘，重复写入会被静默吃掉。
    """
    service = make_service()
    writer = _recording_writer(service.memory.write)
    service.memory.write = writer
    decision = PedagogicalDecision(
        action=ACTION_UPDATE_PROFILE,
        params={
            "records": [
                {
                    "learner_id": "learner_1",
                    "memory_type": "long_term",
                    "key": "concept:梯度下降",
                    "content": "规则化观察。",
                    "source": "graph:update_profile",
                    "confidence": 0.3,
                    "revocable": True,
                }
            ]
        },
    ).to_dict()

    for _ in range(2):
        asyncio.run(service.execute(decision, CTX))

    assert len(writer.writes) == 2, "两次写入都必须真的落盘"


# ======================================================================
# 十二、绑定表数据本身
# ======================================================================
def test_action_bindings_cover_the_migrated_actions():
    """迁移的动作必须都有绑定，且绑定引用的能力都已注册。"""
    from runtime.capabilities import default_capabilities

    registered = set(default_capabilities().names())
    migrated = {
        ACTION_ASSESS,
        ACTION_END,
        ACTION_HINT,
        ACTION_ASK,
        ACTION_TEACH,
        ACTION_CORRECT,
        ACTION_REFLECT,
        ACTION_TEST,
    }
    assert migrated <= set(ACTION_BINDINGS)
    for action, binding in ACTION_BINDINGS.items():
        assert binding["capability"] in registered, f"{action} 引用了未注册能力"
        evidence_bound = binding.get("evidence")
        if evidence_bound is not None:
            assert evidence_bound["capability"] in registered


def test_assess_is_not_a_routable_node_action():
    """ACTION_ASSESS 只是"取证据"的请求名，不得进 ACTION_NODES。

    进了的话 route_from_assess 会把 next_action=assess 映射成节点 "assess"，
    于是 Assess 出口回到自己，形成自环。
    """
    assert ACTION_ASSESS not in ACTION_NODES
    assert ACTION_ASSESS not in END_ACTIONS


def test_action_bindings_contain_no_teaching_branching():
    """绑定是纯数据（可 JSON 序列化），不得夹带函数或 if/elif 之类的可执行逻辑。"""
    import json

    json.dumps(ACTION_BINDINGS, ensure_ascii=False)  # 不可序列化即失败

    def _has_callable(value) -> bool:
        if callable(value):
            return True
        if isinstance(value, dict):
            return any(_has_callable(item) for item in value.values())
        if isinstance(value, list):
            return any(_has_callable(item) for item in value)
        return False

    assert not _has_callable(ACTION_BINDINGS)


# ======================================================================
# 八、Assess：只判断，不拼正文
# ======================================================================
def test_assess_probe_returns_locators_without_textbook_text():
    """证据原文不得跨回策略层（§6.4）：Assess 拿到的必须是可定位引用。"""
    service = make_service({"rag": rag_result("ok", [dict(EVIDENCE)])})

    result = asyncio.run(
        service.execute(
            PedagogicalDecision(action=ACTION_ASSESS, concept="梯度下降", params={"query": QUERY}).to_dict(),
            CTX,
        )
    )

    assert result["status"] == STATUS_SUCCESS
    assert len(result["evidence"]) == 1
    reference = result["evidence"][0]
    assert "text" not in reference, "教材原文只用于当次生成，不得进状态（§6.4）"
    assert reference["document_id"] == "doc1" and reference["page"] == 12


def test_assess_stores_only_locator_refs_and_skips_retrieval_when_stopped():
    """Assess 状态里只有引用；学生停止时连检索都不发起。"""
    port = node_port({"rag": rag_result("ok", [dict(EVIDENCE)])})
    result = asyncio.run(assess(new_state(**BASE_STATE, user_input=QUERY), port))

    assert result["evidence_sufficient"] is True
    assert result["last_assessment"]["evidence_count"] == 1
    assert "text" not in result["retrieved_evidence_refs"][0]

    stopped_port = node_port({"rag": rag_result("ok", [dict(EVIDENCE)])})
    stopped = asyncio.run(
        assess(new_state(**BASE_STATE, user_input="我不想学了", student_stopped=True), stopped_port)
    )
    assert stopped["action"] == ACTION_END
    assert stopped_port.calls_of("invoke_skill") == [], "已停止时不应再检索教材"


def test_assess_closing_line_comes_from_binding_not_the_node():
    """收束语由绑定声明（ACTION_END → 模板渲染），节点不再自带 STOPPED_TEXT。"""
    port = node_port()
    result = asyncio.run(
        assess(new_state(**BASE_STATE, user_input="我不想学了", student_stopped=True), port)
    )
    assert result["response_text"] == STOPPED_TEXT
    assert port.calls_of("generate") == [], "收束语是模板，不经模型"

    # 没注入绑定时节点不得凭空造一段收束语（否则 HOW 又回到图里）：
    # 状态里干脆不出现 response_text，而不是填一句节点自带的文案
    bare = asyncio.run(
        assess(new_state(**BASE_STATE, user_input="我不想学了", student_stopped=True), FakeRuntime())
    )
    assert bare.get("response_text", "") == ""
    assert bare["action"] == ACTION_END