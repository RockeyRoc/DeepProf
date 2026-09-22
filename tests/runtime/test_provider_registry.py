"""Provider Registry：逻辑角色路由、显式 fallback、能力与健康状态。"""

from __future__ import annotations

import pytest

from runtime.core.errors import KIND_UPSTREAM_ERROR, ProviderError
from runtime.providers.fake import FakeProvider
from runtime.providers.profiles import ProviderProfile
from runtime.providers.registry import ProviderRegistry
from runtime.providers.secrets import InMemorySecretStore


def make_registry(*profile_ids: str) -> ProviderRegistry:
    registry = ProviderRegistry(InMemorySecretStore(), settings=None)
    for profile_id in profile_ids:
        registry.add(
            ProviderProfile(
                profile_id=profile_id,
                base_url="https://example.invalid/v1",
                default_model=f"{profile_id}-model",
                capabilities={"stream": True},
            ),
            FakeProvider(profile_id, script=[{"content": f"from {profile_id}"}]),
        )
    return registry


def test_unknown_profile_raises_value_error():
    registry = make_registry("a")
    with pytest.raises(ValueError, match="unknown provider profile"):
        registry.get("nope")


def test_role_resolution_uses_logical_role():
    registry = make_registry("a", "b")
    registry.set_role("tutor.default", "b", "b-model")
    provider, profile_id, model = registry.resolve("tutor.default")
    assert (profile_id, model) == ("b", "b-model")
    assert provider.profile_id == "b"


def test_unknown_role_falls_back_to_default_role():
    registry = make_registry("a")
    registry.set_role("tutor.default", "a", "a-model")
    _, profile_id, _ = registry.resolve("quiz.generator")
    assert profile_id == "a"


def test_disabled_profile_is_rejected():
    registry = make_registry("a")
    registry.set_role("tutor.default", "a", "m")
    registry.profile("a").enabled = False
    with pytest.raises(ValueError, match="disabled"):
        registry.resolve("tutor.default")


async def test_no_silent_fallback_without_explicit_chain():
    """未显式配置 fallback chain 时，失败必须原样抛出，不静默换模型。"""
    registry = make_registry("a", "b")
    registry.get("a")._script = [ProviderError("boom", kind=KIND_UPSTREAM_ERROR)]  # noqa: SLF001
    registry.set_role("tutor.default", "a", "a-model")

    with pytest.raises(ProviderError):
        await registry.generate("tutor.default", {"messages": []}, {})


async def test_explicit_fallback_records_degraded_from():
    registry = make_registry("a", "b")
    registry.get("a")._script = [ProviderError("boom", kind=KIND_UPSTREAM_ERROR)]  # noqa: SLF001
    registry.set_role("tutor.default", "a", "a-model")
    registry.set_fallback("a", ["b"])

    result = await registry.generate("tutor.default", {"messages": []}, {})
    assert result["content"] == "from b"
    assert result["provider_profile"] == "b"
    assert result["metadata"]["degraded_from"] == "a"
    assert result["metadata"]["fallback_index"] == 1


async def test_fallback_chain_reports_all_failed():
    registry = make_registry("a", "b")
    registry.get("a")._script = [ProviderError("a down", kind=KIND_UPSTREAM_ERROR)]  # noqa: SLF001
    registry.get("b")._script = [ProviderError("b down", kind=KIND_UPSTREAM_ERROR)]  # noqa: SLF001
    registry.set_role("tutor.default", "a", "a-model")
    registry.set_fallback("a", ["b"])

    with pytest.raises(ProviderError) as excinfo:
        await registry.generate("tutor.default", {"messages": []}, {})
    assert excinfo.value.details["profile_id"] == "a"
    assert excinfo.value.details["last_error"]["details"]["kind"] == KIND_UPSTREAM_ERROR


def test_fallback_rejects_unknown_profile():
    registry = make_registry("a")
    with pytest.raises(ValueError):
        registry.set_fallback("a", ["ghost"])


async def test_probe_updates_capabilities_and_health():
    registry = make_registry("a")
    result = await registry.probe("a")
    assert result["status"] == "ok"
    assert registry.health("a")["status"] == "ok"


def test_status_is_secret_free():
    registry = make_registry("a")
    registry.profile("a").api_key_ref = "provider:a"
    registry.set_secret("provider:a", "sk-super-secret")

    status = registry.status()
    dumped = repr(status)
    assert "sk-super-secret" not in dumped
    assert "api_key" not in dumped
    assert status[0]["base_url_host"] == "example.invalid"


def test_has_secret_never_reveals_value():
    registry = make_registry("a")
    registry.profile("a").api_key_ref = "provider:a"
    assert registry.has_secret("a") is False
    registry.set_secret("provider:a", "sk-1")
    assert registry.has_secret("a") is True


def test_remove_clears_role_binding():
    registry = make_registry("a")
    registry.set_role("tutor.default", "a", "m")
    registry.remove("a")
    assert "tutor.default" not in registry.roles