"""LearnerEstimate（学情估计）—— 数据组 → 教学图的交接契约（DESIGNv0.4 §18.2 / §18.1）。

字段严格取自 §18.2 的 LearnerEstimate 行：
    learner_id, concept_id, model_type, model_version, estimate, evidence_count, uncertainty

为什么要显式区分 model_type（§18.1）：
- BKT 输出**知识点掌握概率**（[0,1] 的概率语义）；
- IRT 输出**学生能力分与题目难度**（能力量表语义，不是概率）；
- 两类输出**不可直接相加或当作同一尺度比较**，因此 model_type 必须显式标注，
  消费方（教学图）必须按 model_type 决定如何解释 estimate。
- 冷启动 / 信息不足：evidence_count 为 0 时，uncertainty 缺省（None）
  明确表示"信息不足"，教学图应转而采用经教师审核的保守策略（§18.1 / §7.3）。

边界：本模块只定义契约与校验，**不实现 BKT / IRT / 知识追踪算法**
（那是数据组下一轮工作，§16.6）；也不提供跨模型合并函数——
跨模型融合需要新的一致性假设，不能在契约层偷偷替数据组决定。
"""
from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, ConfigDict, Field, model_validator

__all__ = ["LearnerEstimate", "ModelType", "PROBABILITY_MODEL_TYPES"]


class ModelType(str, Enum):
    """估计值来自哪一类学情模型（§18.1）。

    新增模型必须在此显式登记：宁可让人补一行枚举，
    也不要让"未知 model_type"默默流进教学图。
    """

    BKT = "bkt"  # 知识点掌握概率
    IRT = "irt"  # 学生能力 / 题目难度（能力量表）
    KNOWLEDGE_TRACING = "knowledge_tracing"  # 后续知识追踪模型（如 DKT 系列）
    OTHER = "other"  # 其它经验证模型；语义必须在 model_version 中说明


#: 输出语义为"概率"（取值必须在 [0,1]）的模型类型
PROBABILITY_MODEL_TYPES = frozenset(
    {ModelType.BKT, ModelType.KNOWLEDGE_TRACING}
)


class LearnerEstimate(BaseModel):
    """一条带模型版本与不确定性的学情估计。

    extra="forbid"：字段以 §18.2 为准；需要附加信息时先改契约再落库，
    避免数据组仓库里出现无人认领的字段。
    """

    model_config = ConfigDict(extra="forbid")

    learner_id: str = Field(description="学习者标识；跨会话不得被覆盖（§18.2）")
    concept_id: str = Field(
        default="",
        description="知识点标识；IRT 的学习者级能力分约定用空串表示非知识点估计",
    )
    model_type: ModelType = Field(description="模型类型，决定 estimate 的语义（§18.1）")
    model_version: str = Field(
        description="模型与参数版本（含训练数据范围），保证估计可复现"
    )
    estimate: float = Field(
        description="估计值：概率类模型为掌握概率，能力类模型为能力分（尺度由 model_type 决定）"
    )
    evidence_count: int = Field(
        default=0, ge=0, description="支撑本次估计的证据条数（如作答次数）"
    )
    uncertainty: float | None = Field(
        default=None,
        ge=0.0,
        description="不确定性（如标准差 / 熵）；None 表示信息不足，不可当 0 使用",
    )

    # ---------- 校验 ----------
    @model_validator(mode="after")
    def _check_semantics(self) -> "LearnerEstimate":
        """按 model_type 校验取值语义，避免概率与能力分被混用。"""
        if not self.learner_id.strip():
            raise ValueError("learner_id 不能为空")
        if not self.model_version.strip():
            raise ValueError("model_version 不能为空：没有版本的估计无法复现")
        if self.model_type in PROBABILITY_MODEL_TYPES and not 0.0 <= self.estimate <= 1.0:
            raise ValueError(
                f"{self.model_type.value} 输出的是概率语义，estimate 必须落在 [0,1]，"
                f"当前为 {self.estimate}"
            )
        return self

    @property
    def is_probability(self) -> bool:
        """estimate 是否为概率语义；消费方据此判断能否与其它概率比较。"""
        return self.model_type in PROBABILITY_MODEL_TYPES

    @property
    def insufficient(self) -> bool:
        """是否处于信息不足状态（冷启动）：uncertainty 缺省即视为信息不足。"""
        return self.uncertainty is None or self.evidence_count == 0