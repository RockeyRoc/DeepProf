"""条件路由与教学动作决策（DESIGNv0.6 §6.2 / §7.3 / §16.3）。

本模块是“教师此刻应该怎么教”的唯一判定处，分两层：

1. `decide_action(state)` —— 纯函数决策，Assess 节点调用它并把结果写入
   `next_action` 与 `last_assessment`。判定依据全部来自状态字段与 policies 常量，
   优先级与理由见函数 docstring（这也是教师评审与实验复盘的对照口径）。
2. `route_from_*` —— LangGraph 条件边函数，把状态映射到下一个节点名或 END。
   它们只做“安全兜底”（停止、超轮次、未知动作），不重复业务判断，
   保证即使状态被外部写坏也不会走出非法分支。

覆盖的分支（§6.2 六类教学动作 + 退出）：
    Assess → Teach / Ask / Hint / Correct / Test / Reflect / END
"""

from __future__ import annotations

from langgraph.graph import END

from .policies import (
    ACTION_ASK,
    ACTION_CORRECT,
    ACTION_END,
    ACTION_HINT,
    ACTION_NODES,
    ACTION_REFLECT,
    ACTION_TEACH,
    CORRECT_WRONG_STREAK,
    END_ACTIONS,
    HINT_MAX_LEVEL,
    MAX_TURNS_DEFAULT,
    MISCONCEPTION_LIMIT,
    QUIZ_AFTER_ATTEMPTS,
    requests_explanation,
)
from .state import PedagogyState


