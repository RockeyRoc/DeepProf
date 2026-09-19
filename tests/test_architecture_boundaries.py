"""架构边界守护测试（DESIGNv0.4 §4.4 / §9.1）。

设计文档规定的依赖方向是单向的：

    UI / API → Pedagogical Graph → RuntimePort → Runtime Services → Infrastructure

因此 `runtime/` 下任何模块都不得静态导入上层包 `graph` / `skills` / `pet`。
这条约束此前只写在 docstring 里（如 `graph/education/nodes/__init__.py`），
没有自动化兜底：一旦有人为了图节点"顺手"在 Runtime 里加一个 import，
依赖就会反向，Runtime 将无法在无教学图的场景（如 `scripts/chat_repl.py`）
独立使用，"两层可独立演进、独立评测"的主张也就不再成立。

本测试用 AST 解析而不是文本匹配：
- 只统计真正的 import 语句，docstring / 字符串里的示例不会误判；
- 只统计绝对导入（level == 0）；`from .skills import ...` 是 runtime 内部
  相对导入（对应 `runtime/skills.py`），不属于反向依赖。

同一条边界还有两层约束：

1. **词汇约束**：`runtime/` 里不得出现教学动作名（hint / teach …）。
   否则 action → capability 的映射仍然住在 Runtime 里（只是从 if/elif 换成了字符串），
   WHAT / HOW 并没有真正分开。绑定表是**数据**，必须由组合根注入。
2. **端口约束**：`graph/` 只能碰 RuntimePort 的窄面（execute / emit）。
   Skill / Tool / Provider / 学情记忆读写都是"Runtime Execution"，
   图要它们时必须写一条 PedagogicalDecision 并配绑定（见 graph/education/bindings.py）。
   端口面上平铺方法时，这条边界只能靠自觉；分面之后可以机械检查。

注意边界：`runtime/plugins/lifecycle.py` 会用 importlib 按路径加载插件文件，
那是"插件通过 Service Registry 提供能力"的受控机制，不是 Runtime 对上层包的
静态依赖，因此不在本测试的判定范围内。
"""
from __future__ import annotations

import ast
from pathlib import Path

import pytest

from runtime.core.ports import RuntimeHost, RuntimePort

#: 禁止被 runtime 反向依赖的上层包（顶层包名）。
#: 需要教学能力时应通过 runtime.core.ports.RuntimePort 由调用方注入。
FORBIDDEN_TOP_LEVEL = ("graph", "skills", "pet")

#: 宽面（Runtime Execution）的方法名：只有 ActionDispatcher 与 Capability 实现能用。
#: 图节点一旦出现这些属性访问，就说明它又在自己编排 HOW 了。
WIDE_FACE_METHODS = ("invoke_skill", "call_tool", "generate", "read_memory", "write_memory")


def _violations(source: str, filename: str = "<source>") -> list[str]:
    """找出源码中违反依赖方向的导入，返回可读的位置描述列表。"""
    found: list[str] = []
    tree = ast.parse(source, filename=filename)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".")[0] in FORBIDDEN_TOP_LEVEL:
                    found.append(f"{filename}:{node.lineno}: import {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            if node.level:  # 相对导入（from .skills import ...），runtime 内部，放行
                continue
            module = str(node.module or "")
            if module.split(".")[0] in FORBIDDEN_TOP_LEVEL:
                found.append(f"{filename}:{node.lineno}: from {module} import ...")
    return found


def test_runtime_does_not_import_upper_layers(project_root: Path) -> None:
    """runtime/ 下的模块不得导入 graph / skills / pet（§9.1 禁止反向依赖）。"""
    runtime_dir = project_root / "runtime"
    files = sorted(runtime_dir.rglob("*.py"))
    assert files, f"未扫描到任何 runtime 源码，请检查路径: {runtime_dir}"

    violations: list[str] = []
    for path in files:
        violations.extend(
            _violations(
                path.read_text(encoding="utf-8"),
                str(path.relative_to(project_root)).replace("\\", "/"),
            )
        )

    assert not violations, (
        f"检测到反向依赖：runtime/ 不得导入上层包 {list(FORBIDDEN_TOP_LEVEL)}"
        "（DESIGNv0.4 §9.1）。\n"
        "需要教学能力时，请改为依赖 runtime.core.ports.RuntimePort，"
        "由调用方（教学图 / API）注入实现。\n" + "\n".join(violations)
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("from graph.education import builder", 1),
        ("import pet.emotion", 1),
        ("from skills.socratic import SocraticSkill", 1),
        ("from .skills import SkillRegistry", 0),  # runtime 内部相对导入
        ("from runtime.core.ports import RuntimePort", 0),
        ('"""示例，不是导入：from graph import x"""', 0),  # docstring 不算
    ],
)
def test_violation_detector_self_check(source: str, expected: int) -> None:
    """校验检测器本身有效，避免清单或解析写错导致测试永远"全绿"。"""
    assert len(_violations(source)) == expected


