"""Provider 能力声明与兼容等级（§19.6）。

L0 Basic（非流式文本）/ L1 Stream / L2 Agent（tools、结构化输出）
/ 扩展：vision、reasoning、Responses API。兼容等级不做未经验证的承诺。
"""

from __future__ import annotations

from typing import Any

CAP_STREAM = "stream"
CAP_TOOLS = "tools"
CAP_JSON = "json"
CAP_VISION = "vision"
CAP_REASONING = "reasoning"
CAP_RESPONSES = "responses"
CAP_EMBEDDINGS = "embeddings"

ALL_CAPABILITIES = (
    CAP_STREAM,
    CAP_TOOLS,
    CAP_JSON,
    CAP_VISION,
    CAP_REASONING,
    CAP_RESPONSES,
    CAP_EMBEDDINGS,
)

# 教育主链路所需的最小能力集合
REQUIRED_CAPABILITIES = (CAP_STREAM,)


def default_capabilities() -> dict[str, bool]:
    """未探测前的保守声明：只承诺 L0。"""
    return {name: False for name in ALL_CAPABILITIES}


def normalize_capabilities(raw: dict[str, Any] | None) -> dict[str, bool]:
    result = default_capabilities()
    for key, value in (raw or {}).items():
        result[str(key)] = bool(value)
    return result


def missing_capabilities(have: dict[str, bool], need: tuple[str, ...]) -> list[str]:
    return [name for name in need if not have.get(name, False)]