# ======================================================================
# 一、教学动作决策（Assess 节点使用）
# ======================================================================
def decide_action(state: PedagogyState) -> tuple[str, str]:
    """决定本轮教学动作，返回 (action, reason)。

    优先级与依据（§6.2 触发条件）：

    1. 学生主动停止（student_stopped）→ end
       不产出任何教学动作，直接收束（§16.3 验收：“学生停止时退出”）。
    2. 达到最大轮次（turn_count ≥ max_turns）→ reflect
       交由 Reflect 选择回退、换策略或求助教师，避免无限追问（§7.3）。
    3. 稳定错误 / 概念混淆 → correct
       概念混淆：误解条目 ≥ MISCONCEPTION_LIMIT 即可纠错；
       稳定错误：wrong_streak ≥ CORRECT_WRONG_STREAK **且提示阶梯已用尽**
       （hint_level ≥ HINT_MAX_LEVEL）。两者都表示继续追问只会强化错误
       （§6.2 Correct：“出现稳定错误或概念混淆”）。
    4. 尝试受阻 → hint
       wrong_streak ≥ 1 时给提示，从轻到重，每次升一级直至 HINT_MAX_LEVEL。
       第 3 条的 hint_level 条件保证这条阶梯不会被纠错提前打断，
       因此 1—3 级会依次真实出现，不存在“定义了却走不到”的档位。
       每一级（含最高级）都不给出最终答案：第 3 级起开始给脚手架，
       因此显式声明“不给出最终答案”，避免单次答错就纠错、剥夺学生自己推理的机会。
    5. 需要验证理解 → test
       attempt_count ≥ QUIZ_AFTER_ATTEMPTS 且本轮没有未处理的错误
       （有错时先走 Hint/Correct，测验留到状态干净之后，避免边讲边测）。
    6. 先验不足 → teach
       学生自述缺少先验（前置概念未学）且尚无作答尝试：此时追问等于要求他
       推理一个还没学过的前置概念，只会强化挫败；直接讲解并把起点前移到
       前置概念（§16.3“先验不足”样例，起讲点由 policies.teach_prior_note 决定）。
    7. 主动求讲解 / 概念缺失 → teach
       命中讲解意图且没有任何作答尝试（attempt_count == 0）：
       学生还没开始推理，此时直接分层讲解比追问更有效（§16.3“主动求讲解”样例）。
    8. 默认 → ask
       最保守也最不泄露答案的苏格拉底追问（§6.3 Socratic）。

    注意：证据不足**不**改变动作选择，只改变输出方式——Teach / Correct 在
    evidence_sufficient=False 时必须给出“证据不足”表述且不带任何来源（§7.3）。
    """
    if state.get("student_stopped"):
        return ACTION_END, "学生主动停止，本轮不再产出教学动作（§16.3）"

    turn_count = int(state.get("turn_count") or 0)
    max_turns = int(state.get("max_turns") or MAX_TURNS_DEFAULT)
    if turn_count >= max_turns:
        return (
            ACTION_REFLECT,
            f"达到最大轮次 {turn_count}/{max_turns}，转 Reflect 换策略或求助教师（§7.3）",
        )

    wrong_streak = int(state.get("wrong_streak") or 0)
    hint_level = int(state.get("hint_level") or 0)
    attempt_count = int(state.get("attempt_count") or 0)
    misconceptions = list(state.get("misconceptions") or [])

    requested_action = str(state.get("requested_action") or "").lower()
    if requested_action == "hint":
        if hint_level >= HINT_MAX_LEVEL:
            return ACTION_REFLECT, f"三级提示已用尽（{HINT_MAX_LEVEL}/{HINT_MAX_LEVEL}），结束本轮"
        return ACTION_HINT, "学习者主动请求提示；不依据未判分回答推断掌握度"
    if requested_action == "ask":
        return ACTION_ASK, "学习者主动请求苏格拉底追问"
    if requested_action in {"quiz", "test", "answer"}:
        return "test", "题库未配置；不生成替代题目或判分"

    # 稳定错误：连续答错达到阈值，**且提示阶梯已用尽**。
    # 加 hint_level 这一重条件，是为了让“从轻到重给提示”这条教学路径真正走得完：
    # 否则连错到阈值就会打断提示、直接纠错，高等级提示模板永远轮不到（等于死代码）。
    if (
        wrong_streak >= CORRECT_WRONG_STREAK
        and hint_level >= HINT_MAX_LEVEL
        and bool(state.get("evidence_sufficient"))
    ):
        return (
            ACTION_CORRECT,
            f"连续答错 {wrong_streak} 次（阈值 {CORRECT_WRONG_STREAK}）且教材证据充分，进入纠错",
        )
    if len(misconceptions) >= MISCONCEPTION_LIMIT:
        return (
            ACTION_CORRECT,
            f"累积误解 {len(misconceptions)} 条（阈值 {MISCONCEPTION_LIMIT}），判定为概念混淆，需指出冲突",
        )
    if wrong_streak >= 1:
        if hint_level < HINT_MAX_LEVEL:
            return (
                ACTION_HINT,
                f"尝试受阻（连续答错 {wrong_streak} 次）且提示级别 {hint_level}/{HINT_MAX_LEVEL} 未用尽，从轻到重给提示",
            )
        return ACTION_REFLECT, f"提示已达上限 {hint_level}/{HINT_MAX_LEVEL}，停止重复提示并结束本轮"
    if attempt_count >= QUIZ_AFTER_ATTEMPTS:
        return "test", "题库未配置；不生成替代题目或判分"
    if attempt_count == 0 and bool(state.get("prior_knowledge_gap")):
        return (
            ACTION_TEACH,
            "学生自述缺少先验（前置概念未学）且尚无作答尝试：先讲解前置概念，"
            "不要求他自行推理（§16.3 先验不足样例）",
        )
    if attempt_count == 0 and requests_explanation(str(state.get("user_input") or "")):
        return ACTION_TEACH, "学生主动求讲解且尚无作答尝试，概念缺失，适合分层讲解并给出来源"
    return ACTION_ASK, "学生具备推理基础且无错误轨迹，用苏格拉底追问推进（不泄露答案）"


# ======================================================================
# 二、条件边
# ======================================================================
def route_from_assess(state: PedagogyState) -> str:
    """Assess 出口：六类教学动作 + reflect + END（§6.2）。"""
    if state.get("student_stopped"):
        return END
    action = str(state.get("next_action") or "")
    if action in END_ACTIONS:
        return END
    if _at_turn_limit(state):
        # 安全兜底：即使 next_action 被外部写坏或决策未执行，也不会继续追问
        return ACTION_REFLECT
    return action if action in ACTION_NODES else ACTION_ASK


def route_from_teaching(state: PedagogyState) -> str:
    """每个请求只推进一轮；长期学情写入暂未接入。"""
    return END


def route_from_test(state: PedagogyState) -> str:
    """题库和可靠判分未接入；Test 只说明缺口并结束本轮。"""
    return END


def route_from_reflect(state: PedagogyState) -> str:  # noqa: ARG001 - 条件边需保持统一签名
    """本批只保存会话与教学事件，不写长期学情记忆。"""
    return END


# ======================================================================
# 三、内部工具
# ======================================================================
def _at_turn_limit(state: PedagogyState) -> bool:
    turn_count = int(state.get("turn_count") or 0)
    max_turns = int(state.get("max_turns") or MAX_TURNS_DEFAULT)
    return turn_count >= max_turns


__all__ = [
    "decide_action",
    "route_from_assess",
    "route_from_reflect",
    "route_from_teaching",
    "route_from_test",
]
