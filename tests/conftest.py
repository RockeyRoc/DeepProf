"""测试公共配置：把项目根加入 sys.path，并提供隔离的用户数据根。"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


@pytest.fixture(autouse=True)
def isolated_home(tmp_path, monkeypatch):
    """所有测试都写入临时目录，绝不触碰真实的 ~/.deepprof。"""
    home = tmp_path / "deepprof-home"
    monkeypatch.setenv("DEEPPROF_HOME", str(home))
    yield home


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT