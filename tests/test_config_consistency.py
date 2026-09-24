"""配置一致性：settings 默认值必须与 .env.example 声明对齐，防止配置漂移。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from config.settings import Settings

ROOT = Path(__file__).resolve().parents[1]
ENV_EXAMPLE = ROOT / ".env.example"

# settings 字段 -> .env.example 中的键
FIELD_TO_ENV = {
    "llm_max_tokens": "DEEPPROF_LLM_MAX_TOKENS",
    "llm_timeout_seconds": "DEEPPROF_LLM_TIMEOUT_SECONDS",
    "llm_max_retries": "DEEPPROF_LLM_MAX_RETRIES",
    "stream_include_usage": "DEEPPROF_STREAM_INCLUDE_USAGE",
    "probe_max_tokens": "DEEPPROF_PROBE_MAX_TOKENS",
    "api_host": "DEEPPROF_API_HOST",
    "api_port": "DEEPPROF_API_PORT",
    "sqlite_path": "DEEPPROF_SQLITE_PATH",
    "vector_db_path": "DEEPPROF_VECTOR_DB_PATH",
    "library_dir": "DEEPPROF_LIBRARY_DIR",
    "library_chunk_size": "DEEPPROF_LIBRARY_CHUNK_SIZE",
    "library_chunk_overlap": "DEEPPROF_LIBRARY_CHUNK_OVERLAP",
    "library_max_import_bytes": "DEEPPROF_LIBRARY_MAX_IMPORT_BYTES",
    "data_structures_pdf_path": "DEEPPROF_DATA_STRUCTURES_PDF",
    "sandbox_allowlist": "DEEPPROF_SANDBOX_ALLOWLIST",
    "log_level": "DEEPPROF_LOG_LEVEL",
}


def _env_entries() -> dict[str, str]:
    entries: dict[str, str] = {}
    for line in ENV_EXAMPLE.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        entries[key.strip()] = value.strip()
    return entries


def test_every_settings_field_is_documented():
    declared = set(Settings().__slots__)
    documented = {FIELD_TO_ENV[field] for field in declared if field in FIELD_TO_ENV}
    assert declared == set(FIELD_TO_ENV), (
        f"新增/删除设置项后必须同步 FIELD_TO_ENV；未覆盖: {sorted(declared - set(FIELD_TO_ENV))}"
    )
    assert documented == set(FIELD_TO_ENV.values())


def test_env_example_keys_exist():
    entries = _env_entries()
    missing = [key for key in FIELD_TO_ENV.values() if key not in entries]
    assert not missing, f".env.example 缺少键: {missing}"


@pytest.mark.parametrize("field,env_key", sorted(FIELD_TO_ENV.items()))
def test_default_matches_env_example(field: str, env_key: str):
    expected = getattr(Settings(), field)
    raw = _env_entries()[env_key]
    if raw == "":
        actual = "" if isinstance(expected, str) else None
        assert expected in ("", None) or expected == [], (
            f"{env_key} 留空表示“使用默认”，但默认值是 {expected!r}"
        )
        return
    if isinstance(expected, bool):
        actual: object = raw.lower() in {"1", "true", "yes", "on"}
    elif isinstance(expected, int):
        actual = int(raw)
    elif isinstance(expected, float):
        actual = float(raw)
    elif isinstance(expected, list):
        actual = [item.strip() for item in raw.split(",") if item.strip()]
    else:
        actual = raw
    assert actual == expected, f"{env_key} 声明 {actual!r} 与默认值 {expected!r} 不一致"


def test_env_example_has_no_duplicate_keys():
    keys = re.findall(r"^(DEEPPROF_[A-Z0-9_]+)=", ENV_EXAMPLE.read_text(encoding="utf-8"), re.M)
    duplicates = {key for key in keys if keys.count(key) > 1}
    assert not duplicates, f".env.example 重复键: {sorted(duplicates)}"


def test_defaults_are_sane():
    settings = Settings()
    assert settings.api_host == "127.0.0.1", "工作台只允许监听环回地址"
    assert settings.llm_max_tokens >= 4096, "过小的 max_tokens 会导致推理模型空正文截断"
    assert settings.probe_max_tokens >= 256, "过小的探测预算会让推理模型假阴性"
