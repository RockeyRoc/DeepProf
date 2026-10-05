"""Provider Registry 与逻辑模型路由。

- 教学代码只依赖**逻辑角色**（tutor.default / quiz.generator / ...），不写厂商名；
- 未知 Provider 名称抛 ``ValueError``；
- 失败**禁止静默换模型**，只有显式配置的 fallback chain 才允许切换，并记录 ``degraded_from``。
"""

from __future__ import annotations

import asyncio
import time
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
        self._retired_providers: list[Provider] = []
        self._roles: RoleMap = {}
        self._fallbacks: dict[str, list[str]] = {}
        self._health: dict[str, dict[str, Any]] = {}
        self._model_option_cache: dict[tuple[str, str], tuple[float, dict[str, Any]]] = {}
        self._model_option_locks: dict[tuple[str, str], asyncio.Lock] = {}
        self._model_catalog_cache: dict[str, tuple[float, list[str]]] = {}
        self._model_catalog_locks: dict[str, asyncio.Lock] = {}
        self.metrics: dict[str, int] = {"catalog_requests": 0, "catalog_cache_hits": 0,
                                        "model_option_requests": 0, "model_option_cache_hits": 0}
        self._model_option_semaphore = asyncio.Semaphore(4)

    # ---- 注册 ----

    def add(self, profile: ProviderProfile, provider: Provider | None = None) -> ProviderProfile:
        self.invalidate_model_options(profile.profile_id)
        previous = self._providers.get(profile.profile_id)
        current = provider or self._factory(profile, self._secrets)
        if previous is not None and previous is not current:
            self._retired_providers.append(previous)
        self._profiles[profile.profile_id] = profile
        self._providers[profile.profile_id] = current
        return profile

    def remove(self, profile_id: str) -> None:
        self.invalidate_model_options(profile_id)
        self._profiles.pop(profile_id, None)
        self._providers.pop(profile_id, None)
        self._health.pop(profile_id, None)
        self._fallbacks.pop(profile_id, None)
        self._roles = {role: binding for role, binding in self._roles.items() if binding[0] != profile_id}

    def invalidate_model_options(self, profile_id: str | None = None) -> None:
        if profile_id is None:
            self._model_option_cache.clear()
            self._model_option_locks.clear()
            self._model_catalog_cache.clear()
            self._model_catalog_locks.clear()
            return
        for key in [key for key in self._model_option_cache if key[0] == profile_id]:
            self._model_option_cache.pop(key, None)
        for key in [key for key in self._model_option_locks if key[0] == profile_id]:
            self._model_option_locks.pop(key, None)
        self._model_catalog_cache.pop(profile_id, None)
        self._model_catalog_locks.pop(profile_id, None)

    async def list_model_catalog(self, profile_id: str, *, refresh: bool = False,
                                 ttl_seconds: float = 600.0) -> dict[str, Any]:
        """Share one provider model-directory request and retain stale data on failure."""
        self.metrics["catalog_requests"] += 1
        cached = self._model_catalog_cache.get(profile_id)
        if cached and not refresh and time.monotonic() - cached[0] < ttl_seconds:
            self.metrics["catalog_cache_hits"] += 1
            return {"models": list(cached[1]), "status": "cached"}
        lock = self._model_catalog_locks.setdefault(profile_id, asyncio.Lock())
        async with lock:
            cached = self._model_catalog_cache.get(profile_id)
            if cached and not refresh and time.monotonic() - cached[0] < ttl_seconds:
                self.metrics["catalog_cache_hits"] += 1
                return {"models": list(cached[1]), "status": "cached"}
            provider = self.get(profile_id)
            try:
                async with self._model_option_semaphore:
                    models = await provider.list_models()
                result = list(dict.fromkeys(str(item).strip() for item in models if str(item).strip()))
                if not result:
                    raise ProviderError("模型服务未返回模型目录", kind="model_catalog_empty")
                self._model_catalog_cache[profile_id] = (time.monotonic(), result)
                return {"models": list(result), "status": "verified"}
            except Exception as exc:
                if cached:
                    return {"models": list(cached[1]), "status": "stale",
                            "error": str(exc)[:240]}
                raise

    async def describe_model_options(self, profile_id: str, model: str, *, refresh: bool = False,
                                     ttl_seconds: float = 600.0) -> dict[str, Any]:
        self.metrics["model_option_requests"] += 1
        key = (profile_id, model)
        cached = self._model_option_cache.get(key)
        if cached and not refresh and time.monotonic() - cached[0] < ttl_seconds:
            self.metrics["model_option_cache_hits"] += 1
            return dict(cached[1])
        lock = self._model_option_locks.setdefault(key, asyncio.Lock())
        async with lock:
            cached = self._model_option_cache.get(key)
            if cached and not refresh and time.monotonic() - cached[0] < ttl_seconds:
                self.metrics["model_option_cache_hits"] += 1
                return dict(cached[1])
            adapter = self.get(profile_id)
            describe = getattr(adapter, "describe_model", None)
            if not callable(describe):
                return {}
            try:
                async with self._model_option_semaphore:
                    options = await describe(model)
                if isinstance(options, dict) and options:
                    result = dict(options)
                    result["verification_status"] = "verified"
                    self._model_option_cache[key] = (time.monotonic(), result)
                    return dict(result)
                return {}
            except Exception as exc:
                if cached:
                    return {**cached[1], "verification_status": "stale",
                            "verification_error": str(exc)[:240]}
                raise

    async def remove_and_close(self, profile_id: str) -> None:
        provider = self._providers.get(profile_id)
        self.remove(profile_id)
        close = getattr(provider, "aclose", None)
        if callable(close):
            await close()

    async def aclose(self) -> None:
        providers = list({id(provider): provider for provider in
                          [*self._providers.values(), *self._retired_providers]}.values())
        for provider in providers:
            close = getattr(provider, "aclose", None)
            if callable(close):
                await close()
        self._retired_providers.clear()

    def unset_role(self, role: str) -> None:
        self._roles.pop(role, None)

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

    def resolve_chain_for_profile(
        self, profile_id: str, requested_model: str | None = None
    ) -> list[tuple[Provider, str, str]]:
        """Resolve an explicitly selected profile and only its configured fallbacks."""
        primary = self.profile(profile_id)
        if not primary.enabled:
            raise ValueError(f"provider profile disabled: {profile_id!r}")
        chain = [profile_id] + self._fallbacks.get(profile_id, [])
        candidates: list[tuple[Provider, str, str]] = []
        seen: set[str] = set()
        for index, candidate_id in enumerate(chain):
            if candidate_id in seen:
                continue
            seen.add(candidate_id)
            candidate = self._profiles.get(candidate_id)
            if candidate is None or not candidate.enabled:
                continue
            model = requested_model if index == 0 else candidate.default_model
            candidates.append((self.get(candidate_id), candidate_id, model or candidate.default_model))
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
            # Non-streaming probe cannot turn an undeclared capability into false.
            # Merge only positive observations while preserving explicit values.
            merged = dict(self._profiles[profile_id].capabilities)
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