#: 教学动作名 / 教学 Skill 名：它们只应出现在**注入的数据**里（graph/education/bindings.py），
#: 不应出现在 runtime 源码的字符串常量中。否则 action → capability 的映射就还在
#: Runtime 里（只是从 if/elif 变成了字符串），WHAT/HOW 的边界没有真正立起来。
#:
#: 只取语义无歧义的词：`ask` / `test` / `end` 在日常英文里也会出现，纳入会造成误报；
#: `pedagogy.*` 是 runtime/core/events.py 里冻结的事件类型契约（§18.2），不是动作映射，
#: 因此同样不纳入。
TEACHING_VOCABULARY = ("hint", "teach", "correct", "reflect", "socratic")


def _string_literals(source: str, filename: str = "<source>") -> list[tuple[int, str]]:
    """取出源码里的字符串常量，排除 docstring（说明性文字提到教学词是允许的）。"""
    tree = ast.parse(source, filename=filename)
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None) or []
        if (
            body
            and isinstance(body[0], ast.Expr)
            and isinstance(body[0].value, ast.Constant)
            and isinstance(body[0].value.value, str)
        ):
            docstrings.add(id(body[0].value))

    found: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            if id(node) not in docstrings:
                found.append((node.lineno, node.value))
    return found


def test_runtime_does_not_hardcode_teaching_actions(project_root: Path) -> None:
    """runtime/ 里的字符串常量不得出现教学动作名（§4.4：WHAT / HOW 分离）。"""
    runtime_dir = project_root / "runtime"
    violations: list[str] = []
    for path in sorted(runtime_dir.rglob("*.py")):
        relative = str(path.relative_to(project_root)).replace("\\", "/")
        for lineno, text in _string_literals(path.read_text(encoding="utf-8"), relative):
            if text.strip().lower() in TEACHING_VOCABULARY:
                violations.append(f"{relative}:{lineno}: {text!r}")

    assert not violations, (
        "runtime/ 不得硬编码教学动作名或教学 Skill 名——那只是把耦合从结构搬到了词汇。\n"
        "请把 action → capability 的映射写成数据（graph/education/bindings.py），"
        "由组合根注入（§4.4）。\n" + "\n".join(violations)
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ('NODE = "hint"', ["hint"]),
        ('"""说明：hint 由模板渲染"""', []),  # docstring 里的教学词不算
        ('def f():\n    """节点名，可写 teach"""\n    return "x"', []),
        ('MAPPING = {"hint": "render_template"}', ["hint"]),
    ],
)
def test_teaching_vocabulary_detector_self_check(source: str, expected: list[str]) -> None:
    """校验词汇检测器本身有效（docstring 必须被排除，键名必须被抓到）。"""
    found = [
        text
        for _, text in _string_literals(source)
        if text.strip().lower() in TEACHING_VOCABULARY
    ]
    assert found == expected


