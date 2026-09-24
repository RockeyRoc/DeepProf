"""DeepProf 配置层：三层目录布局中的“持久化用户数据”解析。

程序文件（只读安装目录）与用户数据（``~/.deepprof``）分离；
``DEEPPROF_HOME`` 可覆盖数据根。所有可再生的状态（缓存/日志/临时）不放在这里。
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_HOME = "DEEPPROF_HOME"


def deepprof_home() -> Path:
    """用户数据根：``DEEPPROF_HOME`` > ``~/.deepprof``。"""
    override = os.environ.get(ENV_HOME)
    if override:
        return Path(override).expanduser()
    return Path.home() / ".deepprof"


def config_file() -> Path:
    return deepprof_home() / "config.json"


def providers_file() -> Path:
    """Provider Profile 元数据（不含明文密钥，只含 api_key_ref）。"""
    return deepprof_home() / "providers.json"


def sessions_db() -> Path:
    return deepprof_home() / "sessions.sqlite"


def memory_db() -> Path:
    return deepprof_home() / "memory.sqlite"


def vector_db_dir() -> Path:
    return deepprof_home() / "vectors"


def library_dir() -> Path:
    return deepprof_home() / "library"


def course_data_dir() -> Path:
    return deepprof_home() / "course"


def question_bank_file() -> Path:
    configured = os.environ.get("DEEPPROF_QUESTION_BANK_PATH")
    return expand(configured) if configured else course_data_dir() / "question_bank.json"


def experiment_runs_dir() -> Path:
    return deepprof_home() / "experiments" / "runs"


def experiment_feedback_file() -> Path:
    return deepprof_home() / "experiments" / "feedback.jsonl"


def logs_dir() -> Path:
    return deepprof_home() / "logs"


def log_file() -> Path:
    return logs_dir() / "runtime.log"


def temp_dir() -> Path:
    return deepprof_home() / "tmp"


def expand(path: str | os.PathLike[str]) -> Path:
    """展开 ``~`` 并返回绝对路径。"""
    return Path(path).expanduser().resolve()


def ensure_layout() -> Path:
    """创建用户数据根及必需子目录，返回数据根。"""
    home = deepprof_home()
    for directory in (home, logs_dir(), temp_dir(), library_dir(), course_data_dir(), experiment_runs_dir()):
        directory.mkdir(parents=True, exist_ok=True)
    return home
