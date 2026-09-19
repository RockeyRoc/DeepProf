"""配置可加载性测试。

目的：验证配置能从 .env / 默认值加载，不依赖真实密钥，CI 可直接跑通。
"""
from config import settings


def test_settings_loads_defaults():
    """默认配置应可加载，provider 默认 deepseek。"""
    assert settings.llm_provider == "deepseek"
    assert settings.llm_temperature == 0.7
    assert 0 <= settings.affinity_init <= 100


def test_provider_keys_exist():
    """三个 provider 的配置字段都应存在（值可为空）。"""
    for attr in ("deepseek_api_key", "openai_api_key", "qwen_api_key"):
        assert hasattr(settings, attr)
