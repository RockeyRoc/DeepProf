"""配置一致性守护测试。

背景：``.env.example`` 是给使用者复制的模板，``config/settings.py`` 的默认值是
"没写 .env 时的兜底"。两者描述同一套配置，一旦漂移就会出现"文档说 A、默认值是 B"
的隐性坑。本仓库已真实发生过两次：

1. ``.env.example`` 写 ``DEEPSEEK_MODEL=deepseek-flash``，settings 默认却是
   ``deepseek-chat``——后者是未文档化的遗留别名（端点 ``/models`` 只列
   ``deepseek-flash``、``deepseek-v4-pro``），随时可能被下线。
2. ``LLM_MAX_TOKENS`` 模板 4096、默认 1024，而 1024 在推理模型下会先被
   reasoning token 耗尽，导致正文为空（``finish_reason=length``）。

本测试的策略：把"已知且有意为之"的差异显式登记在 ``KNOWN_DIVERGENCES``，
其余任何差异都判失败；同时**已修复却仍挂在例外表里**的过期条目也判失败，
避免例外表长期失真后失去守护作用。
"""
from __future__ import annotations

import re
from pathlib import Path

from config.settings import Settings

ENV_EXAMPLE = Path(__file__).resolve().parents[1] / ".env.example"

# 模板里的密钥占位符形如 {{your_deepseek_api_key}}，本就应与空默认值不同
_PLACEHOLDER = re.compile(r"^\{\{.*\}\}$")

# 已知且有意为之的差异：ENV 名 -> 原因。
# 当前已无差异——模板与 settings 默认值应完全一致。
# 若确有无法通过"改成一致"解决的差异，在此登记并写明原因；
# 一旦该差异被修复，必须同步删除条目，否则本测试会判失败。
KNOWN_DIVERGENCES: dict[str, str] = {}


def _parse_env_example() -> dict[str, str]:
    """解析 .env.example 的 KEY=VALUE，忽略注释、空行与行内注释。"""
    values: dict[str, str] = {}
    for raw in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.split("#")[0].strip()
    return values


def test_env_example_matches_settings_defaults():
    """模板声明值必须与 settings 默认值一致，除非显式登记为例外。"""
    template = _parse_env_example()
    defaults = Settings(_env_file=None)

    divergent: list[str] = []
    for name in Settings.model_fields:
        env_name = name.upper()
        if env_name not in template:
            continue
        declared = template[env_name]
        if _PLACEHOLDER.match(declared):  # 密钥占位符，本就该不同
            continue
        actual = str(getattr(defaults, name))
        if declared.lower() != actual.lower():
            divergent.append(env_name)

    new_drift = sorted(set(divergent) - set(KNOWN_DIVERGENCES))
    stale_exception = sorted(set(KNOWN_DIVERGENCES) - set(divergent))
    assert not new_drift, f"新出现的漂移（请修正后保持两边一致）: {new_drift}"
    assert not stale_exception, f"例外表已过期（差异已修复，请从 KNOWN_DIVERGENCES 移除）: {stale_exception}"


def test_env_example_documents_every_setting():
    """每个配置项都要在模板中有说明，避免新增配置只活在代码里。"""
    template = _parse_env_example()

    missing = [name for name in Settings.model_fields if name.upper() not in template]

    assert not missing, f"这些配置项未在 .env.example 中说明: {missing}"