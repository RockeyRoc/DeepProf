"""三层目录布局：程序文件 / 用户数据 / 可再生状态的解析规则。"""

from __future__ import annotations

from pathlib import Path

from config import paths
from config.settings import Settings


def test_home_defaults_to_dot_deepprof(monkeypatch):
    monkeypatch.delenv("DEEPPROF_HOME", raising=False)
    assert paths.deepprof_home() == Path.home() / ".deepprof"


def test_home_honors_env_override(isolated_home):
    assert paths.deepprof_home() == isolated_home


def test_home_expands_tilde(monkeypatch):
    monkeypatch.setenv("DEEPPROF_HOME", "~/custom-deepprof")
    assert paths.deepprof_home() == Path.home() / "custom-deepprof"


def test_ensure_layout_creates_user_data_dirs(isolated_home):
    home = paths.ensure_layout()
    assert home == isolated_home
    for directory in (paths.logs_dir(), paths.temp_dir(), paths.plugins_dir(), paths.pets_dir()):
        assert directory.is_dir()


def test_empty_settings_paths_resolve_under_home(isolated_home):
    settings = Settings()
    assert settings.resolved_sqlite_path == isolated_home / "sessions.sqlite"
    assert settings.resolved_vector_db_path == isolated_home / "vectors"
    assert settings.resolved_plugin_dir == isolated_home / "plugins"


def test_explicit_settings_paths_win(tmp_path):
    settings = Settings(sqlite_path=str(tmp_path / "custom.sqlite"))
    assert settings.resolved_sqlite_path == (tmp_path / "custom.sqlite").resolve()


def test_regenerable_state_is_deletable(isolated_home):
    """删除可再生状态层必须安全：只影响缓存/日志/临时，不影响配置。"""
    paths.ensure_layout()
    (paths.log_file()).write_text("log", encoding="utf-8")
    import shutil

    shutil.rmtree(paths.logs_dir())
    assert not paths.logs_dir().exists()
    assert paths.deepprof_home().exists()