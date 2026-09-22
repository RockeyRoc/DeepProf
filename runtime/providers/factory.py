"""Provider 装配：Profile 读写与 Registry 构建。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from config import paths
from config.settings import Settings
from runtime.providers.fake import FakeProvider
from runtime.providers.profiles import ProviderProfile
from runtime.providers.registry import ProviderRegistry
from runtime.providers.secrets import ChainedSecretStore, EnvSecretStore, SecretStore

MOCK_PROFILE_ID = "mock"


def load_profiles(path: Path | None = None) -> list[ProviderProfile]:
    target = path or paths.providers_file()
    if not target.exists():
        return []
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return []
    items = raw.get("profiles", raw) if isinstance(raw, dict) else raw
    if not isinstance(items, list):
        return []
    return [ProviderProfile.from_dict(item) for item in items if isinstance(item, dict)]


def save_profiles(profiles: list[ProviderProfile], path: Path | None = None) -> Path:
    target = path or paths.providers_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"contract_version": "1.4.0", "profiles": [p.to_dict() for p in profiles]}
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def load_role_map(path: Path | None = None) -> dict[str, tuple[str, str]]:
    """Load persisted logical-role bindings without ever reading secrets."""
    target = path or paths.config_file()
    if not target.exists():
        return {}
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    roles = raw.get("provider_roles", {}) if isinstance(raw, dict) else {}
    if not isinstance(roles, dict):
        return {}
    result: dict[str, tuple[str, str]] = {}
    for role, binding in roles.items():
        if not isinstance(binding, dict) or not binding.get("profile_id"):
            continue
        result[str(role)] = (str(binding["profile_id"]), str(binding.get("model") or ""))
    return result


def save_role_binding(
    role: str, profile_id: str, model: str = "", path: Path | None = None
) -> Path:
    """Persist a logical role selection in the public, secret-free config."""
    target = path or paths.config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        raw = json.loads(target.read_text(encoding="utf-8")) if target.exists() else {}
    except (json.JSONDecodeError, OSError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    roles = raw.setdefault("provider_roles", {})
    if not isinstance(roles, dict):
        roles = {}
        raw["provider_roles"] = roles
    roles[role] = {"profile_id": profile_id, "model": model}
    target.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
    return target


def build_registry(
    profiles: list[ProviderProfile],
    *,
    secret_store: SecretStore | None = None,
    settings: Settings | None = None,
    transport: Any = None,
    role_map: dict[str, tuple[str, str]] | None = None,
    with_mock: bool = True,
) -> ProviderRegistry:
    """从 Profile 列表装配 Registry；无可用 Profile 时回落到 Mock。"""
    secrets = secret_store or ChainedSecretStore(EnvSecretStore())
    registry = ProviderRegistry(secrets, settings=settings, transport=transport)

    usable = [p for p in profiles if p.enabled and p.base_url]
    for profile in usable:
        registry.add(profile)

    if role_map:
        for role, (profile_id, model) in role_map.items():
            if profile_id in {p.profile_id for p in usable}:
                registry.set_role(role, profile_id, model)

    if not usable and with_mock:
        mock = ProviderProfile(
            profile_id=MOCK_PROFILE_ID,
            display_name="Mock（未接入真实模型）",
            protocol="native",
            default_model="mock-model",
            capabilities={"stream": True},
        )
        registry.add(mock, FakeProvider(MOCK_PROFILE_ID))
        registry.set_role("tutor.default", MOCK_PROFILE_ID, "mock-model")
    return registry
