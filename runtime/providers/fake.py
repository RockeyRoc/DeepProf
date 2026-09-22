"""确定性 Fake Provider：供契约测试与离线/Mock 模式使用。"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from runtime.core.errors import ProviderError
from runtime.providers.base import error_frame, finish_frame, tool_call_frame, usage_frame
from runtime.providers.capabilities import normalize_capabilities
from runtime.providers.profiles import PROTOCOL_NATIVE, ProviderProfile

ScriptStep = dict[str, Any] | Exception | Callable[[dict[str, Any]], dict[str, Any]]


class FakeProvider:
    """按脚本产出流式帧；默认脚本为一句固定回复。"""

    protocol = PROTOCOL_NATIVE

    def __init__(
        self,
        profile_id: str = "fake",
        *,
        script: list[ScriptStep] | None = None,
        capabilities: dict[str, bool] | None = None,
        chunk_size: int = 3,
    ) -> None:
        self.profile_id = profile_id
        self.profile = ProviderProfile(
            profile_id=profile_id,
            display_name="Fake Provider",
            protocol=PROTOCOL_NATIVE,
            default_model="fake-model",
        )
        self._script: list[ScriptStep] = list(script or [{"content": "fake reply"}])
        self._index = 0
        self._capabilities = normalize_capabilities(capabilities or {"stream": True})
        self._chunk_size = max(1, chunk_size)

    def _next_step(self, request: dict[str, Any]) -> dict[str, Any]:
        if not self._script:
            return {"content": ""}
        step = self._script[min(self._index, len(self._script) - 1)]
        self._index += 1
        if callable(step):
            return step(request)
        return step

    async def stream(
        self, request: dict[str, Any], ctx: dict[str, Any]
    ) -> AsyncIterator[dict[str, Any]]:
        step = self._next_step(request)
        if isinstance(step, Exception):
            error = step if isinstance(step, ProviderError) else ProviderError(str(step))
            yield error_frame(error.to_dict())
            return
        content = str(step.get("content", ""))
        for start in range(0, len(content), self._chunk_size):
            yield {"type": "delta", "text": content[start : start + self._chunk_size]}
        for call in step.get("tool_calls") or []:
            yield tool_call_frame(call)
        if step.get("usage"):
            yield usage_frame(step["usage"])
        yield finish_frame(str(step.get("finish_reason", "stop")))

    async def generate(self, request: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        step = self._next_step(request)
        if isinstance(step, Exception):
            raise step if isinstance(step, ProviderError) else ProviderError(str(step))
        return {
            "content": str(step.get("content", "")),
            "tool_calls": list(step.get("tool_calls") or []),
            "finish_reason": str(step.get("finish_reason", "stop")),
            "usage": dict(step.get("usage") or {}),
            "model": request.get("model") or self.profile.default_model,
            "capabilities": self.capabilities(),
        }

    async def list_models(self) -> list[str]:
        return [self.profile.default_model]

    def capabilities(self) -> dict[str, bool]:
        return dict(self._capabilities)

    async def healthcheck(self, model: str | None = None) -> dict[str, Any]:
        return {"status": "ok", "kind": "ok", "message": "", "capabilities": self.capabilities()}