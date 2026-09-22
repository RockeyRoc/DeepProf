"""学情领域 DTO 与 LearnerModel 接口（DESIGNv0.6 §18.1 / §18.2，责任人：欧阳文凯）。

两类输出语义不同，切勿混用：

- Attempt：教学过程中产生的作答事实，供 BKT / IRT 消费；
- LearnerEstimate：模型估计结果，带 model_type / model_version / uncertainty，
  BKT 的概率与 IRT 的能力分不可直接相加（§18.1）。

`LearnerModel` 是统一接口：BKT 掌握度、IRT 标定与后续知识追踪都实现它，
以便按同一口径比较（§18.1）。**本轮只交付接口与契约，不实现估计算法**——
算法属数据组下一轮工作，冷启动阶段教学图使用经教师审核的保守策略。
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable

from .attempt import Attempt
from .estimate import PROBABILITY_MODEL_TYPES, LearnerEstimate, ModelType


@runtime_checkable
class LearnerModel(Protocol):
    """学情模型统一接口（§18.1）。

    实现方（BKT / IRT / 后续知识追踪）必须做到：
    - 按时间顺序消费 Attempt，不引入未来信息；
    - 输出带 model_type / model_version 与不确定性的 LearnerEstimate；
    - 证据不足时返回 ``estimate=None``（信息不足），而不是给一个“大概的”数字。
    """

    model_type: ModelType
    model_version: str

    def update(self, attempts: list[Attempt]) -> None:
        """按时间顺序吸收作答事实。"""

    def estimate(self, learner_id: str, concept_id: str = "") -> LearnerEstimate | None:
        """给出当前估计；信息不足时返回 None（调用方退化为保守策略）。"""

    def to_dict(self) -> dict[str, Any]:
        """可复现所需的模型标识（类型 + 版本）。"""


__all__ = [
    "Attempt",
    "LearnerEstimate",
    "LearnerModel",
    "ModelType",
    "PROBABILITY_MODEL_TYPES",
]