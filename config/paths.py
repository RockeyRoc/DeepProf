"""项目路径工具。

统一把配置里的相对路径解析到项目根目录，避免受当前工作目录影响。
"""
from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def project_path(*parts: str | Path) -> Path:
    """把相对路径拼到项目根目录；绝对路径原样返回。"""
    if not parts:
        return PROJECT_ROOT
    first = Path(parts[0])
    if first.is_absolute():
        return first
    return PROJECT_ROOT.joinpath(*[str(p) for p in parts])


def ensure_parent(path: str | Path) -> Path:
    """确保目标文件的父目录存在，返回解析后的绝对路径。"""
    resolved = project_path(path)
    resolved.parent.mkdir(parents=True, exist_ok=True)
    return resolved