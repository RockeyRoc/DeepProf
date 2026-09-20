"""Teach 节点：给出讲解决策，不生成讲解正文（DESIGNv0.4 §4.4 / §6.2 / §7.3 / §12）。

进入条件：概念缺失、学生主动求讲解或先验不足（由 Assess 决策为 teach）。

先验不足时（§16.3"先验不足"样例）：Assess 已判定 `prior_knowledge_gap`，
本节点只把"起讲点前移到前置概念"的策略说明放进 `params.prior_gap_note`，
具体那段话由 policies.teach_prior_note 决定、由绑定拼进提示词——节点不写文案。

两条关键纪律现在由"决策 + 绑定"共同保证，而不是由节点自己写代码：

1. **不编造引用**：决策声明 `require_evidence=True`，Runtime 的分发器据此先取
   可定位证据；取不到就**完全不调用模型**，改用绑定声明的确定性"证据不足"表述，
   且引用为空（§7.3、§12）。节点不再自己判断要不要调用模型。
2. **状态不放大文本**：进状态的只有可定位引用（CapabilityResult.evidence，
   不含原文）；讲解所需的原文由能力层内部取用，用完即弃（§6.4）。

证据是否充分由 Assess 判定并写进决策（`evidence_sufficient`）：
策略层说"本轮无证据"时，分发器不再硬找，直接给证据不足表述——省一次检索，
也避免"证据不足还试一下"的含糊语义。

只依赖 RuntimePort：execute / emit。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimePort

from ..contracts import PedagogicalDecision
from ..policies import ACTION_TEACH, EMOTION_BY_ACTION, teach_prior_note
from ..state import PedagogyState
from . import dispatch, emit_decision, emit_entered, emit_exited

NODE = "teach"


async def teach(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    """分层讲解：有证据则带引用生成，无证据则明确说明证据不足。"""
    concept = str(state.get("current_concept") or "")
    evidence_sufficient = bool(state.get("evidence_sufficient"))
    # 先验判定由 Assess 给出（§16.3 先验不足样例）；"怎么讲"的起讲点提示词由
    # policies.teach_prior_note 决定，节点只负责把它交给绑定去拼。
    prior_gap = bool(state.get("prior_knowledge_gap"))
    await emit_entered(
        port,
        state,
        NODE,
        concept=concept,
        evidence_sufficient=evidence_sufficient,
        prior_knowledge_gap=prior_gap,
    )

    query = str(state.get("user_input") or "") or concept
    result = await dispatch(
        port,
        state,
        PedagogicalDecision(
            action=ACTION_TEACH,
            concept=concept,
            # 讲解必须落在教材上：没有可定位证据就不生成（§7.3 不编造引用）
            require_evidence=True,
            reveal_answer=True,  # 讲解就是要给出结论
            require_student_reply=True,
            evidence_sufficient=evidence_sufficient,
            params={
                "learning_goal": str(state.get("learning_goal") or ""),
                "user_input": str(state.get("user_input") or ""),
                "query": query,
                "prior_gap_note": teach_prior_note(prior_gap),
                "memory_note": str(state.get("memory_note") or ""),
            },
            reason=(
                "学生自述缺少先验，讲解起点前移到前置概念"
                if prior_gap
                else "概念缺失且适合直接解释"
            ),
        ),
    )

    citations = list(result.evidence)
    degraded = bool(result.metadata.get("degraded_from"))
    if result.status == "insufficient_evidence":
        reason = "证据不足（无检索命中或检索不可用），按 §7.3 不给出任何来源，未调用模型"
    elif degraded:
        reason = "检索到证据但模型调用失败，按失败表述返回且不附引用"
    else:
        reason = f"依据 {len(citations)} 条可定位教材片段生成分层讲解，并附引用"

    await emit_decision(
        port,
        state,
        NODE,
        ACTION_TEACH,
        reason,
        citation_count=len(citations),
        generation_skipped=result.status == "insufficient_evidence",
        source_attached=bool(citations),
        prior_knowledge_gap=prior_gap,
        capability=result.capability,
        capability_status=result.status,
    )
    await emit_exited(
        port, state, NODE, response_text=result.content, citations=citations, action=ACTION_TEACH
    )

    update: dict[str, Any] = {
        "action": ACTION_TEACH,
        "response_text": result.content,
        "emotion": EMOTION_BY_ACTION[ACTION_TEACH],
        "citations": citations,
        "strategy_note": f"teach:evidence={len(citations)}",
    }
    if citations:
        update["retrieved_evidence_refs"] = citations
    return update


__all__ = ["NODE", "teach"]