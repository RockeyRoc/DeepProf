"""Test 节点：给出"出题/评价"的决策，不生成题目与评价正文
（DESIGNv0.6 §4.4 / §6.2 / §6.3 / §12）。

进入条件：需要验证理解或间隔复习（Assess 决策为 test）。

本节点只做 WHAT：

1. 算出 `reply_frame`（出题 / 判对 / 判错 / 判分缺失）与难度粗分——
   两者都是教学策略，因此留在策略层；
2. 产出 PedagogicalDecision(action=test, mode=..., 判分状态...)；
3. 用 CapabilityResult 更新学情计数（judgement / wrong_streak / attempt_count）

题目正文、评价正文、开头结尾的框架文案、"题库未接入/判分未接入"的说明，
全部由 bindings.ACTION_TEST 声明（按 mode 选 Skill 结果字段、按 reply_frame
选框架文案），节点不再拼字符串。

诚实原则（本项目当前状态）仍然成立，且现在由数据保证：
- 课程题库、题目难度标定与自动判分**尚未接入**（题库与来源映射由许阳毅负责，
  自动判分链路由欧阳文凯负责），因此出题走 Quiz Skill 的模型即时生成，
  回复里必须声明"题库未接入"（绑定的后缀文案）；
- 判分只使用调用方给出的结构化 `last_answer_correct`；若为 None，
  则**不更新 wrong_streak**、不写成学情标签（§13.1），措辞也换成"先对齐"；
- **作答事实（Attempt）**由本节点按 §18.2 交给数据组（§16.3"向数据组发送 Attempt"，
  契约由数据组定义在 models/learner/attempt.py）：产出条件是
  "归属明确 + 判分可靠 + 题目可追踪"（policies.attempt_gate），
  当前题库未接入 → 没有 item_id → 显式不产出，并在决策事件里写明原因。

只依赖 RuntimePort：execute / emit。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimePort

from ..contracts import PedagogicalDecision
from ..policies import (
    ACTION_TEST,
    EMOTION_BY_ACTION,
    REPLY_FRAME_CORRECT,
    REPLY_FRAME_INCORRECT,
    REPLY_FRAME_QUIZ,
    REPLY_FRAME_UNKNOWN,
)
from ..state import PedagogyState
from . import dispatch, emit_attempt, emit_decision, emit_entered, emit_exited

NODE = "test"

#: 评价模式：需要读学生对本题的作答
MODE_EVALUATE = "evaluate"
#: 出题模式：本轮没有作答可评价
MODE_GENERATE = "generate"


async def test(state: PedagogyState, port: RuntimePort) -> dict[str, Any]:
    """出题（无作答）或评价作答（有作答），并更新尝试/连续错误计数。"""
    concept = str(state.get("current_concept") or "")
    user_input = str(state.get("user_input") or "")
    attempt_count = int(state.get("attempt_count") or 0)
    wrong_streak = int(state.get("wrong_streak") or 0)
    correct = state.get("last_answer_correct")  # bool | None
    mode = MODE_EVALUATE if user_input else MODE_GENERATE
    frame = _reply_frame(mode, correct)
    mode_text = "生成自检题" if mode == MODE_GENERATE else "评价学生作答"

    await emit_entered(
        port,
        state,
        NODE,
        concept=concept,
        mode=mode,
        attempt_count=attempt_count,
        wrong_streak=wrong_streak,
        has_judgement=correct is not None,
    )

    result = await dispatch(
        port,
        state,
        PedagogicalDecision(
            action=ACTION_TEST,
            concept=concept,
            reveal_answer=frame == REPLY_FRAME_CORRECT,  # 只有"判对"这一帧会说出结论
            require_student_reply=True,
            evidence_sufficient=bool(state.get("evidence_sufficient")),
            params={
                "mode": mode,
                "reply_frame": frame,
                "difficulty": _difficulty(attempt_count, wrong_streak),
                "item_type": "short_answer",
                "student_answer": user_input,
                "learning_goal": str(state.get("learning_goal") or ""),
                "evidence_refs": list(state.get("retrieved_evidence_refs") or []),
            },
            reason=f"需要验证理解（累计尝试 {attempt_count} 次）：{mode_text}",
        ),
    )

    # ---- 学情计数：判分三态必须分开处理（§13.1 不贴永久标签）----
    judgement = "not_applicable"
    if mode == MODE_GENERATE:
        reason = "按绑定声明的出题能力生成自检题；题库未接入，题目仅用于即时自检"
    elif correct is True:
        judgement = "correct"
        wrong_streak = 0
        reason = "学生作答被判定正确：清零连续错误计数"
    elif correct is False:
        judgement = "incorrect"
        wrong_streak += 1
        reason = f"学生作答被判定错误：连续答错累计 {wrong_streak} 次"
    else:
        judgement = "unknown"
        reason = "缺少可靠判分（自动判分未接入），不更新错误计数，也不写成学情标签"
    if mode == MODE_EVALUATE:
        attempt_count += 1

    # ---- 作答事实：交给数据组（§16.3 交接 / §18.2 契约）----
    # 出题模式本轮没有作答，判分三态里的"判不了"也不是作答事实：
    # 两种情况都只由 attempt_gate 给出显式原因，不产出 Attempt（§13.1）。
    judged = correct if mode == MODE_EVALUATE else None
    attempt, skip_reason = await emit_attempt(port, state, correct=judged)

    await emit_decision(
        port,
        state,
        NODE,
        ACTION_TEST,
        reason,
        quiz_skill_status=str(result.metadata.get("skill_status") or result.status),
        item_bank_connected=bool(result.metadata.get("item_bank_connected")),
        judgement=judgement,
        answer_leaked=judgement == "correct",
        attempt_recorded=attempt is not None,
        attempt_id=str((attempt or {}).get("attempt_id") or ""),
        attempt_skip_reason=skip_reason,
        capability=result.capability,
        capability_status=result.status,
    )
    await emit_exited(
        port, state, NODE, response_text=result.content, citations=[], action=ACTION_TEST
    )

    return {
        "action": ACTION_TEST,
        "response_text": result.content,
        "emotion": EMOTION_BY_ACTION[ACTION_TEST],
        "citations": [],
        "attempt_count": attempt_count,
        "wrong_streak": wrong_streak,
        "strategy_note": f"test:{mode}:{judgement}",
    }


def _reply_frame(mode: str, correct: bool | None) -> str:
    """把 mode 与判分三态映射成回复框架的键（§6.2 Test / §13.1）。

    出题与"判分缺失"都拿不到判分，因此不能只靠 correct 选帧——
    把 mode 一起编进键里，绑定才能为"出题"和"判不了"分别声明文案。
    """
    if mode == MODE_GENERATE:
        return REPLY_FRAME_QUIZ
    if correct is True:
        return REPLY_FRAME_CORRECT
    if correct is False:
        return REPLY_FRAME_INCORRECT
    return REPLY_FRAME_UNKNOWN


def _difficulty(attempt_count: int, wrong_streak: int) -> str:
    """按作答历史粗分难度（题库接入后应改为按 IRT 难度参数选题，§18.1）。"""
    if wrong_streak >= 1 or attempt_count <= 1:
        return "basic"
    return "intermediate"


__all__ = ["MODE_EVALUATE", "MODE_GENERATE", "NODE", "test"]