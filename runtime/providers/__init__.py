"""模型与语音 Provider 适配层。

新增供应商时：继承 Provider 实现 generate/stream/capabilities，
并在 factory.get_provider() 注册分支，业务代码无需改动。
"""
from .base import ModelChunk, ModelRequest, ModelResponse, Provider, ProviderCapabilities
from .factory import get_provider
from .fake import FakeProvider

__all__ = [
    "Provider",
    "ProviderCapabilities",
    "ModelRequest",
    "ModelResponse",
    "ModelChunk",
    "FakeProvider",
    "OpenAICompatProvider",
    "get_provider",
]


def __getattr__(name: str):
    """延迟导入真实供应商实现，避免无密钥/无 SDK 场景导入失败。"""
    if name == "OpenAICompatProvider":
        from .openai_compat import OpenAICompatProvider

        return OpenAICompatProvider
    raise AttributeError(name)