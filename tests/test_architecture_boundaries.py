"""架构边界守卫（v0.6 累积）。

这些测试把“约定不要互相调用”变成“违反即失败”，包含自检以证明检测器真的有效。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

# Runtime 不得反向依赖的包
FORBIDDEN_RUNTIME_IMPORTS = (
    "graph",
    "skills",
    "pet",
    "apps",
    "api",
    "library",
    "models",
    "tools",
    "integrations",
)

# 教学动作名：只允许存在于图侧数据里，不得成为 Runtime 的字符串常量
PEDAGOGICAL_ACTION_WORDS = ("hint", "teach", "correct", "reflect", "socratic")

_ACTION_WORD_PATTERNS = {word: re.compile(rf"\b{word}") for word in PEDAGOGICAL_ACTION_WORDS}

# 图唯一可依赖的端口方法
NARROW_PORT_METHODS = {"execute", "emit"}


def python_files(package: str) -> list[Path]:
    directory = ROOT / package
    if not directory.is_dir():
        return []
    return sorted(path for path in directory.rglob("*.py") if "__pycache__" not in path.parts)


def imported_roots(path: Path) -> set[str]:
    """收集一个文件里所有 import 的顶层包名。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


def string_constants(path: Path, *, include_docstrings: bool = False) -> list[tuple[int, str]]:
    """收集字符串常量（默认排除 docstring 与注释）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    if not include_docstrings:
        for node in ast.walk(tree):
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
                body = getattr(node, "body", None)
                if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
                    if isinstance(body[0].value.value, str):
                        docstrings.add(id(body[0].value))
    result: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            result.append((node.lineno, node.value))
    return result


# ---- 守卫 1：Runtime 不得反向依赖 ----

def test_runtime_does_not_import_forbidden_packages():
    offenders: list[str] = []
    for path in python_files("runtime"):
        for root in imported_roots(path):
            if root in FORBIDDEN_RUNTIME_IMPORTS:
                offenders.append(f"{path.relative_to(ROOT)} -> {root}")
    assert not offenders, "runtime/ 出现反向依赖：" + "; ".join(offenders)


def test_runtime_import_scan_detects_violation(tmp_path):
    """自检：检测器必须能真的发现违规导入（防止守卫退化为恒真）。"""
    sample = tmp_path / "sample.py"
    sample.write_text("import graph.education\nfrom library import crawler\n", encoding="utf-8")
    roots = imported_roots(sample)
    assert {"graph", "library"} <= roots
    assert {"graph", "library"} & set(FORBIDDEN_RUNTIME_IMPORTS)


# ---- 守卫 2：Runtime 不认识教学词汇 ----

def test_runtime_has_no_pedagogical_action_string_constants():
    offenders: list[str] = []
    for path in python_files("runtime"):
        for lineno, value in string_constants(path):
            lowered = value.lower()
            for word, pattern in _ACTION_WORD_PATTERNS.items():
                if pattern.search(lowered):
                    offenders.append(f"{path.relative_to(ROOT)}:{lineno} -> {value!r}")
    assert not offenders, (
        "runtime/ 出现教学动作名字符串常量（应放在图侧绑定表数据里）：" + "; ".join(offenders)
    )


def test_action_word_scan_uses_word_boundaries():
    """'incorrect api key' 里的 correct 不是教学动作名，不能误报。"""
    assert _ACTION_WORD_PATTERNS["correct"].search("incorrect api key") is None
    assert _ACTION_WORD_PATTERNS["correct"].search("correct") is not None
    assert _ACTION_WORD_PATTERNS["hint"].search("hints are disabled") is not None


def test_action_word_scan_detects_violation(tmp_path):
    sample = tmp_path / "sample.py"
    sample.write_text('ACTION = "hint"\nOTHER = "teach"\n', encoding="utf-8")
    found = [value for _, value in string_constants(sample)]
    assert any(word in value.lower() for value in found for word in PEDAGOGICAL_ACTION_WORDS)


def test_docstrings_are_excluded_from_action_word_scan(tmp_path):
    """文档字符串允许解释“为什么这里不出现教学词汇”。"""
    sample = tmp_path / "sample.py"
    sample.write_text('"""Runtime 不认识 hint/teach 等词汇。"""\nX = "safe"\n', encoding="utf-8")
    values = [value for _, value in string_constants(sample)]
    assert values == ["safe"]


# ---- 守卫 3：端口窄面不被加宽 ----

def test_narrow_port_surface_is_frozen():
    from runtime.core.ports import RuntimeHost, RuntimePort

    narrow = {
        name
        for name in dir(RuntimePort)
        if not name.startswith("_") and callable(getattr(RuntimePort, name, None))
    }
    assert narrow == NARROW_PORT_METHODS, f"RuntimePort 窄面被加宽：{sorted(narrow)}"

    wide = {
        name
        for name in dir(RuntimeHost)
        if not name.startswith("_") and callable(getattr(RuntimeHost, name, None))
    }
    assert NARROW_PORT_METHODS <= wide
    assert {"invoke_skill", "call_tool", "generate", "read_memory", "write_memory"} <= wide


def test_port_methods_are_async_where_contract_requires():
    import inspect

    from runtime.core.ports import RuntimePort

    assert inspect.iscoroutinefunction(RuntimePort.execute)
    assert inspect.iscoroutinefunction(RuntimePort.emit)


def test_execute_signature_uses_plain_dicts():
    """契约冻结：RuntimePort.execute 只接受并返回 dict。"""
    import inspect

    from runtime.core.ports import RuntimePort

    signature = inspect.signature(RuntimePort.execute)
    assert list(signature.parameters) == ["self", "request", "ctx"]


# ---- 守卫 4：图侧只能依赖窄面（graph/ 由 MVP-2 提供，此处自检检测能力） ----

WIDE_SURFACE_ATTRIBUTES = ("invoke_skill", "call_tool", "generate", "read_memory", "write_memory")


def graph_wide_surface_accesses(path: Path) -> list[tuple[int, str]]:
    """扫描图侧代码里对宽面方法的属性访问。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in WIDE_SURFACE_ATTRIBUTES:
            hits.append((node.lineno, node.attr))
    return hits


