"""Pedagogical Graph 状态定义（DESIGNv0.6 §6.4）。

两条硬约束：
1. **只放 JSON 友好类型**：状态要能进 LangGraph checkpoint、能随事件回放（§18.2）；
2. **不放大文本**：大型文档、模型原始输出、完整对话历史都不进状态，
   只保存 Storage 引用（learner_state_ref）与检索定位（retrieved_evidence_refs），
   避免状态膨胀与隐私复制（§6.4、§13.2）。

字段来源：
- §6.4 基线字段：
  session_id / learning_goal / current_concept / learner_state_ref /
  retrieved_evidence_refs / attempt_count / hint_level / misconceptions /
  last_assessment / next_action；
- 实现补充字段：
  learner_id / trace_id / user_input            关联信息与本轮输入
  action / response_text / emotion / citations  交前端的三件套（§16.3 交接）
  evidence_sufficient / wrong_streak / last_answer_correct  教学判断依据
  prior_knowledge_gap / item_id                  先验判定与作答题目（Attempt 依据，§18.2）
  student_stopped / turn_count / max_turns      退出与防无限追问（§7.3）
  strategy_note                                 本轮策略备注（复盘用）
"""

from __future__ import annotations

from typing import Any, TypedDict

from .policies import MAX_TURNS_DEFAULT


class PedagogyState(TypedDict, total=False):
    """教学策略图状态。total=False：节点只返回自己更新的字段（增量更新）。"""

    # ---------- §6.4 基线字段 ----------
    session_id: str
    learning_goal: str
    current_concept: str
    current_concept_id: str
    experiment_group: str
    learner_estimate: dict[str, Any]
    retrieved_evidence_refs: list[dict]  # 教材证据定位，不含原文
    attempt_count: int  # 学生作答尝试累计
    hint_level: int  # 已给出的提示级别（0=未给提示）
    misconceptions: list[str]  # 疑似错误概念（需展示不确定性）
    last_assessment: dict  # 最近一次 Assess 的结构化快照
    next_action: str  # Assess 决策出的下一步教学动作

    # ---------- 关联与输入 ----------
    learner_id: str
    trace_id: str
    user_input: str  # 本轮学生输入（短文本，随会话落盘，不放完整历史）
    course_id: str
    provider_profile: str
    model: str
    freeze_model: bool
    generation_config: dict[str, Any]
    requested_action: str

    # ---------- 本轮输出（§16.3：向前端输出文本、教学动作和情感标签） ----------
    action: str
    response_text: str
    emotion: str
    citations: list[dict]

    # ---------- 判断依据 ----------
    evidence_sufficient: bool
    evidence_constraint: bool
    wrong_streak: int  # 连续答错次数
    last_answer_correct: bool | None  # None = 缺少可靠判分，不得当成答错
    prior_knowledge_gap: bool  # 学生自述缺少先验（前置概念未学）→ 讲解起点前移
    item_id: str  # 本轮作答对应的题目（Attempt 的幂等与来源映射依据；题库未接入时为空）
    quiz_question: dict[str, Any]
    test_difficulty: int | None

    # ---------- 循环控制（§7.3 防无限追问） ----------
    student_stopped: bool
    turn_count: int
    max_turns: int

    # ---------- 复盘备注 ----------
    strategy_note: str


#: 完整默认状态：保证每个节点读到的都是确定类型，而不是到处写 .get(..., 0)。
DEFAULT_STATE: dict[str, Any] = {
    "session_id": "",
    "learning_goal": "",
    "current_concept": "",
    "current_concept_id": "",
    "experiment_group": "B",
    "learner_estimate": {},
    "retrieved_evidence_refs": [],
    "attempt_count": 0,
    "hint_level": 0,
    "misconceptions": [],
    "last_assessment": {},
    "next_action": "",
    "learner_id": "",
    "trace_id": "",
    "user_input": "",
    "course_id": "",
    "provider_profile": "",
    "model": "",
    "freeze_model": True,
    "generation_config": {},
    "requested_action": "",
    "action": "",
    "response_text": "",
    "emotion": "",
    "citations": [],
    "evidence_sufficient": False,
    "evidence_constraint": True,
    "wrong_streak": 0,
    "last_answer_correct": None,
    "prior_knowledge_gap": False,
    "item_id": "",
    "quiz_question": {},
    "test_difficulty": None,
    "student_stopped": False,
    "turn_count": 0,
    "max_turns": MAX_TURNS_DEFAULT,
    "strategy_note": "",
}


def new_state(**overrides: Any) -> PedagogyState:
    """构造一份完整状态：先铺默认值，再覆盖调用方给出的字段。

    只接受状态 schema 内的键，未知键会被忽略——这样 LangGraph 不会因为
    意外字段报 InvalidUpdateError，也强迫新增字段必须先在 state.py 里声明。
    """
    state = dict(DEFAULT_STATE)
    for key, value in overrides.items():
        if key in DEFAULT_STATE:
            state[key] = value
    return state  # type: ignore[return-value]
