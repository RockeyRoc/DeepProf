"""DeepProf Runtime。

分层（DESIGNv0.4 §4.1）：
    core        自研极简内核：Agent / Session / Event / Message / Port
    providers   模型与语音 Provider 适配
    tools       Tool 注册、校验、审批与执行
    memory      记忆接口、策略与实现
    storage     Session / Event / 资源 / 画像持久化
    sandbox     文件、进程、网络隔离策略
    plugins     Plugin Runtime（manifest / registry / lifecycle）
    skills      面向图节点的可复用能力注册表

依赖规则（§9.1）：runtime 不得反向依赖 graph、skills、pet 或具体数据库 SDK。
"""
from .core.errors import (
    AgentLoopLimit,
    PermissionDenied,
    PluginError,
    ProviderError,
    RuntimeError_,
    SessionNotFound,
    SkillNotFound,
    ToolApprovalRequired,
    ToolNotFound,
    ToolValidationError,
)
from .core.events import EventBus, EventType, RuntimeEvent, StreamSubscription, redact
from .core.message import Message, Role, ToolCall, new_id, utc_now
from .core.session import Session

__all__ = [
    # 消息与事件
    "Message",
    "Role",
    "ToolCall",
    "RuntimeEvent",
    "EventType",
    "EventBus",
    "StreamSubscription",
    "redact",
    "new_id",
    "utc_now",
    # 会话
    "Session",
    # 异常
    "RuntimeError_",
    "SessionNotFound",
    "ProviderError",
    "ToolNotFound",
    "ToolValidationError",
    "ToolApprovalRequired",
    "PermissionDenied",
    "PluginError",
    "SkillNotFound",
    "AgentLoopLimit",
]