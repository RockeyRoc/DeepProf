"""Correct 节点：指出冲突、解释原因、提供对比例（DESIGNv0.4 §4.4 / §6.2 / §7.3）。

进入条件：出现稳定错误或概念混淆（Assess 决策为 correct）。

本节点只做 WHAT：
1. 把状态里的疑似误解提炼成"冲突点"（`conflicts`）；
2. 产出 PedagogicalDecision(action=correct, require_evidence=True)；
3. 用 CapabilityResult 更新状态与事件。

纪律与 Teach 一致，且同样由决策 + 绑定保证：证据不足时**不调用模型、不给任何来源**，
改用绑定声明的固定表述（§7.3、§12"不编造引用"）。

纠错完成后重置 wrong_streak 与 hint_level：纠错给出了新信息，
下一轮应当重新 Assess、从最轻提示开始，而不是立刻再次进入同一纠错分支。
错误事实本身不会丢——已由 attempt_count、misconceptions、学情记忆（§5.5）
与作答事实（Attempt，§18.2）记录。

本节点同样产出 Attempt（§16.3"在 Test / Correct 节点产出作答事实"）：
但只认调用方给出的可靠判分（last_answer_correct），
不由"连续答错次数"或误解条目反推一条作答事实出来（§13.1 不给学生贴永久标签）。

只依赖 RuntimePort：execute / emit。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimePort

from ..contracts import PedagogicalDecision
from ..policies import ACTION_CORRECT, EMOTION_BY_ACTION
from ..state import PedagogyState
from . import dispatch, emit_attempt, emit_decision, emit_entered, emit_exited

NODE = "correct"


async def correct(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    """指出概念冲突并给出对比例；无证据时只说明冲突且不带引用。"""
    concept = str(state.get("current_concept") or "")
    misconceptions = [str(item) for item in (state.get("misconceptions") or []) if str(item).strip()]
    conflicts = "；".join(misconceptions) if misconceptions else "你的结论与概念定义不一致"

    await emit_entered(
        port,
        state,
        NODE,
        concept=concept,
        wrong_streak=int(state.get("wrong_streak") or 0),
        misconception_count=len(misconceptions),
        evidence_sufficient=bool(state.get("evidence_sufficient")),
    )

    query = str(state.get("user_input") or "") or concept
    result = await dispatch(
        port,
        state,
        PedagogicalDecision(
            action=ACTION_CORRECT,
            concept=concept,
            require_evidence=True,  # 纠错同样必须落在教材上（§7.3）
            reveal_answer=True,  # 纠错要指出正确方向
            require_student_reply=True,
            evidence_sufficient=bool(state.get("evidence_sufficient")),
            params={
                "conflicts": conflicts,
                "user_input": str(state.get("user_input") or ""),
                "query": query,
                "memory_note": str(state.get("memory_note") or ""),
            },
            reason="出现稳定错误或概念混淆，转入纠错",
        ),
    )

    citations = list(result.evidence)
    degraded = bool(result.metadata.get("degraded_from"))
    if result.status == "insufficient_evidence":
        reason = "证据不足（无检索命中或检索不可用），按 §7.3 不给出任何来源，未调用模型"
    elif degraded:
        reason = "检索到证据但模型调用失败，按失败表述返回且不附引用"
    else:
        reason = f"针对稳定错误生成纠错与对比例，依据 {len(citations)} 条可定位教材片段"

    # ---- 作答事实：交给数据组（§16.3 交接 / §18.2 契约）----
    # 判分缺失（None）或题目不可追踪时由 attempt_gate 给出显式原因，不产出（§13.1）
    attempt, skip_reason = await emit_attempt(
        port, state, correct=state.get("last_answer_correct")
    )

    await emit_decision(
        port,
        state,
        NODE,
        ACTION_CORRECT,
        reason,
        citation_count=len(citations),
        generation_skipped=result.status == "insufficient_evidence",
        source_attached=bool(citations),
        misconception_count=len(misconceptions),
        attempt_recorded=attempt is not None,
        attempt_id=str((attempt or {}).get("attempt_id") or ""),
        attempt_skip_reason=skip_reason,
        capability=result.capability,
        capability_status=result.status,
    )
    await emit_exited(
        port, state, NODE, response_text=result.content, citations=citations, action=ACTION_CORRECT
    )

    update: dict[str, Any] = {
        "action": ACTION_CORRECT,
        "response_text": result.content,
        "emotion": EMOTION_BY_ACTION[ACTION_CORRECT],
        "citations": citations,
        # 纠错后清零连续错误与提示级别，下一轮从轻提示重新开始（见模块 docstring）
        "wrong_streak": 0,
        "hint_level": 0,
        "strategy_note": f"correct:evidence={len(citations)}",
    }
    if citations:
        update["retrieved_evidence_refs"] = citations
    return update


__all__ = ["NODE", "correct"]