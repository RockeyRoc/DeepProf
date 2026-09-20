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


#: 中文档位 → 契约里的英文键（shared/contracts/events.json 的 affinity.updated.payload.stage）。
#:
#: ⚠️ 2026-09-20 由张钧翔补。这里和前端 `desktop/src/renderer/src/petStats.js` 的
#:    `STAGES` 是**同一套分档，只是叫法不同**：
#:
#:      | 阈值 | 本文件（中文） | petStats.js（英文键 + 中文标签） |
#:      | ---- | -------------- | -------------------------------- |
#:      |  <20 | 生疏           | stranger · 刚认识                |
#:      |  <50 | 熟悉           | familiar · 熟悉                  |
#:      |  <80 | 信任           | close    · 亲近   ← 这里叫"信任"  |
#:      | >=80 | 亲密           | attached · 依赖   ← 这里叫"亲密"  |
#:
#:    阈值**完全一致**（0/20/50/80），所以只是命名问题，不是算法分歧。
#:    契约以**英文键**为准（前端按它查表），中文标签只用于界面展示 ——
#:    发事件时用 stage，别把 level 发出去。
_STAGE_BY_LEVEL = {
    "生疏": "stranger",
    "熟悉": "familiar",
    "信任": "close",
    "亲密": "attached",
}


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
        """展示档位（中文，表现层用语）。只用于界面显示，**不要发进事件**。"""
        return _level_of(self.value)

    @property
    def stage(self) -> str:
        """契约 `affinity.updated` 里的 stage（英文键）。发事件用这个。"""
        return _STAGE_BY_LEVEL[self.level]

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

    def to_event_payload(self) -> dict:
        """契约 `affinity.updated` 的 payload 形状（shared/contracts/events.json）。

        ⚠️ 2026-09-20 由张钧翔补。**为什么不能直接用 to_dict() 发事件**：
            两边字段名根本对不上 ——

              to_dict()（本文件原有的）        affinity.updated（契约要求的）
              ─────────────────────────       ──────────────────────────────
              value   int                     affinity  float
              level   中文档位                 stage     英文键
              unbound bool                    （无）

            而契约那个形状是照**前端** `petStats.js` 写的，前端只认
            `affinity` / `stage` 这套名字 —— 拿 to_dict() 发出去，前端一个字段都读不到。
            所以发事件必须用本方法；to_dict() 保留给界面展示与旧调用方，别删。

        另外两点仍在分歧中，**没有单方面改**，记录在此等对齐：
          1. **初值**：这里取 settings.affinity_init（默认 30），而 petStats.js 从 4 开始。
             两边一开始就不同档；谁为准要定（见 INCONSISTENCIES.md）。
          2. **谁算**：本骨架完全没有事件源（source 恒为 "unbound"），而前端
             petStats.js 已经在本地按互动/时间自己算了。契约说 producer 是 runtime ——
             到底是"前端算了报上去"还是"后端算了推下来"，这条链路还没有结论。
        """
        return {
            "learner_id": self.learner_id,
            "affinity": float(self.value),
            "stage": self.stage,
            "source": self.source,
            "updated_at": self.updated_at,
        }


def _clamp(value: int) -> int:
    return max(MIN_AFFINITY, min(MAX_AFFINITY, value))


def _now() -> str:
    """ISO8601 UTC 时间字符串（与 Runtime 事件时间格式一致，便于前端对齐）。"""
    return datetime.now(timezone.utc).isoformat()