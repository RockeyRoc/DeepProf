"""Plugin Runtime：注册、生命周期与权限治理。

思想参考 DeepSeek Harness 的"Everything is a Plugin"，
但只取受控子集（D-4：MVP 仅受信 Python 插件）。
"""
from .lifecycle import (
    STATUS_FAILED,
    STATUS_INSTALLED,
    STATUS_STARTED,
    STATUS_STOPPED,
    PluginBase,
    PluginContext,
    PluginManager,
    PluginState,
)
from .manifest import CAPABILITIES, MANIFEST_FILENAME, PluginManifest, discover
from .registry import ServiceRegistry
from .trust import PluginTrustStore, TrustDecision, TrustRecord, plugin_digest

__all__ = [
    "PluginManifest",
    "discover",
    "CAPABILITIES",
    "MANIFEST_FILENAME",
    "ServiceRegistry",
    "PluginBase",
    "PluginContext",
    "PluginManager",
    "PluginState",
    "PluginTrustStore",
    "TrustDecision",
    "TrustRecord",
    "plugin_digest",
    "STATUS_INSTALLED",
    "STATUS_STARTED",
    "STATUS_STOPPED",
    "STATUS_FAILED",
]