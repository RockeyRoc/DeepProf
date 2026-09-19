"""Reflect 节点：给出"换策略"的决策，不生成建议正文（DESIGNv0.4 §4.4 / §6.2 / §7.3）。

进入条件（由 Assess 决策或 router 兜底）：
- 达到最大轮次 max_turns；
- 或后续可扩展为"多轮无进展/工具异常"等信号。

本节点只做 WHAT：
1. 汇总"为什么走到这一步"（优先用 Assess 给的理由，否则说明是轮次上限）；
2. 产出 PedagogicalDecision(action=reflect)，把"学生是否已停止"作为参数交给
   Runtime——换策略建议与收束语都是绑定声明的模板（确定性渲染，不经模型）；
3. 把 next_action 置为结束态，保证图一定收敛，不会形成无限追问（§16.3 验收）。

只依赖 RuntimePort：execute / emit。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimePort

from ..contracts import PedagogicalDecision
from ..policies import (
    ACTION_END,
    ACTION_REFLECT,
    EMOTION_BY_ACTION,
    MAX_TURNS_DEFAULT,
    REFLECT_STRATEGY_OPTIONS,
)
from ..state import PedagogyState
from . import dispatch, emit_decision, emit_entered, emit_exited

NODE = "reflect"


async def reflect(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    """输出策略调整建议，并把本轮标记为结束态。"""
    concept = str(state.get("current_concept") or "")
    turn_count = int(state.get("turn_count") or 0)
    max_turns = int(state.get("max_turns") or MAX_TURNS_DEFAULT)
    stopped = bool(state.get("student_stopped"))
    assessment = state.get("last_assessment") or {}

    await emit_entered(
        port,
        state,
        NODE,
        concept=concept,
        turn_count=turn_count,
        max_turns=max_turns,
        student_stopped=stopped,
    )

    reason = (
        "学生已停止，Reflect 只负责收束"
        if stopped
        else str(
            assessment.get("reason")
            or f"达到最大轮次 {turn_count}/{max_turns}，转 Reflect 换策略（§7.3）"
        )
    )
    result = await dispatch(
        port,
        state,
        PedagogicalDecision(
            action=ACTION_REFLECT,
            concept=concept,
            reveal_answer=False,
            require_student_reply=False,
            evidence_sufficient=bool(state.get("evidence_sufficient")),
            params={"stopped": stopped, "turn_count": turn_count, "max_turns": max_turns},
            reason=reason,
        ),
    )

    await emit_decision(
        port,
        state,
        NODE,
        ACTION_REFLECT,
        reason,
        strategy_options=list(REFLECT_STRATEGY_OPTIONS),
        prior_action=str(state.get("action") or state.get("next_action") or ""),
        capability=result.capability,
        capability_status=result.status,
    )
    await emit_exited(
        port, state, NODE, response_text=result.content, citations=[], action=ACTION_REFLECT
    )

    return {
        "action": ACTION_REFLECT,
        "response_text": result.content,
        "emotion": EMOTION_BY_ACTION[ACTION_REFLECT],
        "citations": [],
        "next_action": ACTION_END,
        "strategy_note": "reflect:strategy_switch",
    }


__all__ = ["NODE", "reflect"]