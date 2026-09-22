"""Provider Registry 与逻辑模型路由。

- 教学代码只依赖**逻辑角色**（tutor.default / quiz.generator / ...），不写厂商名；
- 未知 Provider 名称抛 ``ValueError``；
- 失败**禁止静默换模型**，只有显式配置的 fallback chain 才允许切换，并记录 ``degraded_from``。
"""

from __future__ import annotations

from typing import Any, Callable, Protocol

from config.settings import Settings
from runtime.core.errors import ProviderError
from runtime.providers.base import Provider, probe_provider
from runtime.providers.capabilities import normalize_capabilities
from runtime.providers.local import LocalProvider
from runtime.providers.openai_compatible import OpenAICompatibleProvider
from runtime.providers.profiles import (
    PROTOCOL_LOCAL,
    PROTOCOL_OPENAI_COMPATIBLE,
    ProviderProfile,
    RoleMap,
)
from runtime.providers.secrets import SecretStore

DEFAULT_ROLE = "tutor.default"

ProviderFactory = Callable[[ProviderProfile, SecretStore], Provider]


class ModelRouter(Protocol):
    """把逻辑角色解析为具体的 (provider, profile_id, model)。"""

    def resolve(
        self, role: str, requested_model: str | None = None
    ) -> tuple[Provider, str, str]: ...


def default_provider_factory(
    profile: ProviderProfile,
    secrets: SecretStore,
    *,
    transport: Any = None,
    settings: Settings | None = None,
) -> Provider:
    kwargs: dict[str, Any] = {}
    if transport is not None:
        kwargs["transport"] = transport
    if settings is not None:
        kwargs["default_max_tokens"] = settings.llm_max_tokens
        kwargs["timeout_seconds"] = settings.llm_timeout_seconds
    if profile.protocol == PROTOCOL_LOCAL:
        return LocalProvider(profile, secrets, **kwargs)
    if profile.protocol == PROTOCOL_OPENAI_COMPATIBLE:
        return OpenAICompatibleProvider(profile, secrets, **kwargs)
    raise ValueError(f"unsupported provider protocol: {profile.protocol!r}")


