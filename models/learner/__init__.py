"""学情领域 DTO（DESIGNv0.4 §18.1 / §18.2 / §16.6，责任人：欧阳文凯）。

两类输出语义不同，切勿混用：
- Attempt：教学过程中产生的作答事实，供 BKT / IRT 消费；
- LearnerEstimate：模型估计结果，带 model_type / model_version / uncertainty，
  BKT 的概率与 IRT 的能力分不可直接相加（§18.1）。
"""
from .attempt import Attempt
from .estimate import PROBABILITY_MODEL_TYPES, LearnerEstimate, ModelType

__all__ = ["Attempt", "LearnerEstimate", "ModelType", "PROBABILITY_MODEL_TYPES"]