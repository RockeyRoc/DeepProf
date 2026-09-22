"""教学图契约与装配测试（DESIGNv0.6 §4.4 / §6.4 / §13.2 / §16.3 验收）。

锁住四件事：

1. **决策事件不含学生正文**（§6.4 / §13.2 隐私纪律）：学生文本只出现在决策
   `params` 里供能力层使用，事件只记长度与依据字段；
2. **组合根真的装配**（§16.1 第 4 条）：/health 能看到绑定表、Skill 与 Tool；
3. **RAG Skill 的规范化纪律**（§7.3）：不可定位的命中一律丢弃，不编造来源；
4. **节点源码里没有提示模板与 Skill 名**（§16.3 验收）：文案与能力名都在绑定表数据里。
"""

from __future__ import annotations

import asyncio
import ast
import re
from pathlib import Path

from graph.education.bindings import ACTION_BINDINGS, ACTION_CAPABILITIES
from graph.education.builder import run_teaching_turn
from graph.education.nodes import EVENT_DECISION
from graph.education.policies import ACTION_ASK
from runtime.testing import FakeRuntime, RecordingHost
from skills.rag import RAGSkill

NODES_DIR = Path(__file__).resolve().parents[2] / "graph" / "education" / "nodes"

#: 节点源码里不该出现的字符串（docstring 除外）：
#: 能力名（节点不该知道谁来做）与提示词占位符（模板属于绑定表数据）。
FORBIDDEN_NODE_STRINGS = (
    "socratic",
    "quiz",
    "rag",
    "diagnosis",
    "paper_reader",
    "search_textbook",
    "{evidence_block}",
    "{prior_gap_note}",
    "{memory_note}",
    "{conflicts}",
)

_FORBIDDEN_PATTERNS = [
    (token, re.compile(rf"\b{re.escape(token)}\b" if not token.startswith("{") else re.escape(token)))
    for token in FORBIDDEN_NODE_STRINGS
]

BASE_STATE = {
    "session_id": "sess_1",
    "learner_id": "learner_1",
    "trace_id": "trace_1",
    "current_concept": "极限",
    "learning_goal": "理解极限的定义",
}

#: 学生原文里会出现的独特串：它只应出现在 params，不应出现在任何事件载荷里
SECRET_STUDENT_TEXT = "我把极限理解成直接代入就完事"

EVIDENCE = {
    "evidence_id": "doc1#c1@12",
    "document_id": "doc1",
    "chunk_id": "c1",
    "page": 12,
    "text": "极限描述的是趋近过程，不是代入结果。",
    "source": "教材A",
}


# ======================================================================
# 一、隐私纪律
# ======================================================================
def test_decision_events_never_carry_student_text():
    port = FakeRuntime(
        replies=["（模型回复）先看你的思路。"],
        evidence=[dict(EVIDENCE)],
        action_bindings=ACTION_BINDINGS,
    )
    asyncio.run(run_teaching_turn(port, {**BASE_STATE, "user_input": SECRET_STUDENT_TEXT}))

    decisions = [e for e in port.events if e.get("type") == EVENT_DECISION]
    assert decisions, "本轮应当产生教学决策事件"
    for event in decisions:
        flattened = repr(event["payload"])
        assert SECRET_STUDENT_TEXT not in flattened
        # 学生文本只以长度出现
        assert event["payload"]["content_length"] == len(SECRET_STUDENT_TEXT)


# ======================================================================
# 二、组合根装配
# ======================================================================
def test_composition_root_registers_bindings_skills_and_tools():
    """/health 必须同时暴露绑定表、教育 Skill 与检索 Tool（缺装配要响亮）。"""
    from api.app import build_service_with_bindings

    service = build_service_with_bindings()
    health = service.health()

    assert health["bindings"] == ACTION_CAPABILITIES
    assert {"socratic", "rag", "quiz", "diagnosis", "paper_reader"} <= set(health["skills"])
    assert "search_textbook" in health["tools"]


def test_composition_root_can_skip_education_for_runtime_only_runs():
    from api.app import build_service_with_bindings

    service = build_service_with_bindings(bindings={}, with_education=False)
    result = asyncio.run(service.execute({"action": ACTION_ASK}, {}))

    assert result["status"] == "no_binding"
    assert service.health()["bindings"] == {}


# ======================================================================
# 三、检索规范化（§7.3 不编造引用）
# ======================================================================
def test_rag_skill_drops_hits_without_locators():
    """缺 document_id / chunk_id / page 的命中不能当引用——无法定位等于不可验证。"""
    host = RecordingHost(
        tool_result={
            "evidence": [
                {"text": "没有定位标识的片段"},
                {"document_id": "d", "chunk_id": "c", "page": 3, "text": "可定位片段", "source": "教材"},
            ]
        }
    )
    result = asyncio.run(RAGSkill().invoke({"query": "极限", "concept": "极限"}, {}, host))

    assert result["status"] == "ok"
    assert [item["chunk_id"] for item in result["evidence"]] == ["c"]


def test_rag_skill_reports_insufficient_when_tool_returns_nothing():
    host = RecordingHost(tool_result={"evidence": [], "status": "insufficient_evidence"})
    result = asyncio.run(RAGSkill().invoke({"query": "极限"}, {}, host))

    assert result["status"] == "insufficient_evidence"
    assert result["evidence"] == []
    assert "不编造" in result["note"]


# ======================================================================
# 四、节点瘦身（§16.3 验收：提示模板 / 提示词 / 证据获取 / Skill 名都不在节点里）
# ======================================================================
def node_string_constants(path: Path) -> list[tuple[int, str]]:
    """收集节点源码里的字符串常量（排除 docstring，注释本就不是常量）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        body = getattr(node, "body", None)
        if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant):
            if isinstance(body[0].value.value, str):
                docstrings.add(id(body[0].value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_nodes_do_not_carry_templates_or_skill_names():
    """节点只说“做什么”：文案与能力名留在 policies / bindings 里（§4.4、§16.3）。"""
    offenders: list[str] = []
    for path in sorted(NODES_DIR.glob("*.py")):
        for lineno, value in node_string_constants(path):
            for token, pattern in _FORBIDDEN_PATTERNS:
                if pattern.search(value.lower()):
                    offenders.append(f"{path.name}:{lineno} -> {token!r} in {value!r}")
    assert not offenders, "节点源码里出现能力名或提示词占位符：" + "; ".join(offenders)


def test_node_scanner_detects_violation(tmp_path):
    """自检：检测器必须能真的发现违规（防止守卫退化为恒真）。"""
    sample = tmp_path / "node.py"
    sample.write_text('A = "search_textbook"\nB = "讲解模板：{evidence_block}"\n', encoding="utf-8")
    hits = [
        token
        for _lineno, value in node_string_constants(sample)
        for token, pattern in _FORBIDDEN_PATTERNS
        if pattern.search(value.lower())
    ]
    assert sorted(hits) == ["search_textbook", "{evidence_block}"]


def test_node_scanner_excludes_docstrings(tmp_path):
    """文档字符串允许解释“为什么用绑定表”，不算违规。"""
    sample = tmp_path / "node.py"
    sample.write_text('"""本节点不调用 socratic / rag 能力。"""\nX = "safe"\n', encoding="utf-8")
    assert [value for _lineno, value in node_string_constants(sample)] == ["safe"]