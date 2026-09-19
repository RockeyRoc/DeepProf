"""pytest 全局夹具。

- 把项目根目录加入 sys.path，使 `import runtime / config / graph` 在任意目录下都能工作；
- 提供 SQLite 临时库夹具，保证测试之间数据隔离、不写进项目 data/ 目录。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from runtime.providers.fake import FakeProvider  # noqa: E402
from runtime.sandbox.policy import SandboxPolicy  # noqa: E402
from runtime.storage.sqlite_store import SqliteDatabase  # noqa: E402


@pytest.fixture
def db(tmp_path: Path) -> SqliteDatabase:
    """文件型临时数据库（比 :memory: 更接近真实部署，可验证重启恢复）。"""
    database = SqliteDatabase(tmp_path / "test.db")
    yield database
    database.close()


@pytest.fixture
def sandbox() -> SandboxPolicy:
    """只允许访问项目 data 目录的沙箱策略。"""
    from config import project_path

    return SandboxPolicy(allowed_roots=[project_path("data")])


@pytest.fixture
def fake_provider() -> FakeProvider:
    """确定性模型，测试不触网。"""
    return FakeProvider()


@pytest.fixture
def project_root() -> Path:
    return PROJECT_ROOT