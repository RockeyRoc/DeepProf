"""教学契约：决策（WHAT）与结果（HOW 的产出）（DESIGNv0.6 §4.4 / §6.2 / §7.1）。

两张契约把“应该做什么”与“怎么做”分开：

    Pedagogical Graph（策略）  ──PedagogicalDecision──▶  DeepProf Runtime（执行）
    Pedagogical Graph（策略）  ◀──CapabilityResult────  DeepProf Runtime（执行）

图只产出**声明式决策**（动作 + 参数 + 依据），不生成任何正文；
Runtime 按注入的绑定表把它翻译成通用能力调用（见 graph/education/bindings.py），
回传结构化结果。策略层因此退化成纯数据，可反事实回放（§3.4 研究问题 1）。

为什么契约放在 graph 侧而不是 runtime/core/ports.py：
- `runtime/` 不得反向依赖上层包（tests/test_architecture_boundaries.py 有 AST 守卫），
  契约类不能写进 runtime；
- 端口只传可序列化 dict（`RuntimePort.execute(request: dict, ctx: dict) -> dict`），
  本模块负责对象与 dict 之间的往返，成为图侧唯一的“词汇表”。

字段归属（有意划分，避免同一件事有两个主人）：
    action                  Graph：教学动作，决定绑定到哪个 capability
    concept / level         Graph：策略计算的产物（如 policies.next_hint_level）
    evidence_sufficient     Graph：本轮评估结论，仅用于决定表述（如“不含引用”说明）；
                            它**不能**替代分发器对证据的实际校验
    reveal_answer           capability 参数：是否允许泄露最终答案
    require_evidence        分发器通用前置条件：取不到证据则不执行主能力、不调用模型
    require_student_reply   Graph：循环控制，Runtime 不参与
    params                  执行所需补充输入（学习目标、检索意图、作答次数…）。
                            **不得整体写进事件**：学生正文只能出现在这里，
                            pedagogy.* 决策事件必须显式挑字段（§6.4 / §13.2）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ======================================================================
# 一、能力结果的状态取值（Runtime 侧用同样的字符串，见 runtime/capabilities.py）
# ======================================================================

#: 成功（内容可用）
STATUS_SUCCESS = "success"
#: 证据不足：分发器在调用主能力前拦下，不调用模型、不编造来源
STATUS_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
#: 该动作没有注入绑定（调用方忘了装配，必须显式失败而不是静默兜底）
STATUS_NO_BINDING = "no_binding"
#: 绑定引用了未注册的能力
STATUS_CAPABILITY_NOT_FOUND = "capability_not_found"
#: 请求本身不完整（例如绑定引用了请求里不存在的字段）
STATUS_INVALID_REQUEST = "invalid_request"
#: 能力执行失败（模型失败、Skill 不可用且无兜底…）
STATUS_ERROR = "error"


@dataclass
class PedagogicalDecision:
    """教学图产出的一条声明式决策：只说“做什么”，不说“怎么做”。

    action 取值沿用 policies.ACTION_*（全小写，如 "hint" / "ask" / "teach"），
    避免在已有常量之上再做一层大小写转换。
    """

    action: str
    concept: str = ""
    level: int = 0
    reveal_answer: bool = False
    require_evidence: bool = False
    require_student_reply: bool = True
    evidence_sufficient: bool = False
    params: dict[str, Any] = field(default_factory=dict)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action,
            "concept": self.concept,
            "level": int(self.level),
            "reveal_answer": bool(self.reveal_answer),
            "require_evidence": bool(self.require_evidence),
            "require_student_reply": bool(self.require_student_reply),
            "evidence_sufficient": bool(self.evidence_sufficient),
            "params": dict(self.params),
            "reason": self.reason,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "PedagogicalDecision":
        """宽松解析：未知键被忽略，缺省字段落回默认值（便于跨版本回放）。"""
        data = data or {}
        return cls(
            action=str(data.get("action") or ""),
            concept=str(data.get("concept") or ""),
            level=int(data.get("level") or 0),
            reveal_answer=bool(data.get("reveal_answer", False)),
            require_evidence=bool(data.get("require_evidence", False)),
            require_student_reply=bool(data.get("require_student_reply", True)),
            evidence_sufficient=bool(data.get("evidence_sufficient", False)),
            params=dict(data.get("params") or {}),
            reason=str(data.get("reason") or ""),
        )


@dataclass
class CapabilityResult:
    """Runtime 回传的结构化结果：图拿它更新状态与交前端，不解析字符串。

    四类载荷各司其职，不要互相代用：

        content   面向学生的正文（可能为空——如只取状态的调用）
        evidence  教材证据的**可定位引用**（不含原文，§6.4）
        records   结构化记录（学情记忆等），供图侧解释而不进状态正文
        metadata  执行元信息（能力名、降级来源、透传字段…）
    """

    content: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    status: str = STATUS_SUCCESS
    action: str = ""
    capability: str = ""
    error: dict[str, Any] | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.status == STATUS_SUCCESS

    def to_dict(self) -> dict[str, Any]:
        return {
            "content": self.content,
            "evidence": [dict(item) for item in self.evidence],
            "records": [dict(item) for item in self.records],
            "status": self.status,
            "action": self.action,
            "capability": self.capability,
            "error": dict(self.error) if isinstance(self.error, dict) else None,
            "metadata": dict(self.metadata),
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any] | None) -> "CapabilityResult":
        data = data or {}
        error = data.get("error")
        return cls(
            content=str(data.get("content") or ""),
            evidence=[dict(item) for item in (data.get("evidence") or []) if isinstance(item, dict)],
            records=[dict(item) for item in (data.get("records") or []) if isinstance(item, dict)],
            status=str(data.get("status") or STATUS_SUCCESS),
            action=str(data.get("action") or ""),
            capability=str(data.get("capability") or ""),
            error=dict(error) if isinstance(error, dict) else None,
            metadata=dict(data.get("metadata") or {}),
        )


__all__ = [
    "STATUS_CAPABILITY_NOT_FOUND",
    "STATUS_ERROR",
    "STATUS_INSUFFICIENT_EVIDENCE",
    "STATUS_INVALID_REQUEST",
    "STATUS_NO_BINDING",
    "STATUS_SUCCESS",
    "CapabilityResult",
    "PedagogicalDecision",
]