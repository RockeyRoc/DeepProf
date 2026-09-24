"""运行时设置。默认值必须与 ``.env.example`` 一致（守卫见 tests/test_config_consistency.py）。"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from config import paths

TRUTHY = {"1", "true", "yes", "on"}


def _env_str(key: str, default: str) -> str:
    value = os.environ.get(key)
    return default if value is None else value


def _env_int(key: str, default: int) -> int:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return int(raw)


def _env_float(key: str, default: float) -> float:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return float(raw)


def _env_bool(key: str, default: bool) -> bool:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return default
    return raw.strip().lower() in TRUTHY


def _env_list(key: str, default: list[str]) -> list[str]:
    raw = os.environ.get(key)
    if raw is None or raw.strip() == "":
        return list(default)
    return [item.strip() for item in raw.split(",") if item.strip()]


@dataclass(slots=True)
class Settings:
    """DeepProf Runtime 的可配置项。"""

    # 模型调用
    llm_max_tokens: int = 4096
    llm_timeout_seconds: float = 120.0
    llm_max_retries: int = 2
    stream_include_usage: bool = True

    # Provider 能力探测
    probe_max_tokens: int = 512

    # API / Gateway（0 = 动态端口，仅监听环回地址）
    api_host: str = "127.0.0.1"
    api_port: int = 0

    # 存储路径（留空 => 解析到用户数据根）
    sqlite_path: str = ""
    vector_db_path: str = ""
    library_dir: str = ""

    # Resource library / offline retrieval
    library_chunk_size: int = 800
    library_chunk_overlap: int = 120
    library_max_import_bytes: int = 50_000_000
    data_structures_pdf_path: str = ""

    # 沙箱
    sandbox_allowlist: list[str] = field(default_factory=lambda: ["~/.deepprof"])

    log_level: str = "INFO"

    @classmethod
    def from_env(cls) -> "Settings":
        try:
            from dotenv import load_dotenv
            load_dotenv()
        except ImportError:  # pragma: no cover - dependency is declared by the project
            pass
        return cls(
            llm_max_tokens=_env_int("DEEPPROF_LLM_MAX_TOKENS", 4096),
            llm_timeout_seconds=_env_float("DEEPPROF_LLM_TIMEOUT_SECONDS", 120.0),
            llm_max_retries=_env_int("DEEPPROF_LLM_MAX_RETRIES", 2),
            stream_include_usage=_env_bool("DEEPPROF_STREAM_INCLUDE_USAGE", True),
            probe_max_tokens=_env_int("DEEPPROF_PROBE_MAX_TOKENS", 512),
            api_host=_env_str("DEEPPROF_API_HOST", "127.0.0.1"),
            api_port=_env_int("DEEPPROF_API_PORT", 0),
            sqlite_path=_env_str("DEEPPROF_SQLITE_PATH", ""),
            vector_db_path=_env_str("DEEPPROF_VECTOR_DB_PATH", ""),
            library_dir=_env_str("DEEPPROF_LIBRARY_DIR", ""),
            library_chunk_size=_env_int("DEEPPROF_LIBRARY_CHUNK_SIZE", 800),
            library_chunk_overlap=_env_int("DEEPPROF_LIBRARY_CHUNK_OVERLAP", 120),
            library_max_import_bytes=_env_int("DEEPPROF_LIBRARY_MAX_IMPORT_BYTES", 50_000_000),
            data_structures_pdf_path=_env_str("DEEPPROF_DATA_STRUCTURES_PDF", ""),
            sandbox_allowlist=_env_list("DEEPPROF_SANDBOX_ALLOWLIST", ["~/.deepprof"]),
            log_level=_env_str("DEEPPROF_LOG_LEVEL", "INFO"),
        )

    # ---- 解析后的路径（留空 => 用户数据根） ----

    @property
    def resolved_sqlite_path(self) -> Path:
        return paths.expand(self.sqlite_path) if self.sqlite_path else paths.sessions_db()

    @property
    def resolved_vector_db_path(self) -> Path:
        return paths.expand(self.vector_db_path) if self.vector_db_path else paths.vector_db_dir()

    @property
    def resolved_library_dir(self) -> Path:
        return paths.expand(self.library_dir) if self.library_dir else paths.library_dir()

    @property
    def resolved_data_structures_pdf_path(self) -> Path | None:
        return paths.expand(self.data_structures_pdf_path) if self.data_structures_pdf_path else None
