"""Diagnosis Skill：从答题/提问/错误轨迹推断知识状态（DESIGNv0.4 §6.3 / §18.1）。

**诚实边界（重要）**：学情模型**尚未接入**。
按 D-6（§15）与 §18.1，BKT 掌握度、IRT 题目标定与后续知识追踪由欧阳文凯负责，
必须以统一 `LearnerModel` 接口输出
{learner_id, concept_id, model_type, model_version, estimate, evidence_count, uncertainty}。

因此本 Skill 现在**返回 not_implemented，不产出任何掌握度数字**：
- 没有模型版本与不确定性的"掌握度"在教学伦理上不可接受（§13.1）；
- 冷启动阶段图应使用经教师审核的保守策略（§18.1），
  而不是让模型凭感觉给出学情结论。

接入方式（交接清单）：
1. 数据组提供 LearnerModel 实现与模型版本号；
2. 本 Skill 改为调用 `port.call_tool("learner_estimate", ...)`；
3. 返回值填入 estimate / uncertainty / evidence_count，
   由 UpdateProfile 写入带 model_version 的长期记忆。

责任人：欧阳文凯（§16.6 学情模型与评测）。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimeContext, RuntimePort
from runtime.skills import Skill, SkillDescriptor

#: 缺少的能力与补齐责任（写给交接与评审看）
_MISSING = (
    "缺少 BKT/IRT/知识追踪模型（欧阳文凯负责，§16.6）与 LearnerModel 统一接口（§18.1）："
    "当前无法给出掌握度估计及其模型版本与不确定性，因此不输出任何学情数字"
)


class DiagnosisSkill(Skill):
    """学情诊断 Skill（占位：模型未接入，返回 not_implemented）。"""

    descriptor = SkillDescriptor(
        name="diagnosis",
        description="从答题、提问与错误轨迹推断知识状态（学情模型未接入，当前返回 not_implemented）",
        when_to_use="UpdateProfile / Assess 需要结构化掌握度估计时",
        version="0.1.0",
        owner="欧阳文凯",
        tags=["learner_model", "bkt", "irt", "not_implemented"],
    )

    async def handle(
        self, payload: dict, ctx: RuntimeContext, port: RuntimePort
    ) -> dict[str, Any]:
        """不产出估计值；只回显收到的输入字段，便于交接时对齐契约。"""
        return {
            "status": "not_implemented",
            "skill": self.name,
            "note": _MISSING,
            "learner_id": str(payload.get("learner_id") or ""),
            "concept": str(payload.get("concept") or ""),
            "inputs_received": sorted(str(key) for key in payload),
            "expected_output_schema": [
                "learner_id",
                "concept_id",
                "model_type",
                "model_version",
                "estimate",
                "evidence_count",
                "uncertainty",
            ],
            "estimate": None,
            "uncertainty": None,
        }


__all__ = ["DiagnosisSkill"]