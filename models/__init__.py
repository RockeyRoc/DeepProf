"""领域模型与 DTO（DESIGNv0.4 §9 目录树 / §18.2 交接字段）。

这里放的是**跨组交接契约**，不是算法实现：
- 教育组 → 数据组：Attempt（作答事实）
- 数据组 → 教学图：LearnerEstimate（学情估计）

依赖方向（§9.1）：models 只描述可序列化的数据形状，
不导入 runtime / graph / api，也不绑定任何数据库 SDK。
"""
from .learner import Attempt, LearnerEstimate, ModelType

__all__ = ["Attempt", "LearnerEstimate", "ModelType"]