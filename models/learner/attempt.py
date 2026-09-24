"""Attempt（一次作答尝试）—— 教育组 → 数据组的交接契约（DESIGNv0.6 §18.2 / §16.6）。

字段严格取自 §18.2 的 Attempt 行：
    attempt_id, learner_id, course_id, item_id, scored_concept_id, question_bank_version,
    grading_source, confidence, correct, timestamp, hint_count

责任与边界：
- 生产者：教育组（孙一新）在 Test / Correct 节点产出作答事实；
- 消费者：BKT 与后续知识追踪；
    - 幂等：attempt_id 唯一，同一题库版本首次无提示、可靠判分的作答才更新 BKT；
- 本模块定义作答事实契约；可靠判分缺失时 ``correct`` 为 ``None``，禁止将人工待判答案伪装为错误。

trace 关联：session_id / trace_id 将作答事实关联到会话和本轮事件（§18.2）。
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, field_validator

__all__ = ["Attempt"]


class Attempt(BaseModel):
    """一条可复现、可幂等写入的作答记录。

    extra="forbid"：交接字段以 §18.2 为准，拒绝隐式扩字段，
    让字段漂移在联调阶段就暴露，而不是悄悄写进数据组仓库。
    """

    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(description="作答事件唯一标识（幂等键，§18.2）")
    learner_id: str = Field(description="学习者标识；不同学习者的数据不得互相覆盖")
    session_id: str = Field(description="产生作答的 Session")
    trace_id: str = Field(description="作答命令对应的 trace")
    course_id: str = Field(default="", description="课程版本标识")
    item_id: str = Field(description="题目标识（与教材来源、知识点由数据组映射）")
    scored_concept_id: str = Field(default="", description="本次判分唯一归属的知识点")
    concept_ids: list[str] = Field(
        default_factory=list,
        description="本题覆盖的知识点；BKT 按知识点逐条累积证据",
    )
    correct: bool | None = Field(description="可靠判分为正确/错误；人工待判时为 None")
    question_bank_version: str = Field(default="", description="固定题库版本")
    grading_source: str = Field(default="", description="判分方法标识")
    confidence: float = Field(default=0.0, ge=0.0, le=1.0, description="判分可信度")
    timestamp: str = Field(description="作答时间，ISO8601（建议 UTC，保持时间顺序）")
    hint_count: int = Field(
        default=0, ge=0, description="本题已给出的提示次数（提示越多，证据越弱）"
    )

    # ---------- 校验 ----------
    @field_validator("attempt_id", "learner_id", "session_id", "trace_id", "item_id")
    @classmethod
    def _require_identifier(cls, value: str, info) -> str:
        """标识类字段不能为空串：空标识会让幂等与画像隔离失效。"""
        if not value or not value.strip():
            raise ValueError(f"{info.field_name} 不能为空")
        return value

    @field_validator("scored_concept_id", "course_id", "question_bank_version", "grading_source")
    @classmethod
    def _trim_optional_identifiers(cls, value: str) -> str:
        return str(value or "").strip()

    @field_validator("timestamp")
    @classmethod
    def _require_iso8601(cls, value: str) -> str:
        """时间必须是可解析的 ISO8601：知识追踪按时间排序，时间不可信则结果不可信。"""
        try:
            datetime.fromisoformat(value)
        except ValueError as exc:  # 明确报错，不做静默兜底
            raise ValueError("timestamp 必须是 ISO8601 字符串，例如 2026-09-17T08:00:00+00:00") from exc
        return value

    @field_validator("concept_ids")
    @classmethod
    def _normalize_concepts(cls, value: list[str]) -> list[str]:
        """去空、去重且保持原顺序，避免同一知识点被重复计数。"""
        seen: list[str] = []
        for item in value or []:
            text = str(item).strip()
            if text and text not in seen:
                seen.append(text)
        return seen
