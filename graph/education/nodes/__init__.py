"""图节点公共设施（DESIGNv0.4 §5.4 / §6.4 / §7.1 / §7.3）。

只放三类东西，避免各节点各写一遍：

1. **事件发射**：节点进入 / 决策 / 退出统一走这里，保证每条事件都带
   session_id、trace_id、source 与决策依据，便于复盘与可解释性分析（§3.4、§5.4）；
2. **端口调用上下文与决策分发**：把图状态压成 RuntimePort 需要的 ctx dict（§7.1），
   并把 PedagogicalDecision 交给 Runtime 执行、取回 CapabilityResult（§4.4）；
3. **作答事实交接**：把一次作答（Attempt）按 §18.2 契约校验后交给数据组
   （Test / Correct 节点共用，见 emit_attempt）。

边界（§4.4）：本包只依赖 `runtime.core.ports` 的 RuntimePort 契约，
不 import 任何 Provider / Storage / ToolRegistry / 数据库 / 模型 SDK。
"怎么把决策做出来"（模板、提示词、证据校验、模型调用）全在 Runtime 侧的能力层，
节点只声明"应该做什么"——证据的获取与可定位校验因此也不在这里
（唯一主人是 runtime/capabilities.py 的检索能力，§7.3）。
"""
from __future__ import annotations

from typing import Any

from runtime.core.message import new_id, utc_now
from runtime.core.ports import RuntimePort

from models.learner import Attempt

from ..contracts import CapabilityResult, PedagogicalDecision
from ..policies import (
    EVENT_ATTEMPT,
    EVENT_DECISION,
    EVENT_NODE_ENTERED,
    EVENT_NODE_EXITED,
    GRAPH_SOURCE,
    attempt_gate,
)
from ..state import PedagogyState


# ======================================================================
# 一、RuntimePort 调用上下文与决策分发
# ======================================================================
def runtime_ctx(state: PedagogyState) -> dict[str, Any]:
    """构造端口调用上下文（dict 形态，RuntimeContext.from_dict 可解析）。

    只带关联标识与来源，不携带任何正文——Runtime 侧凭 session_id/trace_id
    把模型、工具、记忆事件串到同一条轨迹上（§18.2）。
    """
    return {
        "session_id": str(state.get("session_id") or ""),
        "learner_id": str(state.get("learner_id") or ""),
        "trace_id": str(state.get("trace_id") or ""),
        "source": GRAPH_SOURCE,
        "metadata": {
            "graph": "education",
            "concept": str(state.get("current_concept") or ""),
            "turn_count": int(state.get("turn_count") or 0),
        },
    }


async def dispatch(
    port: RuntimePort, state: PedagogyState, decision: PedagogicalDecision
) -> CapabilityResult:
    """把声明式决策交给 Runtime 执行，取回结构化结果。

    节点不解析字符串、也不做兜底拼接：内容、引用与降级状态都由
    CapabilityResult 给出（status / content / evidence / metadata）。

    装配失败（no_binding / capability_not_found / invalid_request）时
    content 为空——这是**有意的**：Runtime 不得凭空产出教学话术，
    节点也不应再自带一份兜底文案（那等于把 HOW 又搬回图里）。
    失败可在决策事件的 capability_status 与 /health 的 action_bindings 里看到。
    """
    return CapabilityResult.from_dict(
        await port.execute(decision.to_dict(), runtime_ctx(state))
    )


