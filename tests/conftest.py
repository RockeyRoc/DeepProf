"""pytest 全局夹具。

- 把项目根目录加入 sys.path，使 `import runtime / config / graph` 在任意目录下都能工作；
- 提供 SQLite 临时库夹具，保证测试之间数据隔离、不写进项目 data/ 目录；
- 提供 `make_service` / `make_client` 两个模块级构造函数，供各 API 测试复用装配逻辑
  （此前 test_health / test_tools / test_sessions / test_teaching_turn 各写了一份）。
"""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from fastapi.testclient import TestClient  # noqa: E402

from runtime.providers.fake import FakeProvider  # noqa: E402
from runtime.sandbox.policy import SandboxPolicy  # noqa: E402
from runtime.service import RuntimeService  # noqa: E402
from runtime.storage.sqlite_store import SqliteDatabase  # noqa: E402
from tools import register_default_tools  # noqa: E402


def make_service(*, with_tools: bool = False, with_sandbox: bool = False) -> RuntimeService:
    """构造不触网的 RuntimeService（内存库，测试之间互不干扰）。

    Args:
        with_tools: 是否装配项目级工具（``tools.register_default_tools``）
        with_sandbox: 是否按 settings 构造沙箱策略（教学链路需要）
    """
    kwargs = {"sandbox": SandboxPolicy.from_settings()} if with_sandbox else {}
    service = RuntimeService(provider=FakeProvider(), db=SqliteDatabase(":memory:"), **kwargs)
    if with_tools:
        register_default_tools(service.tools)
    return service


def make_client(service: RuntimeService) -> TestClient:
    """注入 RuntimeService 并返回 TestClient。

    ``api.app`` 在导入时会构造默认单例（读 .env、开 SQLite），因此这里延迟导入，
    避免仅跑非 API 测试时也触发那套装配副作用。
    """
    from api.app import create_app
    from api.deps import get_runtime_service

    app = create_app(runtime=service)
    app.dependency_overrides[get_runtime_service] = lambda: service
    return TestClient(app)


def make_async_client(service: RuntimeService):
    """异步 httpx 客户端：供需要「流式进行中并发发另一条请求」的测试使用
    （如显式取消：SSE 还在读的时候 POST /cancel）。"""
    import httpx

    from api.app import create_app
    from api.deps import get_runtime_service

    app = create_app(runtime=service)
    app.dependency_overrides[get_runtime_service] = lambda: service
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


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