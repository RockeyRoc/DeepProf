"""Ask 节点：给出追问决策，不生成问题正文（DESIGNv0.4 §4.4 / §6.2 / §6.3）。

进入条件：学生具备推理基础（Assess 决策为 ask）。

本节点只做 WHAT：产出 PedagogicalDecision(action=ask)，交给 Runtime 执行。
问题由 Socratic Skill 生成、Skill 不可用时退回策略模板、证据不足时追加
"不含引用"说明——这三件事都是可评审的教学策略，因此写在 bindings 的数据里，
不写在节点里，也不写在 Skill 里。

硬性要求仍然成立：必须"避免过早泄露答案"，决策固定 `avoid_answer=True`，
Skill 的系统提示词据此约束模型；该约束当前是 prompt_only，需教师抽检（§16.7）。

只依赖 RuntimePort：execute / emit。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimePort

from ..contracts import PedagogicalDecision
from ..policies import ACTION_ASK, EMOTION_BY_ACTION
from ..state import PedagogyState
from . import dispatch, emit_decision, emit_entered, emit_exited

NODE = "ask"


async def ask(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    """生成一个递进追问，并说明它对应的认知目标。"""
    concept = str(state.get("current_concept") or "")
    await emit_entered(
        port,
        state,
        NODE,
        concept=concept,
        evidence_sufficient=bool(state.get("evidence_sufficient")),
        hint_level=int(state.get("hint_level") or 0),
    )

    result = await dispatch(
        port,
        state,
        PedagogicalDecision(
            action=ACTION_ASK,
            concept=concept,
            level=int(state.get("hint_level") or 0),
            reveal_answer=False,  # 硬性要求：不得提前给出结论（§6.3）
            require_student_reply=True,
            evidence_sufficient=bool(state.get("evidence_sufficient")),
            params={
                "learning_goal": str(state.get("learning_goal") or ""),
                "user_input": str(state.get("user_input") or ""),
                "attempt_count": int(state.get("attempt_count") or 0),
            },
            reason="学生具备推理基础，转入递进追问（不泄露结论）",
        ),
    )

    # Skill 命中 → socratic_skill；被策略模板兜底 → policy_template。
    # 判定依据来自能力层的降级标记，节点不自己解析正文。
    degraded = bool(result.metadata.get("degraded_from"))
    source_kind = "policy_template" if degraded else "socratic_skill"
    reason = (
        "Socratic Skill 不可用，改用策略模板追问"
        if degraded
        else "Socratic Skill 生成递进追问（avoid_answer=True，不泄露结论）"
    )

    await emit_decision(
        port,
        state,
        NODE,
        ACTION_ASK,
        reason,
        question_source=source_kind,
        cognitive_goal=f"澄清「{concept or '当前主题'}」的关键条件与适用范围",
        source_attached=False,
        capability=result.capability,
        capability_status=result.status,
    )
    await emit_exited(
        port, state, NODE, response_text=result.content, citations=[], action=ACTION_ASK
    )

    return {
        "action": ACTION_ASK,
        "response_text": result.content,
        "emotion": EMOTION_BY_ACTION[ACTION_ASK],
        "citations": [],
        "strategy_note": f"ask:{source_kind}",
    }


__all__ = ["NODE", "ask"]