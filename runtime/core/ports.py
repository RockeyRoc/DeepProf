"""Runtime Port：教学图与 Runtime 之间的稳定契约（DESIGNv0.4 §7.1）。

边界（§4.4）分两个面，方向单向：

    Pedagogical Graph ──RuntimePort（窄面）──▶ Runtime
                                              ├─ ActionDispatcher 查绑定表
    ActionDispatcher ──RuntimeHost（宽面）────▶ Runtime 能力实现
                                              ├─ Skill / Tool / Provider / Memory

    RuntimePort  图唯一可依赖的面：**只出决策，只写轨迹**
    RuntimeHost  能力实现才能拿到的面：Skill / Tool / Provider / 记忆读写

为什么要分：端口上原本平铺着 generate / call_tool / read_memory，图节点随手
就能调，于是"图只声明 WHAT"这条边界只靠自觉。分面之后，图依赖的类型里根本
没有这些方法——`tests/test_architecture_boundaries.py` 还有一条 AST 守卫，
任何节点只要碰一下宽面就会红。

两类方法为什么留在图侧：
- `execute`  图表达 WHAT 的唯一出口；
- `emit`     事件轨迹是**图自己的可观测行为**（§3.4 可解释性分析的数据来源），
             不是"调用某个教学能力"，因此不包装成决策。

端口参数与返回值一律使用可序列化的 dict，
使教学图与 Runtime 的类型演化互不牵连。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Protocol, runtime_checkable


@dataclass
class RuntimeContext:
    """一次调用携带的关联信息（session / learner / trace）。"""

    session_id: str = ""
    learner_id: str = ""
    trace_id: str = ""
    source: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "session_id": self.session_id,
            "learner_id": self.learner_id,
            "trace_id": self.trace_id,
            "source": self.source,
            "metadata": self.metadata,
        }

    @classmethod
    def from_dict(cls, data: dict | None) -> "RuntimeContext":
        data = data or {}
        return cls(
            session_id=str(data.get("session_id") or ""),
            learner_id=str(data.get("learner_id") or ""),
            trace_id=str(data.get("trace_id") or ""),
            source=str(data.get("source") or ""),
            metadata=dict(data.get("metadata") or {}),
        )


@runtime_checkable
class RuntimePort(Protocol):
    """教学图唯一可依赖的 Runtime 能力面（窄面）。

    只有两个方法：表达决策、写轨迹。图不需要、也不应该知道
    "这个决策最终会调哪个 Skill、哪个 Tool、哪个模型"。
    """

    async def execute(self, request: dict, ctx: dict) -> dict:
        """执行一条**声明式决策请求**（WHAT → HOW，§4.4）。

        request 是 `PedagogicalDecision.to_dict()` 的形态：动作、概念、级别、
        require_evidence 等字段 + params。Runtime 不认识任何教学动作名——
        它只做通用分发：查调用方注入的绑定表 → 按名解析 capability → 执行 →
        返回 `CapabilityResult` 的 dict 形态（字段见 graph/education/contracts.py）。

        没注入绑定、绑定指向未注册能力、绑定引用了请求里不存在的字段、
        按取值选模板却选不到时，**必须显式失败**
        （status=no_binding / capability_not_found / invalid_request / error），
        不允许静默兜底成一段自由发挥的文本。
        """

    async def emit(self, event: dict) -> None:
        """写入事件轨迹（教学决策与节点进出）。"""


@runtime_checkable
class RuntimeHost(RuntimePort, Protocol):
    """Runtime 能力实现可用的完整面（宽面）。

    这一层就是"Runtime Execution"：真正落地的 Skill / Tool / Provider / 记忆读写。
    它只应被 Runtime 内部的 ActionDispatcher 与 Capability 实现使用——
    图节点一旦拿到它，就等于把 HOW 拉回了策略层。

    宽面继承窄面，因此 RuntimeService 只需实现一套方法即可同时满足两个面。
    """

    async def invoke_skill(self, name: str, input: dict, ctx: dict) -> dict:
        """调用一个 Skill（Socratic / Quiz / RAG / Diagnosis / PaperReader）。"""

    async def call_tool(self, name: str, arguments: dict, ctx: dict) -> dict:
        """调用一个原子 Tool，返回结构化结果（含失败时的 error）。"""

    def generate(self, request: dict, ctx: dict) -> AsyncIterator[dict]:
        """流式生成；实现为异步生成器，逐条产出增量字典。"""

    async def read_memory(self, query: dict, ctx: dict) -> list[dict]:
        """读取学情记忆，返回可序列化记录列表。"""

    async def write_memory(self, records: list[dict], ctx: dict) -> None:
        """写入学情记忆（必须带来源、置信度、过期策略与撤回标记）。"""


__all__ = ["RuntimeContext", "RuntimeHost", "RuntimePort"]