class ProviderRegistry:
    """管理 Profile、能力、健康状态与 Secret 引用。"""

    def __init__(
        self,
        secret_store: SecretStore,
        *,
        settings: Settings | None = None,
        transport: Any = None,
        provider_factory: ProviderFactory | None = None,
    ) -> None:
        self._secrets = secret_store
        self._settings = settings or Settings()
        self._transport = transport
        self._factory = provider_factory or (
            lambda profile, secrets: default_provider_factory(
                profile, secrets, transport=self._transport, settings=self._settings
            )
        )
        self._profiles: dict[str, ProviderProfile] = {}
        self._providers: dict[str, Provider] = {}
        self._roles: RoleMap = {}
        self._fallbacks: dict[str, list[str]] = {}
        self._health: dict[str, dict[str, Any]] = {}

    # ---- 注册 ----

    def add(self, profile: ProviderProfile, provider: Provider | None = None) -> ProviderProfile:
        self._profiles[profile.profile_id] = profile
        self._providers[profile.profile_id] = provider or self._factory(profile, self._secrets)
        return profile

    def remove(self, profile_id: str) -> None:
        self._profiles.pop(profile_id, None)
        self._providers.pop(profile_id, None)
        self._health.pop(profile_id, None)
        self._fallbacks.pop(profile_id, None)
        self._roles = {role: binding for role, binding in self._roles.items() if binding[0] != profile_id}

    def get(self, profile_id: str) -> Provider:
        if profile_id not in self._providers:
            raise ValueError(
                f"unknown provider profile: {profile_id!r}; known={sorted(self._providers)}"
            )
        return self._providers[profile_id]

    def profile(self, profile_id: str) -> ProviderProfile:
        if profile_id not in self._profiles:
            raise ValueError(f"unknown provider profile: {profile_id!r}")
        return self._profiles[profile_id]

    def profiles(self) -> list[ProviderProfile]:
        return list(self._profiles.values())

    # ---- 凭据（只暴露“有没有”，不回显明文） ----

    @property
    def secret_store(self) -> SecretStore:
        return self._secrets

    def set_secret(self, ref: str, value: str) -> None:
        self._secrets.set(ref, value)

    def has_secret(self, profile_id: str) -> bool:
        profile = self._profiles.get(profile_id)
        if profile is None or not profile.api_key_ref:
            return False
        return bool(self._secrets.get(profile.api_key_ref))

    # ---- 角色与回退 ----

    def set_role(self, role: str, profile_id: str, model: str = "") -> None:
        self.profile(profile_id)  # 未注册则报错
        self._roles[role] = (profile_id, model)

    def set_fallback(self, profile_id: str, chain: list[str]) -> None:
        for item in chain:
            self.profile(item)
        self._fallbacks[profile_id] = list(chain)

    @property
    def roles(self) -> RoleMap:
        return dict(self._roles)

    def _primary(self, role: str) -> tuple[str, str]:
        binding = self._roles.get(role) or self._roles.get(DEFAULT_ROLE)
        if binding is not None:
            return binding
        enabled = [p for p in self._profiles.values() if p.enabled]
        if not enabled:
            raise ValueError("no provider profile registered")
        first = enabled[0]
        return first.profile_id, first.default_model

    def resolve(
        self, role: str, requested_model: str | None = None
    ) -> tuple[Provider, str, str]:
        profile_id, model = self._primary(role)
        provider = self.get(profile_id)
        profile = self._profiles[profile_id]
        if not profile.enabled:
            raise ValueError(f"provider profile disabled: {profile_id!r}")
        return provider, profile_id, (requested_model or model or profile.default_model)

    def resolve_chain(
        self, role: str, requested_model: str | None = None
    ) -> list[tuple[Provider, str, str]]:
        """返回显式 fallback chain；未配置时只有主 Provider。"""
        primary_id, _ = self._primary(role)
        chain = [primary_id] + self._fallbacks.get(primary_id, [])
        candidates: list[tuple[Provider, str, str]] = []
        seen: set[str] = set()
        for profile_id in chain:
            if profile_id in seen:
                continue
            seen.add(profile_id)
            profile = self._profiles.get(profile_id)
            if profile is None or not profile.enabled:
                continue
            provider = self.get(profile_id)
            model = requested_model or (
                profile.default_model if profile_id != primary_id else self._roles.get(role, ("", ""))[1]
            )
            candidates.append((provider, profile_id, model or profile.default_model))
        return candidates

    async def generate(
        self, role: str, request: dict[str, Any], ctx: dict[str, Any]
    ) -> dict[str, Any]:
        """按显式 fallback chain 依次尝试；仅在失败时切换，并记录 degraded_from。"""
        candidates = self.resolve_chain(role, requested_model=request.get("model"))
        if not candidates:
            raise ValueError("no provider candidate available")
        primary_id = candidates[0][1]
        last_error: ProviderError | None = None
        for index, (provider, profile_id, model) in enumerate(candidates):
            payload = dict(request)
            payload["model"] = model
            try:
                result = await provider.generate(payload, ctx)
            except ProviderError as exc:
                last_error = exc
                continue
            result.setdefault("metadata", {})
            if index > 0:
                result["metadata"]["degraded_from"] = primary_id
                result["metadata"]["fallback_index"] = index
            result["provider_profile"] = profile_id
            return result
        assert last_error is not None
        raise ProviderError(
            f"所有 provider 候选均失败（primary={primary_id}）",
            kind=last_error.kind,
            details={"profile_id": primary_id, "degraded_from": None, "last_error": last_error.to_dict()},
        )

    # ---- 能力与健康 ----

    def capabilities_for(self, profile_id: str) -> dict[str, bool]:
        return normalize_capabilities(self.profile(profile_id).capabilities)

    async def probe(self, profile_id: str, model: str | None = None) -> dict[str, Any]:
        provider = self.get(profile_id)
        result = await probe_provider(
            provider,
            model=model or self._profiles[profile_id].default_model,
            max_tokens=self._settings.probe_max_tokens,
        )
        self._health[profile_id] = result
        if result.get("capabilities") and result["status"] == "ok":
            # 探测成功的能力合并进 Profile 声明（保守：只升不降）
            merged = normalize_capabilities(self._profiles[profile_id].capabilities)
            merged.update({k: v for k, v in result["capabilities"].items() if v})
            self._profiles[profile_id].capabilities = merged
        return result

    def health(self, profile_id: str) -> dict[str, Any]:
        return dict(self._health.get(profile_id, {"status": "unknown"}))

    def status(self) -> list[dict[str, Any]]:
        """非敏感装配状态，供 /health 暴露。"""
        result = []
        for profile in self._profiles.values():
            health = self._health.get(profile.profile_id, {"status": "unknown"})
            result.append(
                {
                    "profile_id": profile.profile_id,
                    "display_name": profile.display_name,
                    "base_url_host": _host(profile.base_url),
                    "default_model": profile.default_model,
                    "enabled": profile.enabled,
                    "protocol": profile.protocol,
                    "capabilities": dict(profile.capabilities),
                    "health": health.get("status", "unknown"),
                    "last_probe": health.get("kind", ""),
                }
            )
        return result


def _host(url: str) -> str:
    if not url:
        return ""
    without_scheme = url.split("://", 1)[-1]
    return without_scheme.split("/", 1)[0]