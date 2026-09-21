"""用户目录三层布局测试（config/paths.py ↔ desktop/src/main/index.js 的 PATHS）。

布局约定（模仿 Codex 桌面端）：
- 用户数据（持久）→ ``~/.deepprof``，``DEEPPROF_HOME`` 可覆盖（CLI 与 Electron 都认）；
- 可再生状态     → ``%LOCALAPPDATA%\\deepprof``，无该环境变量时退回 ``~/.deepprof/local``。

Python 侧的存储默认值（sqlite / vector / plugins）必须落在用户数据目录，
不能依赖 CWD 或安装目录（安装目录只读）。
"""
from __future__ import annotations

from pathlib import Path

from config.paths import (
    deepprof_home,
    local_data_dir,
    plugins_default_dir,
    sqlite_default_path,
    vector_default_path,
)


def test_deepprof_home_env_override(tmp_path, monkeypatch):
    """DEEPPROF_HOME 优先：开发调试可把用户数据指到仓库内，与个人数据隔离。"""
    target = tmp_path / "isolated-home"
    monkeypatch.setenv("DEEPPROF_HOME", str(target))
    assert deepprof_home() == target


def test_deepprof_home_defaults_to_user_profile(monkeypatch):
    """未设置 DEEPPROF_HOME 时落在用户主目录（~/.deepprof，像 codex 的 ~/.codex）。"""
    monkeypatch.delenv("DEEPPROF_HOME", raising=False)
    assert deepprof_home() == Path.home() / ".deepprof"


def test_local_data_dir_uses_localappdata(tmp_path, monkeypatch):
    """可再生状态归 %LOCALAPPDATA%\\deepprof（Roaming 不用，缓存不值得漫游）。"""
    monkeypatch.delenv("DEEPPROF_HOME", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "localappdata"))
    assert local_data_dir() == tmp_path / "localappdata" / "deepprof"


def test_local_data_dir_falls_back_without_localappdata(tmp_path, monkeypatch):
    """没有 LOCALAPPDATA 的环境退回 ~/.deepprof/local，保证任何环境都有明确落点。"""
    monkeypatch.setenv("DEEPPROF_HOME", str(tmp_path / "home"))
    monkeypatch.delenv("LOCALAPPDATA", raising=False)
    assert local_data_dir() == tmp_path / "home" / "local"


def test_storage_defaults_live_under_user_home(tmp_path, monkeypatch):
    """存储默认值（留空配置时）必须全部落在 ~/.deepprof 下，且目录自动创建。"""
    monkeypatch.setenv("DEEPPROF_HOME", str(tmp_path / "home"))
    home = tmp_path / "home"

    assert sqlite_default_path() == home / "sessions" / "deepprof.db"
    assert vector_default_path() == home / "memory" / "vector_store"
    assert plugins_default_dir() == home / "plugins"

    assert (home / "sessions").is_dir()
    assert (home / "memory").is_dir()
    assert (home / "plugins").is_dir()