def test_graph_wide_surface_scanner_detects_violation(tmp_path):
    sample = tmp_path / "node.py"
    sample.write_text("async def go(host):\n    return await host.invoke_skill('x', {}, {})\n", encoding="utf-8")
    assert graph_wide_surface_accesses(sample) == [(2, "invoke_skill")]


def test_graph_only_uses_narrow_port():
    files = python_files("graph")
    if not files:
        pytest.skip("graph/ 尚未实现（MVP-2）")
    offenders = [
        f"{path.relative_to(ROOT)}:{lineno} -> {attr}"
        for path in files
        for lineno, attr in graph_wide_surface_accesses(path)
    ]
    assert not offenders, "graph/ 使用了 Runtime 宽面：" + "; ".join(offenders)


# ---- 守卫 5：密钥边界 ----

SECRET_TOKENS = ("api_key", "apikey", "authorization", "secret", "bearer")


def test_api_layer_never_returns_plaintext_key():
    """Provider Settings 只编辑 Profile；任何响应模型都不得含明文密钥字段。"""
    from api.schemas import ProviderProfileIn, ProviderProfileOut

    out_fields = set(ProviderProfileOut.model_fields)
    assert "api_key" not in out_fields
    assert "api_key_ref" in out_fields
    # 提交体允许 api_key（只写），但不得进入任何持久化视图
    assert "api_key" in ProviderProfileIn.model_fields


def test_profile_export_excludes_secret_ref():
    from runtime.providers.profiles import ProviderProfile

    profile = ProviderProfile(profile_id="p", api_key_ref="provider:p", base_url="https://x")
    exported = profile.export()
    assert "api_key_ref" not in exported
    assert "api_key" not in exported


def test_health_endpoint_payload_is_secret_free():
    from runtime.testing import make_service

    service = make_service()
    service.router.add(
        type(service.router.profiles()[0])(
            profile_id="real", base_url="https://example.invalid/v1", api_key_ref="provider:real"
        )
    )
    service.router.set_secret("provider:real", "sk-should-never-appear")

    dumped = repr(service.health())
    assert "sk-should-never-appear" not in dumped
    assert "api_key" not in dumped


def test_provider_status_never_leaks_headers_or_secret():
    from runtime.testing import make_service

    service = make_service()
    for entry in service.health()["providers"]:
        assert "extra_headers" not in entry
        assert "api_key_ref" not in entry


# ---- 守卫 6：工作台只监听环回地址 ----

def test_default_api_host_is_loopback():
    from config.settings import Settings

    assert Settings().api_host in {"127.0.0.1", "::1", "localhost"}


def test_no_hardcoded_non_loopback_binding():
    offenders: list[str] = []
    for path in python_files("api"):
        for lineno, value in string_constants(path):
            if value in {"0.0.0.0", "::"}:
                offenders.append(f"{path.relative_to(ROOT)}:{lineno} -> {value!r}")
    assert not offenders, "api/ 出现非环回地址绑定：" + "; ".join(offenders)


# ---- 守卫 7：契约包与代码同步（细节见 test_contract_consistency.py） ----

def test_runtime_does_not_import_api():
    """反向依赖守卫的显式版本：Runtime 是最底层。"""
    for path in python_files("runtime"):
        assert "api" not in imported_roots(path), f"{path.relative_to(ROOT)} 反向依赖 api/"


# ---- 守卫 8：正式学生链路不得依赖 Pi Agent Core（D-9） ----

def test_product_code_does_not_import_pi():
    """只有 integrations/pi/ 可以碰 Pi；产品链路一律走自有 Runtime。"""
    offenders: list[str] = []
    for package in ("runtime", "api", "graph", "skills", "library", "apps"):
        for path in python_files(package):
            roots = imported_roots(path)
            if "integrations" in roots or "pi" in roots:
                offenders.append(f"{path.relative_to(ROOT)} -> {sorted(roots & {'integrations', 'pi'})}")
    assert not offenders, "产品代码依赖了 Pi：" + "; ".join(offenders)


def test_pi_adapter_lives_only_in_integrations():
    assert (ROOT / "integrations" / "pi" / "rpc_client.py").exists()
    assert (ROOT / "integrations" / "pi" / "event_adapter.py").exists()


def test_pi_events_are_marked_developer_only():
    from integrations.pi.event_adapter import adapt_event, is_developer_only_event

    event = adapt_event({"event": "message.delta", "payload": {"delta": "x"}})
    assert is_developer_only_event(event)