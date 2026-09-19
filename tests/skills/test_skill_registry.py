"""Skill 注册表与五个教育能力的测试（DESIGNv0.4 §5.3 / §6.3 / §16.3）。

覆盖：
- 注册与能力清单（describe）；
- 重名注册报错，不静默覆盖；
- 调用未注册 Skill 返回结构化失败（不抛异常打断教学图）；
- RAG 无命中返回 insufficient_evidence，绝不编造来源；
- 尚未接入的能力（学情模型、论文 PDF 链路）诚实返回 not_implemented。

同步测试内部用 asyncio.run 包一层，不依赖 pytest-asyncio 的 asyncio_mode 配置。
"""
from __future__ import annotations

import asyncio

import pytest

from runtime.core.ports import RuntimeContext
from runtime.skills import Skill, SkillDescriptor, SkillRegistry
from runtime.testing import FakeRuntime
from skills import register_default_skills
from skills.diagnosis import DiagnosisSkill
from skills.paper_reader import PaperReaderSkill
from skills.quiz import QuizSkill
from skills.rag import RAGSkill, SEARCH_TOOL
from skills.socratic import SocraticSkill

CTX = RuntimeContext(session_id="sess_1", learner_id="learner_1", trace_id="trace_1")

HITS = [
    {
        "document_id": "doc1",
        "chunk_id": "c1",
        "page": 12,
        "text": "梯度下降沿负梯度方向迭代更新参数。",
        "source": "教材A",
        "score": 0.82,
    }
]


def invoke(registry: SkillRegistry, name: str, payload: dict, port: FakeRuntime) -> dict:
    return asyncio.run(registry.invoke(name, payload, CTX, port))


def registry_with(skill: Skill) -> SkillRegistry:
    """构造只含一个 Skill 的注册表（register 返回的是 skill，不是 registry）。"""
    registry = SkillRegistry()
    registry.register(skill)
    return registry


def rag_port(data: dict) -> FakeRuntime:
    """构造一个 search_textbook 有结构化返回的 FakeRuntime。"""
    return FakeRuntime(
        tool_results={SEARCH_TOOL: {"ok": True, "content": "命中 1 条", "data": data, "error": None}}
    )


# ======================================================================
# 一、注册表
# ======================================================================
def test_register_and_describe_skill():
    """注册后可在能力清单中看到描述信息（供文档/门户/评审使用）。"""
    registry = SkillRegistry()
    registry.register(SocraticSkill())
    assert registry.names() == ["socratic"]
    assert registry.has("socratic") is True
    described = registry.describe()
    assert described[0]["name"] == "socratic"
    assert described[0]["owner"] == "孙一新"
    assert described[0]["when_to_use"]


def test_register_duplicate_name_raises():
    """重名注册必须报错，避免静默替换教学能力。"""
    registry = SkillRegistry()
    registry.register(SocraticSkill())
    with pytest.raises(ValueError, match="Skill 名称重复"):
        registry.register(SocraticSkill())


def test_register_requires_name():
    """没有 name 的 Skill 不能注册（能力必须可被引用）。"""

    class Nameless(Skill):
        descriptor = SkillDescriptor(name="", description="无名字")

        async def handle(self, payload, ctx, port):  # pragma: no cover - 不会被调用
            return {"status": "ok"}

    with pytest.raises(ValueError, match="必须有 name"):
        SkillRegistry().register(Nameless())


def test_invoke_unregistered_returns_structured_failure():
    """未注册 Skill 返回结构化失败，而不是抛异常打断教学图（§7.3）。"""
    registry = SkillRegistry()
    result = invoke(registry, "nope", {}, FakeRuntime())
    assert result["status"] == "skill_not_found"
    assert result["skill"] == "nope"
    assert result["error"]["code"] == "skill_not_found"


def test_register_default_skills_registers_all_five():
    """默认注册五个教育能力，并带责任人（可直接用于交接评审）。"""
    registry = register_default_skills(SkillRegistry())
    assert registry.names() == ["diagnosis", "paper_reader", "quiz", "rag", "socratic"]
    owners = {item["name"]: item["owner"] for item in registry.describe()}
    assert owners["rag"] == "许阳毅"
    assert owners["socratic"] == "孙一新"
    assert owners["quiz"] == "孙一新"
    assert owners["diagnosis"] == "欧阳文凯"


# ======================================================================
# 二、RAG：证据纪律
# ======================================================================
def test_rag_returns_locatable_evidence():
    """有结构化命中时返回 Evidence 契约字段与稳定 evidence_id。"""
    result = invoke(registry_with(RAGSkill()), "rag", {"query": "梯度下降"}, rag_port({"hits": HITS}))
    assert result["status"] == "ok"
    assert result["count"] == 1
    evidence = result["evidence"][0]
    assert evidence["document_id"] == "doc1"
    assert evidence["chunk_id"] == "c1"
    assert evidence["page"] == 12
    assert evidence["source"] == "教材A"
    assert evidence["evidence_id"] == "doc1#c1@12"


