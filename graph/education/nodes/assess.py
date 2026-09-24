"""Assess 节点：判断当前问题、证据充分度与回合边界。

进入条件：每轮对话的入口（新问题、阶段切换或信息不足）。

本节点是**决策节点**：它不产出教学正文，只判断"下一步该教什么"，
并把判断依据写进状态。只依据当前用户输入和教材证据，不读取跨题学情历史。
教材证据通过 action=assess → retrieve_evidence 能力取得。节点拿回
   CapabilityResult，用 `status == "success"` 判断证据是否充分、用 `result.evidence`
   填状态引用。

  证据只以**可定位引用**的形式回到状态（document_id / chunk_id / page），
  原文由能力层内部取用、用完即弃（§6.4）。Assess 因此不再 import RAG Skill、
  也不再自己校验命中是否可定位——那件事只有能力层一个主人。

注意：证据是否充分**不**改变动作选择，只影响输出方式（见 router.decide_action
的 docstring）。Assess 取证据是为了把 `evidence_sufficient` 与
`retrieved_evidence_refs` 写进状态，供 Ask 的"不含引用"说明与 Teach/Correct
的输出方式使用；它不参与"教什么"的判定。

只依赖 RuntimePort：execute / emit。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimePort

from ..contracts import CapabilityResult, PedagogicalDecision
from ..policies import (
    ACTION_ASSESS,
    ACTION_END,
    ACTION_QUIZ,
    ACTION_TEST,
    MAX_TURNS_DEFAULT,
    request_explicitly_claims_source_gap,
    reports_prior_gap,
)
from ..router import decide_action
from ..state import PedagogyState
from . import dispatch, emit_decision, emit_entered, emit_exited

#: 该节点的动作决策是"整体判定"，证据不足也照常决策（只影响输出方式）
NODE = "assess"


async def assess(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    """执行一次教学评估，返回状态增量（正文只可能是收束语，§6.4）。"""
    concept = str(state.get("current_concept") or "")
    turn_count = int(state.get("turn_count") or 0)
    max_turns = int(state.get("max_turns") or MAX_TURNS_DEFAULT)
    stopped = bool(state.get("student_stopped"))

    await emit_entered(
        port,
        state,
        NODE,
        concept=concept,
        student_stopped=stopped,
        input_chars=len(str(state.get("user_input") or "")),
    )

    # ---- 1) 轮次计数：只在未达上限时递增，保证 turn_count 有界 ----
    next_turn = turn_count + 1 if turn_count < max_turns else turn_count
    at_limit = next_turn >= max_turns

    # The first M1 batch deliberately avoids cross-question learning history.
    misconceptions = list(state.get("misconceptions") or [])[:5]
    # 先验判定（§16.3 先验不足样例）：只认学生的自述，不从"记忆为空"反推没掌握；
    # 判定口径（关键词与讲解起点）都在 policies，节点不自己写一份。
    prior_gap = reports_prior_gap(str(state.get("user_input") or ""))

    # ---- 3) 取证：学生已停止或已达轮次上限时不再检索 ----
    # 这两种情况下本轮不会有教学输出，探一次只是白花一次检索；
    # "要不要探"是策略，所以由节点决定要不要发出这条决策。
    evidence_result: CapabilityResult | None = None
    if not (stopped or at_limit):
        evidence_result = await dispatch(
            port,
            state,
            PedagogicalDecision(
                action=ACTION_ASSESS,
                concept=concept,
                # 不要 require_evidence 门：Assess 要的就是"有没有证据"这个信号，
                # 设了门会先探一次、再取一次
                require_evidence=False,
                require_student_reply=False,
                params={
                    # Include the active concept when a follow-up such as "give me a hint"
                    # has no textbook vocabulary of its own. This keeps retrieval tied to
                    # the same question while preserving the evidence gate.
                    "query": " ".join(part for part in (
                        str(state.get("user_input") or "").strip(),
                        concept.strip(),
                    ) if part) or concept,
                    "course_id": str(state.get("course_id") or ""),
                },
                reason="评估本轮教学动作前先确认教材证据是否可定位",
            ),
        )
    evidence_refs = list(evidence_result.evidence) if evidence_result is not None else []
    evidence_status = (
        "skipped"
        if evidence_result is None
        else str(evidence_result.metadata.get("retrieval_status") or evidence_result.status)
    )
    evidence_sufficient = evidence_result is not None and evidence_result.status == "success"
    request_text = str(state.get("user_input") or "") or concept
    if evidence_sufficient and request_explicitly_claims_source_gap(request_text):
        evidence_sufficient = False
        evidence_refs = []
        evidence_status = "insufficient_evidence"
    evidence_constraint = bool(state.get("evidence_constraint", True))
    if not evidence_constraint:
        evidence_sufficient = True

    # ---- 4) 决策 ----
    decision_state: PedagogyState = {
        **state,
        "turn_count": next_turn,
        "misconceptions": misconceptions,
        "evidence_sufficient": evidence_sufficient,
        "evidence_constraint": evidence_constraint,
        "retrieved_evidence_refs": evidence_refs,
        "prior_knowledge_gap": prior_gap,
    }
    action, reason = decide_action(decision_state)
    estimate = dict(state.get("learner_estimate") or {})
    if (str(state.get("experiment_group") or "").upper() == "C"
            and estimate.get("status") == "available" and isinstance(estimate.get("mastery"), (int, float))):
        mastery = float(estimate["mastery"])
        requested = str(state.get("requested_action") or "").lower()
        safety_actions = {ACTION_END, "reflect", "correct", "hint"}
        if requested not in {"ask", "hint", ACTION_QUIZ, ACTION_TEST, "answer"} and action not in safety_actions:
            if mastery < 0.30:
                action, reason = "teach", f"BKT 掌握估计 {mastery:.2f} 低于 0.30，先讲解"
            elif mastery < 0.60:
                action, reason = ("hint", f"BKT 掌握估计 {mastery:.2f} 位于 0.30–0.60，提供渐进提示") if state.get("item_id") else ("ask", f"BKT 掌握估计 {mastery:.2f} 位于 0.30–0.60，无活动题目，先引导推理")
            elif mastery < 0.85:
                action, reason = "ask", f"BKT 掌握估计 {mastery:.2f} 位于 0.60–0.85，检查概念关系"
            else:
                action, reason = "test", f"BKT 掌握估计 {mastery:.2f} 达到 0.85，测验迁移"
    if not evidence_sufficient and not stopped and action not in (ACTION_END, "reflect"):
        from ..policies import ACTION_REFLECT
        action = ACTION_REFLECT
        reason = "当前课程教材没有返回足够且可定位的证据；保守停止，不调用生成模型"
    assessment = {
        "action": action,
        "reason": reason,
        "evidence_sufficient": evidence_sufficient,
        "evidence_status": evidence_status,
        "evidence_count": len(evidence_refs),
        "bkt_model_version": str(estimate.get("model_version") or "") or None,
        "bkt_config_hash": str(estimate.get("config_hash") or "") or None,
        "mastery": estimate.get("mastery"),
        "learner_evidence_count": int(estimate.get("evidence_count") or 0),
        "attempt_count": int(state.get("attempt_count") or 0),
        "learner_estimate": estimate,
        "wrong_streak": int(state.get("wrong_streak") or 0),
        "hint_level": int(state.get("hint_level") or 0),
        "prior_knowledge_gap": prior_gap,
        "turn_count": next_turn,
        "max_turns": max_turns,
        "student_stopped": stopped,
    }
    await emit_decision(
        port,
        decision_state,
        NODE,
        action,
        reason,
        # assessment 里已含 action/reason，剔除后展开，避免与 emit_decision 的形参冲突
        **{
            key: value
            for key, value in assessment.items()
            if key not in ("action", "reason")
        },
        capability="retrieve_evidence" if evidence_result is not None else "",
        capability_status=evidence_result.status if evidence_result is not None else "skipped",
        reason_codes=["insufficient_evidence"] if not evidence_sufficient else [action],
    )

    # 学生停止时收束语同样是一条决策：文本由绑定声明，节点不自己拼（§4.4）
    response_text = ""
    if action == ACTION_END:
        closing = await dispatch(
            port,
            state,
            PedagogicalDecision(
                action=ACTION_END,
                concept=concept,
                require_student_reply=False,
                reason="学生主动停止，本轮只输出收束语",
            ),
        )
        response_text = closing.content

    await emit_exited(
        port,
        state,
        NODE,
        response_text=response_text,
        citations=[],
        action=action,
        turn_count=next_turn,
        evidence_count=len(evidence_refs),
    )

    update: dict[str, Any] = {
        "turn_count": next_turn,
        "misconceptions": misconceptions,
        "retrieved_evidence_refs": evidence_refs,
        "evidence_sufficient": evidence_sufficient,
        "prior_knowledge_gap": prior_gap,
        "learner_estimate": estimate,
        "last_assessment": assessment,
        "next_action": action,
        "action": action,
        "strategy_note": f"assess:{action}",
    }
    if response_text:
        update["response_text"] = response_text
        update["emotion"] = "warm"
        update["citations"] = []
    return update


__all__ = ["NODE", "assess"]
