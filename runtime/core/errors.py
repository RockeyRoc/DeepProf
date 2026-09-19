"""Runtime 统一异常。

原则（DESIGNv0.4 §7.3）：失败必须结构化，调用方（教学图）据此决定
重试、换工具或解释限制，而不是靠解析字符串。
"""
from __future__ import annotations


class RuntimeError_(Exception):
    """Runtime 异常基类。

    子类通过 code 提供稳定的机器可读标识，message 面向人类阅读。
    """

    code = "runtime_error"

    def __init__(self, message: str, **details: object) -> None:
        super().__init__(message)
        self.message = message
        self.details = details

    def to_dict(self) -> dict:
        """转为结构化错误，便于写入事件与返回给教学图。"""
        return {"code": self.code, "message": self.message, "details": self.details}


class SessionNotFound(RuntimeError_):
    code = "session_not_found"


class ProviderError(RuntimeError_):
    code = "provider_failed"


class ToolNotFound(RuntimeError_):
    code = "tool_not_found"


class ToolValidationError(RuntimeError_):
    code = "tool_invalid_arguments"


class ToolApprovalRequired(RuntimeError_):
    """高风险工具未获审批（§13.3）。调用方需显式审批后再执行。"""

    code = "tool_approval_required"


class PermissionDenied(RuntimeError_):
    """Sandbox 或权限校验拒绝（D-5：默认拒绝越界）。"""

    code = "permission_denied"


class PluginError(RuntimeError_):
    code = "plugin_failed"


class SkillNotFound(RuntimeError_):
    code = "skill_not_found"


class MemoryValidationError(RuntimeError_):
    """长期记忆写入缺少来源、置信度或撤回标记（§5.5）。"""

    code = "memory_invalid_record"


class AgentLoopLimit(RuntimeError_):
    """模型—工具循环超过上限，避免无限调用。"""

    code = "agent_loop_limit"


class ModelTruncated(RuntimeError_):
    """模型输出被截断且没有正文（finish_reason=length）。

    典型成因：推理模型的 token 预算被 reasoning 过程耗尽，
    此时流式只会返回空正文；若当作成功收尾，学生看到的是一条空回复，
    轨迹里也看不出失败（§16.1 失败与取消可观测）。
    """

    code = "model_truncated"