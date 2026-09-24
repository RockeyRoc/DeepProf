"""Hint 节点：给出决策，不生成提示正文（DESIGNv0.6 §4.4 / §6.2 / §6.3）。

进入条件：尝试受阻但不宜直接给答案（Assess 决策为 hint）。

本节点只做 WHAT（策略），不碰 HOW（执行）：

1. 用 policies.next_hint_level 算出本轮级别（连续答错则升一级，封顶不泄露答案）；
2. 产出 PedagogicalDecision(action=hint, level=...)；
3. 交给 Runtime 执行，拿回 CapabilityResult。

提示话术、模板选择、甚至"是否经过模型"都在
graph/education/bindings.py 的 ACTION_BINDINGS 里声明，由组合根注入 Runtime。
节点因此不再持有任何提示文案——策略评审只需看 policies 与 bindings 两处。

为什么提示必须是确定性渲染而不是模型生成（见 bindings 里的注释）：
提示强度是教学实验的自变量（§3.4 研究问题 1），模板可评审、可复现，
且能从结构上保证到最高一级也不给最终答案。

只依赖 RuntimePort：execute / emit。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimePort

from ..contracts import PedagogicalDecision
from ..policies import (
    ACTION_HINT,
    EMOTION_BY_ACTION,
    HINT_MAX_LEVEL,
    next_hint_level,
)
from ..state import PedagogyState
from . import dispatch, emit_decision, emit_entered, emit_exited

NODE = "hint"


async def hint(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    """按升级规则给出一级提示（不含最终答案）。"""
    concept = str(state.get("current_concept") or "")
    current_level = int(state.get("hint_level") or 0)
    wrong_streak = int(state.get("wrong_streak") or 0)
    explicit_request = str(state.get("requested_action") or "").lower() == "hint"
    level = next_hint_level(current_level, max(wrong_streak, 1) if explicit_request else wrong_streak)

    await emit_entered(
        port, state, NODE, concept=concept, hint_level=current_level, wrong_streak=wrong_streak
    )

    reason = (
        f"尝试受阻（连续答错 {wrong_streak} 次）：提示级别 {current_level} → {level}"
        f"（上限 {HINT_MAX_LEVEL}），仍不给出最终答案"
        if wrong_streak > 0
        else f"学生主动求助且当前无连续错误，维持提示级别 {level}"
    )
    result = await dispatch(
        port,
        state,
        PedagogicalDecision(
            action=ACTION_HINT,
            concept=concept,
            level=level,  # 策略算出的提示级别；能力层按它选模板
            # reveal_answer=False：提示强度封顶由策略层决定（见下）
            reveal_answer=False,
            require_student_reply=True,
            evidence_sufficient=bool(state.get("evidence_sufficient")),
            params={"wrong_streak": wrong_streak, "hint_level_from": current_level},
            reason=reason,
        ),
    )

    await emit_decision(
        port,
        state,
        NODE,
        ACTION_HINT,
        reason,
        hint_level_from=current_level,
        hint_level_to=level,
        # 模板里没有最终答案，因此"不泄露答案"是结构保证，不是运行时判断
        answer_leaked=False,
        capability=result.capability,
        capability_status=result.status,
    )
    await emit_exited(
        port, state, NODE, response_text=result.content, citations=[], action=ACTION_HINT
    )

    return {
        "action": ACTION_HINT,
        "hint_level": level,
        "response_text": result.content,
        "emotion": EMOTION_BY_ACTION[ACTION_HINT],
        "citations": [],
        "strategy_note": f"hint:level={level}:{result.status}",
    }


__all__ = ["NODE", "hint"]
