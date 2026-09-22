"""Provider Hub：多套 OpenAI-compatible Profile + 本地 Adapter。"""

from runtime.providers.base import Provider, probe_provider
from runtime.providers.capabilities import (
    ALL_CAPABILITIES,
    REQUIRED_CAPABILITIES,
    missing_capabilities,
    normalize_capabilities,
)
from runtime.providers.factory import build_registry, load_profiles, save_profiles
from runtime.providers.fake import FakeProvider
from runtime.providers.local import LocalProvider
from runtime.providers.openai_compatible import OpenAICompatibleProvider
from runtime.providers.profiles import ProviderProfile
from runtime.providers.registry import ModelRouter, ProviderRegistry, default_provider_factory
from runtime.providers.secrets import (
    ChainedSecretStore,
    EnvSecretStore,
    InMemorySecretStore,
    SecretStore,
)

__all__ = [
    "ALL_CAPABILITIES",
    "ChainedSecretStore",
    "EnvSecretStore",
    "FakeProvider",
    "InMemorySecretStore",
    "LocalProvider",
    "ModelRouter",
    "OpenAICompatibleProvider",
    "Provider",
    "ProviderProfile",
    "ProviderRegistry",
    "REQUIRED_CAPABILITIES",
    "SecretStore",
    "build_registry",
    "default_provider_factory",
    "load_profiles",
    "missing_capabilities",
    "normalize_capabilities",
    "probe_provider",
    "save_profiles",
]