def test_rag_insufficient_when_tool_missing():
    """工具不存在/调用失败 → insufficient_evidence，且 evidence 为空。"""
    result = invoke(registry_with(RAGSkill()), "rag", {"query": "梯度下降"}, FakeRuntime())
    assert result["status"] == "insufficient_evidence"
    assert result["evidence"] == []
    assert result["count"] == 0
    assert "insufficient_evidence" in result["note"]


def test_rag_insufficient_when_no_hits():
    """工具成功但无命中 → insufficient_evidence（绝不编造来源）。"""
    result = invoke(registry_with(RAGSkill()), "rag", {"query": "梯度下降"}, rag_port({"hits": []}))
    assert result["status"] == "insufficient_evidence"
    assert result["evidence"] == []


def test_rag_insufficient_when_hits_lack_locators():
    """命中缺少 document_id/chunk_id/page 时不可作为引用，同样返回证据不足。"""
    result = invoke(
        registry_with(RAGSkill()),
        "rag",
        {"query": "梯度下降"},
        rag_port({"hits": [{"text": "没有定位标识的片段"}]}),
    )
    assert result["status"] == "insufficient_evidence"
    assert result["evidence"] == []
    assert "无法作为引用" in result["note"]


def test_rag_insufficient_when_query_empty():
    """空检索问题不发工具调用，直接证据不足。"""
    port = FakeRuntime()
    result = invoke(registry_with(RAGSkill()), "rag", {"query": "  "}, port)
    assert result["status"] == "insufficient_evidence"
    assert port.calls_of("call_tool") == []


# ======================================================================
# 三、Socratic / Quiz：真调用 port.generate
# ======================================================================
def test_socratic_returns_question_from_model():
    """Socratic 真调用 port.generate，并声明答案保护只是提示词级约束。"""
    port = FakeRuntime(replies=["你觉得第一步应该先确定什么？"])
    result = invoke(registry_with(SocraticSkill()), "socratic", {"concept": "梯度下降", "avoid_answer": True}, port)
    assert result["status"] == "ok"
    assert result["question"] == "你觉得第一步应该先确定什么？"
    assert result["avoid_answer"] is True
    assert result["answer_guard"].startswith("prompt_only")
    assert port.calls_of("generate"), "应当真的调用模型"


def test_socratic_reports_error_when_model_returns_nothing():
    """模型无输出时返回结构化 error，由调用方退回策略模板。"""
    port = FakeRuntime(default_reply="")
    result = invoke(registry_with(SocraticSkill()), "socratic", {"concept": "梯度下降"}, port)
    assert result["status"] == "error"
    assert result["question"] == ""


def test_quiz_marks_item_bank_not_connected():
    """Quiz 出题可用，但必须标注题库未接入（不说成正式测评）。"""
    port = FakeRuntime(replies=["请说明梯度下降的停止条件。"])
    result = invoke(registry_with(QuizSkill()), "quiz", {"mode": "generate", "concept": "梯度下降"}, port)
    assert result["status"] == "ok"
    assert result["item_bank_connected"] is False
    assert "题库" in result["note"]
    assert result["item"]["item_id"] == ""


def test_quiz_evaluation_returns_no_correctness_claim():
    """评价只给学习性反馈，不返回确定性对错（避免把无判分成结论乱写学情）。"""
    port = FakeRuntime(replies=["你少了收敛判断这一步。"])
    result = invoke(
        registry_with(QuizSkill()),
        "quiz",
        {"mode": "evaluate", "concept": "梯度下降", "student_answer": "沿负梯度走"},
        port,
    )
    assert result["status"] == "ok"
    assert result["evaluation"]["correct"] is None
    assert result["evaluation"]["auto_grading_connected"] is False


# ======================================================================
# 四、诚实原则：未接入的能力返回 not_implemented
# ======================================================================
def test_diagnosis_is_not_implemented_and_outputs_no_estimate():
    """学情模型未接入：不给任何掌握度数字，并写明由谁补（§18.1）。"""
    result = invoke(registry_with(DiagnosisSkill()), "diagnosis", {"concept": "梯度下降", "attempt_count": 3}, FakeRuntime())
    assert result["status"] == "not_implemented"
    assert result["estimate"] is None
    assert result["uncertainty"] is None
    assert "欧阳文凯" in result["note"]
    assert "LearnerModel" in result["note"]
    assert "estimate" in result["expected_output_schema"]


def test_paper_reader_without_text_is_not_implemented():
    """没有正文时不假装能读论文：返回 not_implemented 并说明缺什么。"""
    result = invoke(registry_with(PaperReaderSkill()), "paper_reader", {"question": "总结一下"}, FakeRuntime())
    assert result["status"] == "not_implemented"
    assert result["pdf_parsing_connected"] is False


def test_paper_reader_with_text_returns_model_structure():
    """调用方提供正文时，才调用模型做结构化理解，并声明结果未经原文核对。"""
    port = FakeRuntime(replies=["1) 研究问题：…… 2) 方法：……"])
    result = invoke(registry_with(PaperReaderSkill()), "paper_reader", {"text": "正文内容" * 20}, port)
    assert result["status"] == "ok"
    assert result["structured"] is False
    assert "原文" in result["note"]
    assert port.calls_of("generate")