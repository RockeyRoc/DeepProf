"""UpdateProfile 节点：把本轮教学结果写成学情增量（DESIGNv0.6 §6.2 / §5.5 / §18.1）。

进入条件：教学节点完成或会话结束。

写入纪律（§5.5）：任何长期记忆都必须带**来源、置信度、过期策略与可撤回标记**；
本节点据此构造记录，缺 learner_id 时宁可不写，也不制造无法归属的学情结论。

诚实原则（§18.1）：BKT/IRT 学情模型尚未接入（欧阳文凯负责），
本节点先发一条 diagnose 决策问学情模型要结构化估计；Skill 返回 not_implemented 时，
退化为**规则化观察**，并在内容里写明"未接入模型、不代表掌握/未掌握"（§13.1）。
该调用不声明 content_field，因此"只问状态"——not_implemented 也算调用成功，
结论记在决策事件的 diagnosis_status 里，而不是被当成执行失败。

只依赖 RuntimePort：execute / emit。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimePort

from ..contracts import PedagogicalDecision
from ..policies import (
    ACTION_DIAGNOSE,
    ACTION_END,
    ACTION_UPDATE_PROFILE,
    MEMORY_DISCLAIMER,
    MEMORY_SCOPE_EPISODIC,
    MEMORY_SCOPE_LONG_TERM,
    MISCONCEPTION_LABEL_FIELD,
    RULE_MODEL_VERSION,
    STUDENT_CONTEXT_MAX_CHARS,
    TAG_INTERVENTION,
    TAG_MISCONCEPTION,
    TAG_OBSERVATION,
    TAG_STUDENT_CONTEXT,
    memory_confidence,
)
from ..state import PedagogyState
from . import dispatch, emit_decision, emit_entered, emit_exited

NODE = "update_profile"


async def update_profile(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    """生成本轮学情增量并写入记忆，然后结束本轮。"""
    learner_id = str(state.get("learner_id") or "")
    concept = str(state.get("current_concept") or "")
    action = str(state.get("action") or state.get("next_action") or "")
    turn_count = int(state.get("turn_count") or 0)
    attempt_count = int(state.get("attempt_count") or 0)
    wrong_streak = int(state.get("wrong_streak") or 0)
    hint_level = int(state.get("hint_level") or 0)
    evidence_refs = list(state.get("retrieved_evidence_refs") or [])
    misconceptions = [str(item) for item in (state.get("misconceptions") or [])]

    await emit_entered(
        port,
        state,
        NODE,
        concept=concept,
        action=action,
        turn_count=turn_count,
        has_learner_id=bool(learner_id),
    )

    # ---- 1) 先问学情模型（当前返回 not_implemented，规则化降级，见模块 docstring）----
    diagnosis = await dispatch(
        port,
        state,
        PedagogicalDecision(
            action=ACTION_DIAGNOSE,
            concept=concept,
            require_evidence=False,
            require_student_reply=False,
            params={
                "learner_id": learner_id,
                "attempt_count": attempt_count,
                "wrong_streak": wrong_streak,
                "hint_level": hint_level,
                "misconceptions": misconceptions,
                "last_answer_correct": state.get("last_answer_correct"),
                "recent_actions": [action] if action else [],
                "evidence_count": len(evidence_refs),
            },
            reason="本轮结束时先问学情模型要结构化估计，拿不到就退化为规则化观察",
        ),
    )
    # 不声明 content_field 的调用：结论就是 Skill 自己回的状态（not_implemented / ok…）
    diagnosis_status = str(diagnosis.metadata.get("skill_status") or diagnosis.status)

    # ---- 2) 构造并写入记忆增量 ----
    records: list[dict[str, Any]] = []
    if not learner_id:
        reason = "缺少 learner_id，无法归属 learner 记忆，跳过写入以避免跨学习者污染（§13.2）"
    else:
        records = _build_records(state, action=action, diagnosis_status=diagnosis_status)
        # 写入同样是一条决策：记录由策略层构造，落盘口径由绑定声明
        written = await dispatch(
            port,
            state,
            PedagogicalDecision(
                action=ACTION_UPDATE_PROFILE,
                concept=concept,
                require_evidence=False,
                require_student_reply=False,
                params={"records": records},
                reason=f"把本轮 {len(records)} 条学情增量落盘（source/confidence/revocable 齐备）",
            ),
        )
        if written.status == "success":
            reason = (
                f"写入 {len(records)} 条学情增量（source/confidence/revocable 齐备）；"
                f"诊断来源={diagnosis_status}"
            )
        else:
            # 装配/能力层层面的失败（如 write_memory 未注册）必须说清楚，
            # 不能让"下一轮读不到记忆"变成莫名其妙的行为。
            # 注意：记录本身非法（缺 learner_id / content）时存储会抛异常，
            # 这里**不吞**——那属于本节点构造记录时的 bug，应该炸出来。
            reason = (
                f"构造了 {len(records)} 条学情增量但写入未成功"
                f"（capability_status={written.status}）；本轮不声称已写入"
            )
            records = []  # 没写成功就不在事件里声称写了

    await emit_decision(
        port,
        state,
        NODE,
        ACTION_END,
        reason,
        memory_written=len(records),
        memory_scopes=[str(item.get("scope") or "") for item in records],
        diagnosis_status=diagnosis_status,
        confidence=(records[0].get("confidence") if records else None),
    )
    await emit_exited(
        port, state, NODE, response_text="", citations=[], action=ACTION_END
    )

    # learner_state_ref 由 Assess 依据真实记忆记录写入；write_memory 不返回 record_id，
    # 本节点无法给更新的"真实引用"，因此不覆盖（避免用占位 URI 盖掉可解析的引用）。
    return {
        "next_action": ACTION_END,
        "strategy_note": f"update_profile:writes={len(records)}",
    }


def _build_records(
    state: PedagogyState, *, action: str, diagnosis_status: str
) -> list[dict[str, Any]]:
    """构造四类学情增量：掌握观察、本轮干预、错误模式、学生自述背景。

    所有记录都带 source（可追溯到会话/轨迹/节点）、confidence、revocable=True，
    并为学情模型产生的结论预留 model_version（§5.5、§18.2）。
    记录形状遵循 runtime 的 MemoryRecord 契约（scope / concept_ids / tags / metadata）。
    """
    learner_id = str(state.get("learner_id") or "")
    concept = str(state.get("current_concept") or "")
    session_id = str(state.get("session_id") or "")
    trace_id = str(state.get("trace_id") or "")
    turn_count = int(state.get("turn_count") or 0)
    attempt_count = int(state.get("attempt_count") or 0)
    wrong_streak = int(state.get("wrong_streak") or 0)
    hint_level = int(state.get("hint_level") or 0)
    evidence_sufficient = bool(state.get("evidence_sufficient"))
    evidence_refs = list(state.get("retrieved_evidence_refs") or [])
    misconceptions = [str(item) for item in (state.get("misconceptions") or [])]
    correct = state.get("last_answer_correct")

    source = f"graph:{NODE}/session:{session_id}/trace:{trace_id}/turn:{turn_count}"
    confidence = memory_confidence(evidence_sufficient, attempt_count)
    concept_ids = [concept] if concept else []
    metadata = {
        "action": action,
        "turn_count": turn_count,
        "attempt_count": attempt_count,
        "wrong_streak": wrong_streak,
        "hint_level": hint_level,
        "evidence_count": len(evidence_refs),
        "evidence_sufficient": evidence_sufficient,
        "diagnosis_status": diagnosis_status,
        "model_version": RULE_MODEL_VERSION,
        "graph_source": "deepprof.graph.education",
    }

    records: list[dict[str, Any]] = [
        {
            "learner_id": learner_id,
            "scope": MEMORY_SCOPE_LONG_TERM,
            "concept_ids": concept_ids,
            "tags": [TAG_OBSERVATION],
            "content": (
                f"知识点「{concept or '未指定'}」规则化观察：本轮动作={action}，"
                f"累计尝试={attempt_count}，连续答错={wrong_streak}，提示级别={hint_level}，"
                f"证据数={len(evidence_refs)}。{MEMORY_DISCLAIMER}"
            ),
            "source": source,
            "confidence": confidence,
            "expires_at": None,  # 不自动过期，由用户查看/纠正/删除（§13.2）
            "revocable": True,
            "metadata": metadata,
        },
        {
            "learner_id": learner_id,
            "scope": MEMORY_SCOPE_EPISODIC,
            "concept_ids": concept_ids,
            "tags": [TAG_INTERVENTION],
            "content": (
                f"第 {turn_count} 轮教学干预：动作={action}，情感标签={state.get('emotion') or ''}，"
                f"上一轮判分={_judgement_text(correct)}，证据数={len(evidence_refs)}。"
            ),
            "source": source,
            "confidence": 0.9,  # 直接观测到的教学事件，事实置信度高
            "expires_at": None,
            "revocable": True,
            "metadata": metadata,
        },
    ]

    for label in misconceptions:
        records.append(
            {
                "learner_id": learner_id,
                "scope": MEMORY_SCOPE_LONG_TERM,
                "concept_ids": concept_ids,
                "tags": [TAG_MISCONCEPTION],
                "content": (
                    f"疑似错误概念：{label}（来源：第 {turn_count} 轮作答与追问，"
                    f"未经学情模型确认）。{MEMORY_DISCLAIMER}"
                ),
                "source": source,
                "confidence": min(confidence, 0.4),  # 误解判定证据更弱，置信度上限更低
                "expires_at": None,
                "revocable": True,
                # 标签字段名与 Assess 读取时共用同一常量（改一处即可，见 policies）
                "metadata": {**metadata, MISCONCEPTION_LABEL_FIELD: label, "concept": concept},
            }
        )

    # 学生自述背景：有界摘录本轮输入（如“之前学过/没学过什么、用什么方法做过题”）。
    # 不写这类记录，下一轮的生成上下文就无从个性化（§6.2）。
    # 摘录有界（STUDENT_CONTEXT_MAX_CHARS）、revocable=True，学生可随时查看/更正/删除。
    user_input = str(state.get("user_input") or "").strip()
    if user_input:
        records.append(
            {
                "learner_id": learner_id,
                "scope": MEMORY_SCOPE_LONG_TERM,
                "concept_ids": concept_ids,
                "tags": [TAG_STUDENT_CONTEXT],
                "content": (
                    f"学生自述背景（原文摘录，可撤回）：{user_input[:STUDENT_CONTEXT_MAX_CHARS]}"
                ),
                "source": source,
                "confidence": 0.9,  # 学生直接自述，事实置信度高
                "expires_at": None,
                "revocable": True,
                "metadata": {
                    "model_version": RULE_MODEL_VERSION,
                    "graph_source": "deepprof.graph.education",
                    "turn_count": turn_count,
                },
            }
        )
    return records


def _judgement_text(correct: Any) -> str:
    if correct is True:
        return "正确"
    if correct is False:
        return "错误"
    return "未知（判分未接入）"


__all__ = ["NODE", "update_profile"]