"""好感度状态骨架（DESIGNv0.4 §16.4 / §5.5 Affective Memory / §13.1）。

状态与边界：
- 好感度是**表现层状态**，只影响桌宠表达方式；
  **不参与学情诊断**，不得用于给学生贴标签或影响评分（§13.1）；
- 初值取 ``settings.affinity_init``（默认 30，范围 [0,100]）；
- 目前**未接任何真实事件源**：既没有从 Runtime 事件累计，
  也没有写入 Affective Memory（需用户授权，§5.5 / §13.2 "可查看、可纠正、可删除、可关闭"）。
  因此 ``source`` 固定为 ``"unbound"``，任何数值变化都只可能是显式调用造成的。

下一轮（§16.4）由张钧翔接入：ASR/TTS 与表情联动之后，
再从"经用户授权的事件"（如作答正确、连续学习天数、学生显式反馈）驱动好感度，
并保证可撤回、可清零。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

from config import settings

__all__ = ["AffinityState"]

#: 好感度取值区间
MIN_AFFINITY = 0
MAX_AFFINITY = 100

#: 来源标记：尚未绑定真实事件源
SOURCE_UNBOUND = "unbound"


def _level_of(value: int) -> str:
    """把数值分档为可展示的档位（纯表现层用语，不做任何能力评价）。"""
    if value < 20:
        return "生疏"
    if value < 50:
        return "熟悉"
    if value < 80:
        return "信任"
    return "亲密"


@dataclass
class AffinityState:
    """桌宠好感度状态骨架。

    Attributes:
        learner_id: 归属学习者（按 learner_id 隔离，§17.3）
        value: 好感度数值，恒定落在 [MIN_AFFINITY, MAX_AFFINITY]
        source: 数据来源标记；``"unbound"`` 表示未接真实事件源
        updated_at: 最近更新时间（ISO8601）；未变更时为空
    """

    learner_id: str = ""
    value: int = field(default_factory=lambda: int(settings.affinity_init))
    source: str = SOURCE_UNBOUND
    updated_at: str = ""

    def __post_init__(self) -> None:
        self.value = _clamp(int(self.value))

    @property
    def level(self) -> str:
        """展示档位（表现层用语）。"""
        return _level_of(self.value)

    def apply_delta(self, delta: int, *, source: str = SOURCE_UNBOUND) -> int:
        """按增量更新好感度并夹取到合法区间，返回新值。

        骨架阶段仅供前端走通交互；``source`` 必须由调用方说明来源，
        未授权来源不得调用（§13.2）。
        """
        self.value = _clamp(self.value + int(delta))
        self.source = source
        self.updated_at = _now()
        return self.value

    def reset(self) -> int:
        """清零回到初值（用户可删除/关闭长期记忆，§13.2）。"""
        self.value = int(settings.affinity_init)
        self.source = SOURCE_UNBOUND
        self.updated_at = _now()
        return self.value

    def to_dict(self) -> dict:
        """前端消费的形状。"""
        return {
            "learner_id": self.learner_id,
            "value": self.value,
            "level": self.level,
            "source": self.source,
            "updated_at": self.updated_at,
            "unbound": self.source == SOURCE_UNBOUND,
        }


def _clamp(value: int) -> int:
    return max(MIN_AFFINITY, min(MAX_AFFINITY, value))


def _now() -> str:
    """ISO8601 UTC 时间字符串（与 Runtime 事件时间格式一致，便于前端对齐）。"""
    return datetime.now(timezone.utc).isoformat()