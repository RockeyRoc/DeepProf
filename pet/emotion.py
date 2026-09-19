"""教学动作 → 情绪/表情标签映射（DESIGNv0.4 §16.4 / §7.2 / §6.2）。

这是**前端表现层的可解释映射**：桌宠"为什么做这个表情"必须能追溯到教学动作，
评审与实验才能解释（§12 教育可验证 / §13.1 不把模型判断包装成权威结论）。

数据来源：Pedagogical Graph 的节点（§6.2）与 Runtime 事件流（§5.4），
例如事件 ``pedagogy.node.entered`` / ``pedagogy.decision`` 的 payload 里带教学动作。
本轮只定义契约与映射表；表情资源（Live2D/PNG）在下一轮由张钧翔接入。

映射原则（可被教师评审）：
- 提问时表现为"一起想"，而不是"考你"；
- 给提示、纠错时是鼓励与关切，不做羞辱性表达；
- 未识别的动作不猜测，回落到中性表情。
"""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

__all__ = [
    "EmotionDirective",
    "EmotionLabel",
    "TeachingAction",
    "emotion_for",
    "mapping_table",
]


class TeachingAction(str, Enum):
    """教学动作（取自 §6.2 教学决策节点）。"""

    ASSESS = "Assess"
    TEACH = "Teach"
    ASK = "Ask"
    HINT = "Hint"
    CORRECT = "Correct"
    TEST = "Test"
    UPDATE_PROFILE = "UpdateProfile"
    REFLECT = "Reflect"


class EmotionLabel(str, Enum):
    """情绪标签（表现层语义，不含任何学情判断）。"""

    NEUTRAL = "neutral"
    THINKING = "thinking"
    EXPLAINING = "explaining"
    ENCOURAGING = "encouraging"
    CONCERNED = "concerned"
    STRICT = "strict"
    HAPPY = "happy"


@dataclass(frozen=True)
class EmotionDirective:
    """前端消费的表现指令。

    Attributes:
        action: 触发该表现的教学动作
        emotion: 情绪标签
        expression: 表情资源键名（PNG 先用它，Live2D 表情名下一轮映射）
        reason: 人可读的解释，用于评审与调试（§12）
    """

    action: TeachingAction
    emotion: EmotionLabel
    expression: str
    reason: str

    def to_dict(self) -> dict:
        """转成可序列化形式（前端 IPC / 事件 payload 用）。"""
        return {
            "action": self.action.value,
            "emotion": self.emotion.value,
            "expression": self.expression,
            "reason": self.reason,
        }


#: 教学动作 → 表现指令（一一对应，便于教师逐条评审）
_MAPPING: dict[TeachingAction, EmotionDirective] = {
    TeachingAction.ASSESS: EmotionDirective(
        TeachingAction.ASSESS, EmotionLabel.THINKING, "thinking", "在判断学生当前状态，先不急着讲"
    ),
    TeachingAction.TEACH: EmotionDirective(
        TeachingAction.TEACH, EmotionLabel.EXPLAINING, "explaining", "分层讲解，给出可定位来源"
    ),
    TeachingAction.ASK: EmotionDirective(
        TeachingAction.ASK, EmotionLabel.THINKING, "thinking", "苏格拉底式追问，和学生一起推理"
    ),
    TeachingAction.HINT: EmotionDirective(
        TeachingAction.HINT, EmotionLabel.ENCOURAGING, "encouraging", "从轻到重给提示，不直接给答案"
    ),
    TeachingAction.CORRECT: EmotionDirective(
        TeachingAction.CORRECT, EmotionLabel.CONCERNED, "concerned", "指出冲突并解释原因，不责备"
    ),
    TeachingAction.TEST: EmotionDirective(
        TeachingAction.TEST, EmotionLabel.STRICT, "strict", "验证理解，认真但不施压"
    ),
    TeachingAction.UPDATE_PROFILE: EmotionDirective(
        TeachingAction.UPDATE_PROFILE, EmotionLabel.NEUTRAL, "neutral", "记录学情增量，不打扰学生"
    ),
    TeachingAction.REFLECT: EmotionDirective(
        TeachingAction.REFLECT, EmotionLabel.THINKING, "thinking", "策略受阻，需要换策略或求助教师"
    ),
}

#: 未识别动作的回落表现（不猜测，避免误导）
_FALLBACK = EmotionDirective(
    TeachingAction.ASSESS, EmotionLabel.NEUTRAL, "neutral", "未知教学动作，回落中性表情"
)


def emotion_for(action: str | TeachingAction) -> EmotionDirective:
    """把教学动作映射为表现指令；未知动作回落中性（不猜测）。"""
    try:
        key = action if isinstance(action, TeachingAction) else TeachingAction(str(action))
    except ValueError:
        return _FALLBACK
    return _MAPPING.get(key, _FALLBACK)


def mapping_table() -> list[dict]:
    """完整映射表（供前端对照实现与教师评审，§12 / §16.7）。"""
    return [directive.to_dict() for directive in _MAPPING.values()]