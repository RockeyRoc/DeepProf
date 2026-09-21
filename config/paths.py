"""项目路径工具。

统一把配置里的相对路径解析到项目根目录，避免受当前工作目录影响。

用户目录三层布局（模仿 Codex 桌面端：程序 / 用户数据 / 可再生缓存分离）：
- 程序本体（只读）→ 安装目录，安装时用户可选，运行期不写；
- 用户数据（持久）→ ``~/.deepprof``（``deepprof_home``），唯一需要备份的目录；
- 可再生状态     → ``%LOCALAPPDATA%\\deepprof``（``local_data_dir``），随时可删。
"""
from __future__ import annotations

import os
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


def ensure_dir(path: str | Path) -> Path:
    """确保目录存在（mkdir -p），返回 Path。失败交由调用方处理。"""
    resolved = Path(path)
    resolved.mkdir(parents=True, exist_ok=True)
    return resolved


# ============ 用户目录三层布局（与 desktop/src/main/index.js 的 PATHS 同一套规则） ============

#: 主目录名：CLI（scripts/chat_repl.py）与 Electron 主进程都认 DEEPPROF_HOME 覆盖
_HOME_ENV = "DEEPPROF_HOME"


def deepprof_home() -> Path:
    """用户数据主目录：``DEEPPROF_HOME`` 环境变量优先，否则 ``~/.deepprof``。

    开发调试可把 DEEPPROF_HOME 指到仓库内某个目录，使开发状态与个人数据隔离。
    """
    env = os.environ.get(_HOME_ENV, "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".deepprof"


def local_data_dir() -> Path:
    """可再生状态目录：``%LOCALAPPDATA%\\deepprof``（缓存 / 日志 / 临时文件）。

    选 Local 而不是 Roaming：这些内容跟着机器走，不值得漫游同步；
    没有 LOCALAPPDATA 的环境（非 Windows / 受限沙箱）退回 ``~/.deepprof/local``。
    """
    base = os.environ.get("LOCALAPPDATA", "").strip()
    if base:
        return Path(base) / "deepprof"
    return deepprof_home() / "local"


def sqlite_default_path() -> Path:
    """会话与事件库的默认位置：``~/.deepprof/sessions/deepprof.db``。"""
    return ensure_dir(deepprof_home() / "sessions") / "deepprof.db"


def vector_default_path() -> Path:
    """向量库的默认位置：``~/.deepprof/memory/vector_store``。"""
    return ensure_dir(deepprof_home() / "memory") / "vector_store"


def plugins_default_dir() -> Path:
    """用户安装的第三方插件扫描目录：``~/.deepprof/plugins``。"""
    return ensure_dir(deepprof_home() / "plugins")