# ======================================================================
# 二、事件发射（§5.4 pedagogy.node.entered / pedagogy.decision / pedagogy.node.exited）
# ======================================================================
async def _publish(
    port: RuntimePort,
    state: PedagogyState,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    await port.emit(
        {
            "type": event_type,
            "session_id": str(state.get("session_id") or ""),
            "trace_id": str(state.get("trace_id") or ""),
            "source": GRAPH_SOURCE,
            "payload": payload,
        }
    )


async def emit_entered(
    port: RuntimePort, state: PedagogyState, node: str, **extra: Any
) -> None:
    """节点进入事件。payload 里放进入条件所需的判断依据，不放正文。"""
    await _publish(
        port,
        state,
        EVENT_NODE_ENTERED,
        {
            "node": node,
            "turn_count": int(state.get("turn_count") or 0),
            "hint_level": int(state.get("hint_level") or 0),
            "attempt_count": int(state.get("attempt_count") or 0),
            **extra,
        },
    )


async def emit_decision(
    port: RuntimePort,
    state: PedagogyState,
    node: str,
    action: str,
    reason: str,
    **extra: Any,
) -> None:
    """教学决策事件：action + 原因 + 证据/尝试等依据（可解释性分析的核心数据）。"""
    await _publish(
        port,
        state,
        EVENT_DECISION,
        {
            "node": node,
            "action": action,
            "reason": reason,
            "hint_level": int(state.get("hint_level") or 0),
            "evidence_sufficient": bool(state.get("evidence_sufficient")),
            "evidence_count": len(state.get("retrieved_evidence_refs") or []),
            "attempt_count": int(state.get("attempt_count") or 0),
            "wrong_streak": int(state.get("wrong_streak") or 0),
            "turn_count": int(state.get("turn_count") or 0),
            "max_turns": int(state.get("max_turns") or 0),
            **extra,
        },
    )


async def emit_exited(
    port: RuntimePort,
    state: PedagogyState,
    node: str,
    *,
    response_text: str = "",
    citations: list[dict] | None = None,
    **extra: Any,
) -> None:
    """节点退出事件。只记录长度等元信息，不把回复正文写进轨迹（§13.2）。

    显式传入本轮刚生成的文本与引用，而不是读入参 state——
    入参 state 还是"进入节点时"的快照，读它会记录到上一轮的残值。
    """
    await _publish(
        port,
        state,
        EVENT_NODE_EXITED,
        {
            "node": node,
            "response_chars": len(str(response_text or "")),
            "citation_count": len(citations or []),
            **extra,
        },
    )


# ======================================================================
# 三、作答事实（Attempt）：教育组 → 数据组的交接（§16.3 / §18.2）
# ======================================================================
async def emit_attempt(
    port: RuntimePort,
    state: PedagogyState,
    *,
    correct: bool | None,
) -> tuple[dict[str, Any] | None, str]:
    """判分可靠且题目可追踪时，把一条 Attempt 交给数据组（§16.3 交接 / §18.2 契约）。

    三件事分开，各有主人：

    1. **要不要产出**由 policies.attempt_gate 定义（归属明确 + 判分可靠 + 题目可追踪）；
    2. **契约是否合法**由数据组的 models.learner.attempt.Attempt 校验——
       字段漂移会在这里就报错，而不是写进数据组仓库才发现；
    3. **怎么送达**是发一条 ``pedagogy.attempt`` 事件（§5.4 事件落盘后可回放），
       Attempt 本身不带 session_id / trace_id，两条事实靠事件外层字段对齐（§18.2）。
       §16.6 说的"来源"由事件外层的 ``source`` 表达，不写进 Attempt（契约 extra=forbid）。

    返回 ``(attempt_dict, skip_reason)``：产出时 skip_reason 为空串；
    未产出时 attempt 为 None 并给出显式原因，供决策事件记录
    "这一轮为什么没有答题记录"——静默跳过会让人以为链路坏了。
    """
    item_id = str(state.get("item_id") or "")
    learner_id = str(state.get("learner_id") or "")
    allowed, skip_reason = attempt_gate(correct, item_id, learner_id)
    if not allowed:
        return None, skip_reason

    attempt = Attempt(
        attempt_id=new_id("attempt"),
        learner_id=learner_id,
        item_id=item_id,
        concept_ids=[str(state.get("current_concept") or "")],
        correct=bool(correct),
        timestamp=utc_now(),
        hint_count=int(state.get("hint_level") or 0),
    )
    payload = attempt.model_dump()
    await _publish(port, state, EVENT_ATTEMPT, payload)
    return payload, ""


__all__ = [
    "EVENT_ATTEMPT",
    "EVENT_DECISION",
    "EVENT_NODE_ENTERED",
    "EVENT_NODE_EXITED",
    "GRAPH_SOURCE",
    "dispatch",
    "emit_attempt",
    "emit_decision",
    "emit_entered",
    "emit_exited",
    "runtime_ctx",
]