# ======================================================================
# 端口分面：图只能碰窄面
# ======================================================================
def _wide_face_hits(source: str, filename: str = "<source>") -> list[str]:
    """找出源码里对宽面方法的属性访问（``x.invoke_skill`` / ``port.generate``…）。"""
    found: list[str] = []
    for node in ast.walk(ast.parse(source, filename=filename)):
        if isinstance(node, ast.Attribute) and node.attr in WIDE_FACE_METHODS:
            found.append(f"{filename}:{node.lineno}: .{node.attr}")
    return found


def test_graph_only_touches_the_narrow_port_face(project_root: Path) -> None:
    """graph/ 只能调 RuntimePort 的 execute / emit（§4.4）。

    图要 Skill、Tool、模型或学情记忆读写时，正确做法是写一条
    PedagogicalDecision 并配绑定（graph/education/bindings.py），
    让能力层去落地——而不是在节点里直接调。
    """
    graph_dir = project_root / "graph"
    files = sorted(graph_dir.rglob("*.py"))
    assert files, f"未扫描到任何 graph 源码，请检查路径: {graph_dir}"

    violations: list[str] = []
    for path in files:
        violations.extend(
            _wide_face_hits(
                path.read_text(encoding="utf-8"),
                str(path.relative_to(project_root)).replace("\\", "/"),
            )
        )

    assert not violations, (
        f"图侧触碰了 Runtime 宽面 {list(WIDE_FACE_METHODS)}（DESIGNv0.4 §4.4）。\n"
        "请改为：节点产出 PedagogicalDecision → 组合根注入的绑定 → 能力层执行。\n"
        + "\n".join(violations)
    )


@pytest.mark.parametrize(
    ("source", "expected"),
    [
        ("await port.invoke_skill('rag', {}, ctx)", 1),
        ("await port.read_memory(q, ctx)", 1),
        ("port.generate(req, ctx)", 1),
        ("await port.execute(d, ctx)", 0),  # 窄面
        ("await port.emit(event)", 0),  # 窄面
        ('MAPPING = {"capability": "invoke_skill"}', 0),  # 字符串不是调用
        ('"""说明：能力名 invoke_skill 由绑定声明"""', 0),
    ],
)
def test_wide_face_detector_self_check(source: str, expected: int) -> None:
    """校验端口检测器本身有效（窄面与字符串不得误伤）。"""
    assert len(_wide_face_hits(source)) == expected


def _protocol_surface(protocol: type) -> set[str]:
    """协议声明的方法名。

    必须走 MRO：Protocol 的子协议**不会**把继承来的方法复制进自己的 `__dict__`，
    只看 `RuntimeHost.__dict__` 会漏掉 execute / emit。
    """
    return {
        name
        for klass in protocol.__mro__
        for name in vars(klass)
        if not name.startswith("_")
    }


def test_runtime_port_is_deliberately_narrow() -> None:
    """窄面必须只有 execute / emit；宽面必须覆盖 Runtime Execution 的全部方法。

    这条断言是"分面"这件事的契约本身：有人往 RuntimePort 上加一个
    generate/read_memory 之类的便利方法，图侧边界立刻就不再成立（图会顺手去调），
    所以这里把它钉死。
    """
    narrow = _protocol_surface(RuntimePort)
    wide = _protocol_surface(RuntimeHost)

    assert narrow == {"execute", "emit"}
    assert narrow <= wide, "宽面应包含窄面（RuntimeService 一套方法满足两个面）"
    assert set(WIDE_FACE_METHODS) <= wide


def test_runtime_service_satisfies_both_faces() -> None:
    """真实 Runtime 必须同时满足窄面与宽面，否则图侧测试与生产的端口形状会漂移。"""
    from runtime.providers.fake import FakeProvider
    from runtime.sandbox.policy import SandboxPolicy
    from runtime.service import RuntimeService
    from runtime.storage.sqlite_store import SqliteDatabase

    service = RuntimeService(
        provider=FakeProvider(),
        db=SqliteDatabase(":memory:"),
        sandbox=SandboxPolicy.from_settings(),
    )

    assert isinstance(service, RuntimePort)
    assert isinstance(service, RuntimeHost)