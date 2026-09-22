"""Assess 节点：判断意图、先验知识与置信度（DESIGNv0.6 §6.2 / §4.4）。

进入条件：每轮对话的入口（新问题、阶段切换或信息不足）。

本节点是**决策节点**：它不产出教学正文，只判断"下一步该教什么"，
并把判断依据写进状态。两类输入各发一条决策，节点不直接碰存储与检索：

1. **学情记忆** —— 决策 action=recall → read_memory 能力。身份（learner_id）由
   能力层从调用上下文取，节点只声明"要读记忆"这件事；
2. **教材证据** —— 决策 action=assess → retrieve_evidence 能力。节点拿回
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
    ACTION_RECALL,
    MAX_TURNS_DEFAULT,
    MISCONCEPTION_KEEP,
    is_misconception_record,
    memory_note,
    misconception_label,
    reports_prior_gap,
)
from ..router import decide_action
from ..state import PedagogyState
from . import dispatch, emit_decision, emit_entered, emit_exited

#: 该节点的动作决策是"整体判定"，证据不足也照常决策（只影响输出方式）
NODE = "assess"


async def assess(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    """执行一次教学评估，返回状态增量（正文只可能是收束语，§6.4）。"""
    learner_id = str(state.get("learner_id") or "")
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

    # ---- 2) 读学情记忆（只读，不改变掌握度）----
    recall = await dispatch(
        port,
        state,
        PedagogicalDecision(
            action=ACTION_RECALL,
            concept=concept,
            require_evidence=False,
            require_student_reply=False,
            evidence_sufficient=bool(state.get("evidence_sufficient")),
            params={"learner_id": learner_id},
            reason="判断本轮教学动作前先读该学习者的长期学情记忆",
        ),
    )
    records = list(recall.records)
    record_ids = [str(item.get("record_id") or "") for item in records if item.get("record_id")]
    # learner_state_ref：指向学情记忆的 Storage 引用；无记录时给出可解析的占位引用
    learner_state_ref = record_ids[0] if record_ids else (
        f"memory://{learner_id or 'anonymous'}/{concept or 'general'}"
    )
    misconceptions = _merge_misconceptions(state.get("misconceptions") or [], records)
    # 学情记忆摘要：压缩规则（条数/字数上限）在 policies，节点只负责压缩后放进
    # 状态，供 teach / ask / correct 经 params 透传、由绑定拼进提示词（§6.4）。
    note = memory_note(records)
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
                    "query": str(state.get("user_input") or "") or concept,
                },
                reason="评估本轮教学动作前先确认教材证据是否可定位",
            ),
        )
    evidence_sufficient = evidence_result is not None and evidence_result.status == "success"
    evidence_refs = list(evidence_result.evidence) if evidence_result is not None else []
    evidence_status = (
        "skipped"
        if evidence_result is None
        else str(evidence_result.metadata.get("retrieval_status") or evidence_result.status)
    )

    # ---- 4) 决策 ----
    decision_state: PedagogyState = {
        **state,
        "turn_count": next_turn,
        "misconceptions": misconceptions,
        "evidence_sufficient": evidence_sufficient,
        "retrieved_evidence_refs": evidence_refs,
        "prior_knowledge_gap": prior_gap,
        "memory_note": note,
    }
    action, reason = decide_action(decision_state)
    assessment = {
        "action": action,
        "reason": reason,
        "evidence_sufficient": evidence_sufficient,
        "evidence_status": evidence_status,
        "evidence_count": len(evidence_refs),
        "attempt_count": int(state.get("attempt_count") or 0),
        "wrong_streak": int(state.get("wrong_streak") or 0),
        "hint_level": int(state.get("hint_level") or 0),
        "prior_knowledge_gap": prior_gap,
        "turn_count": next_turn,
        "max_turns": max_turns,
        "memory_refs": record_ids,
        "student_stopped": stopped,
    }
    await emit_decision(
        port,
        state,
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
        recall_status=recall.status,
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
        "learner_state_ref": learner_state_ref,
        "misconceptions": misconceptions,
        "retrieved_evidence_refs": evidence_refs,
        "evidence_sufficient": evidence_sufficient,
        "prior_knowledge_gap": prior_gap,
        "memory_note": note,
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


def _merge_misconceptions(
    existing: list[Any], records: list[dict[str, Any]]
) -> list[str]:
    """合并状态中的误解与记忆里的历史错误模式（去重、限量）。

    历史错误模式由 UpdateProfile 写入 long_term 记忆；**"哪条记录算误解、标签放在哪"
    这个约定由 policies 定义**（`is_misconception_record` / `misconception_label`），
    读写两侧共用同一份，不在这里重写字符串（原来两侧各写一遍，改一处忘另一处不会报错）。

    只做规则化合并，不做任何"掌握度推断"——学情模型尚未接入（§18.1）。
    """
    merged: list[str] = [str(item) for item in existing if str(item).strip()]
    for record in records:
        if not is_misconception_record(record):
            continue
        label = misconception_label(record)
        if label and label not in merged:
            merged.append(label)
    return merged[:MISCONCEPTION_KEEP]


__all__ = ["NODE", "assess"]