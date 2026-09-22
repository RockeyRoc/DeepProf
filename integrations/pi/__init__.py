"""真实 Pi RPC/SDK 适配器，仅开发者模式使用。

产品 CLI 不得依赖 Pi Agent Core；Pi 与本项目 Learner Session 严格隔离（D-9、§19.7）。
"""

from integrations.pi.event_adapter import adapt_event, is_developer_only_event
from integrations.pi.rpc_client import PiRpcClient, PiRpcError, decode_frame, encode_frame, spawn_pi

__all__ = [
    "PiRpcClient",
    "PiRpcError",
    "adapt_event",
    "decode_frame",
    "encode_frame",
    "is_developer_only_event",
    "spawn_pi",
]