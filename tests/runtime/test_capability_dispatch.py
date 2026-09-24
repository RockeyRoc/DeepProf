"""能力分发器契约：Runtime 只查表执行，失败必须显式（D-7 / §7.3）。"""

from __future__ import annotations

import pytest

from runtime.capabilities import (
    STATUS_CAPABILITY_NOT_FOUND,
    STATUS_ERROR,
    STATUS_INSUFFICIENT_EVIDENCE,
    STATUS_INVALID_REQUEST,
    STATUS_NO_BINDING,
    STATUS_SUCCESS,
    ActionDispatcher,
    render_block,
)
from runtime.testing import RecordingHost

EVIDENCE = [
    {"document_id": "doc1", "chunk_id": "c1", "page": 12, "source": "textbook.pdf", "text": "原文不应外泄"}
]


def make_dispatcher(bindings, host: RecordingHost | None = None):
    host = host or RecordingHost()
    return ActionDispatcher(host, bindings), host


# ---- 装配失败必须显式 ----

async def test_missing_binding_returns_no_binding():
    dispatcher, host = make_dispatcher({})
    result = await dispatcher.execute({"action": "teach"}, {})
    assert result["status"] == STATUS_NO_BINDING
    assert result["content"] == ""
    assert result["metadata"]["registered_actions"] == []
    assert host.calls == []


async def test_wrong_capability_name_returns_capability_not_found():
    dispatcher, host = make_dispatcher({"teach": {"capability": "does_not_exist"}})
    result = await dispatcher.execute({"action": "teach"}, {})
    assert result["status"] == STATUS_CAPABILITY_NOT_FOUND
    assert "render_template" in result["metadata"]["registered_capabilities"]
    assert host.calls == []


async def test_missing_placeholder_field_returns_invalid_request():
    dispatcher, _ = make_dispatcher(
        {"teach": {"capability": "render_template", "params": {"template": "${concept} ${ghost_field}"}}}
    )
    result = await dispatcher.execute({"action": "teach", "concept": "极限"}, {})
    assert result["status"] == STATUS_INVALID_REQUEST
    assert result["metadata"]["missing_fields"] == ["ghost_field"]
    assert result["error"]["code"] == "missing_fields"


async def test_unselectable_template_fails_before_main_capability():
    """选不到模板必须显式失败，不能产生半截回复。"""
    dispatcher, host = make_dispatcher(
        {
            "hint": {
                "capability": "generate_grounded",
                "prefix": {"templates": {"1": "轻提示"}, "select": "${level}"},
                "params": {"messages": []},
            }
        }
    )
    result = await dispatcher.execute({"action": "hint", "level": 9}, {})
    assert result["status"] == STATUS_ERROR
    assert result["error"]["code"] == "template_not_found"
    assert host.called("generate") == []


# ---- 证据前置 ----

async def test_require_evidence_skips_model_when_no_evidence():
    dispatcher, host = make_dispatcher(
        {
            "teach": {
                "capability": "generate_grounded",
                "require_evidence": True,
                "evidence": {"capability": "retrieve_evidence", "params": {"arguments": {"q": "x"}}},
                "insufficient_text": "教材中没有找到可定位的依据。",
                "params": {"messages": []},
            }
        }
    )
    result = await dispatcher.execute({"action": "teach", "require_evidence": True}, {})
    assert result["status"] == STATUS_INSUFFICIENT_EVIDENCE
    assert result["content"] == "教材中没有找到可定位的依据。"
    assert host.called("generate") == [], "证据不足时不得调用模型"


async def test_evidence_present_runs_main_capability():
    host = RecordingHost(evidence=EVIDENCE, model_text="讲解正文")
    dispatcher, _ = make_dispatcher(
        {
            "teach": {
                "capability": "generate_grounded",
                "require_evidence": True,
                "evidence": {"capability": "retrieve_evidence"},
                "insufficient_text": "无依据",
                "params": {"messages": []},
            }
        },
        host,
    )
    result = await dispatcher.execute({"action": "teach", "require_evidence": True}, {})
    assert result["status"] == STATUS_SUCCESS
    assert result["content"] == "讲解正文"
    assert host.called("generate") != []


async def test_evidence_result_is_locator_only_without_source_text():
    """跨回策略层的结果只保留可定位引用，不含教材原文。"""
    host = RecordingHost(evidence=EVIDENCE)
    dispatcher, _ = make_dispatcher(
        {"assess": {"capability": "retrieve_evidence", "params": {"tool": "retrieve_evidence"}}},
        host,
    )
    result = await dispatcher.execute({"action": "assess"}, {})
    assert result["evidence"] == [
        {"document_id": "doc1", "chunk_id": "c1", "page": 12, "source": "textbook.pdf"}
    ]
    assert "text" not in result["evidence"][0]


# ---- 同轮证据只检索一次 ----

async def test_evidence_memoized_within_same_trace():
    host = RecordingHost(evidence=EVIDENCE)
    dispatcher, _ = make_dispatcher(
        {
            "assess": {
                "capability": "retrieve_evidence",
                "memo": True,
                "params": {"tool": "retrieve_evidence", "arguments": {"q": "同一问题"}},
            }
        },
        host,
    )
    ctx = {"trace_id": "trc-1"}
    await dispatcher.execute({"action": "assess"}, ctx)
    await dispatcher.execute({"action": "assess"}, ctx)
    assert len(host.called("call_tool")) == 1, "同一轮内应复用证据检索结果"


async def test_evidence_memo_isolated_across_traces():
    host = RecordingHost(evidence=EVIDENCE)
    dispatcher, _ = make_dispatcher(
        {
            "assess": {
                "capability": "retrieve_evidence",
                "memo": True,
                "params": {"tool": "retrieve_evidence", "arguments": {"q": "q"}},
            }
        },
        host,
    )
    await dispatcher.execute({"action": "assess"}, {"trace_id": "trc-1"})
    await dispatcher.execute({"action": "assess"}, {"trace_id": "trc-2"})
    assert len(host.called("call_tool")) == 2, "跨轮必须重新检索"


async def test_evidence_not_memoized_without_trace_id():
    host = RecordingHost(evidence=EVIDENCE)
    dispatcher, _ = make_dispatcher(
        {
            "assess": {
                "capability": "retrieve_evidence",
                "memo": True,
                "params": {"tool": "retrieve_evidence", "arguments": {"q": "q"}},
            }
        },
        host,
    )
    await dispatcher.execute({"action": "assess"}, {})
    await dispatcher.execute({"action": "assess"}, {})
    assert len(host.called("call_tool")) == 2, "无 trace_id 时无法判断是否同一轮，不应备忘"


# ---- 原语行为 ----

async def test_render_template_never_calls_model():
    """提示强度是实验自变量，必须确定性渲染。"""
    dispatcher, host = make_dispatcher(
        {"hint": {"capability": "render_template", "params": {"template": "第 ${level} 级提示：${concept}"}}}
    )
    result = await dispatcher.execute({"action": "hint", "level": 2, "concept": "导数"}, {})
    assert result["content"] == "第 2 级提示：导数"
    assert host.called("generate") == []


async def test_invoke_skill_passes_skill_name_and_input():
    host = RecordingHost(skill_result={"status": "ok", "content": "苏格拉底式追问", "records": [{"k": 1}]})
    dispatcher, _ = make_dispatcher(
        {
            "ask": {
                "capability": "invoke_skill",
                "params": {"skill": "socratic", "content_field": "content", "input": {"concept": "${concept}"}},
            }
        },
        host,
    )
    result = await dispatcher.execute({"action": "ask", "concept": "极限"}, {})
    assert result["content"] == "苏格拉底式追问"
    assert result["records"] == [{"k": 1}]
    assert result["metadata"]["skill_status"] == "ok"
    assert host.called("invoke_skill")[0]["input"]["concept"] == "极限"


async def test_invoke_skill_without_content_field_only_asks_state():
    """不声明 content_field = 只问状态：Skill 如实被调用即算成功，正文为空。

    学情模型未接入时 Skill 回 not_implemented，图侧据此退化为规则化观察，
    而不是把“没接入”当成执行失败。
    """
    host = RecordingHost(skill_result={"status": "not_implemented", "note": "模型未接入"})
    dispatcher, _ = make_dispatcher(
        {"diagnose": {"capability": "invoke_skill", "params": {"skill": "diagnosis"}}}, host
    )
    result = await dispatcher.execute({"action": "diagnose"}, {})
    assert result["status"] == STATUS_SUCCESS
    assert result["content"] == ""
    assert result["metadata"]["skill_status"] == "not_implemented"


async def test_invoke_skill_content_field_selects_by_value():
    """content_field 可按取值选字段路径（出题读 item.stem，评价读 evaluation.feedback）。"""
    host = RecordingHost(
        skill_result={
            "status": "ok",
            "item": {"stem": "请说明更新方向"},
            "evaluation": {"feedback": "先核对定义"},
        }
    )
    dispatcher, _ = make_dispatcher(
        {
            "test": {
                "capability": "invoke_skill",
                "params": {
                    "skill": "quiz",
                    "content_field": {
                        "select": "${params.mode}",
                        "templates": {"generate": "item.stem", "evaluate": "evaluation.feedback"},
                    },
                    "passthrough": ["item_bank_connected"],
                },
            }
        },
        host,
    )
    generated = await dispatcher.execute({"action": "test", "params": {"mode": "generate"}}, {})
    assert generated["content"] == "请说明更新方向"
    evaluated = await dispatcher.execute({"action": "test", "params": {"mode": "evaluate"}}, {})
    assert evaluated["content"] == "先核对定义"


async def test_invoke_skill_declared_content_but_empty_degrades_with_skill_status():
    """声明了 content_field 却取不到正文 → 走绑定的兜底文案，并保留 Skill 状态供审计。"""
    host = RecordingHost(skill_result={"status": "error"})
    dispatcher, _ = make_dispatcher(
        {
            "ask": {
                "capability": "invoke_skill",
                "params": {"skill": "socratic", "content_field": "question"},
                "fallback": {"template": "我们先从一个更小的问题开始：你怎么定义「${concept}」？"},
            }
        },
        host,
    )
    result = await dispatcher.execute({"action": "ask", "concept": "极限"}, {})
    assert result["status"] == STATUS_SUCCESS
    assert "更小的问题" in result["content"]
    assert result["metadata"]["degraded_from"] == "invoke_skill"
    assert result["metadata"]["skill_status"] == "error"


async def test_skill_not_found_surfaces_as_structured_error():
    host = RecordingHost(fail_skills={"quiz"})
    dispatcher, _ = make_dispatcher(
        {"test": {"capability": "invoke_skill", "params": {"skill": "quiz"}}}, host
    )
    result = await dispatcher.execute({"action": "test"}, {})
    assert result["status"] == STATUS_ERROR
    assert result["error"]["code"] == "capability_failed"


async def test_fallback_is_deterministic_and_marks_degradation():
    host = RecordingHost(fail_skills={"quiz"})
    dispatcher, _ = make_dispatcher(
        {
            "test": {
                "capability": "invoke_skill",
                "params": {"skill": "quiz"},
                "fallback": {"template": "本轮测验暂不可用，请稍后重试。"},
            }
        },
        host,
    )
    result = await dispatcher.execute({"action": "test"}, {})
    assert result["status"] == STATUS_SUCCESS
    assert result["content"] == "本轮测验暂不可用，请稍后重试。"
    assert result["metadata"]["degraded_from"] == "invoke_skill"


async def test_generate_error_is_wrapped_and_degraded_by_fallback():
    class FailingHost(RecordingHost):
        async def generate(self, request, ctx):
            yield {"type": "error", "error": {"message": "boom", "details": {"kind": "upstream_error"}}}
            if False:  # pragma: no cover
                yield {}

    dispatcher, _ = make_dispatcher(
        {
            "teach": {
                "capability": "generate_grounded",
                "params": {"messages": []},
                "fallback": {"template": "模型暂时不可用。"},
            }
        },
        FailingHost(),
    )
    result = await dispatcher.execute({"action": "teach"}, {})
    assert result["status"] == STATUS_SUCCESS
    assert result["content"] == "模型暂时不可用。"


async def test_degraded_fallback_drops_evidence():
    """模型失败降级成兜底文案时**不附引用**（§7.3）。

    检索确实命中过，但这一轮没有依据可以生成；把片段挂在一句“生成失败”旁边，
    会让学生以为这段讲解是有出处的。
    """

    class FailingHost(RecordingHost):
        async def generate(self, request, ctx):
            yield {"type": "error", "error": {"message": "boom", "details": {"kind": "upstream_error"}}}
            if False:  # pragma: no cover
                yield {}

    dispatcher, _ = make_dispatcher(
        {
            "teach": {
                "capability": "generate_grounded",
                "require_evidence": True,
                "evidence": {"capability": "retrieve_evidence"},
                "insufficient_text": "无依据",
                "params": {"messages": []},
                "fallback": {"template": "模型暂时不可用。"},
            }
        },
        FailingHost(evidence=EVIDENCE),
    )
    result = await dispatcher.execute({"action": "teach", "require_evidence": True}, {})

    assert result["content"] == "模型暂时不可用。"
    assert result["evidence"] == [], "降级兜底时不得把检索结果当作引用回传"
    assert result["metadata"]["evidence_count"] == 1, "命中数仍如实记录（供审计）"
    assert result["metadata"]["evidence_attached"] is False


async def test_successful_generation_marks_evidence_attached():
    host = RecordingHost(evidence=EVIDENCE, model_text="讲解正文")
    dispatcher, _ = make_dispatcher(
        {
            "teach": {
                "capability": "generate_grounded",
                "require_evidence": True,
                "evidence": {"capability": "retrieve_evidence"},
                "insufficient_text": "无依据",
                "params": {"messages": []},
            }
        },
        host,
    )
    result = await dispatcher.execute({"action": "teach", "require_evidence": True}, {})
    assert result["metadata"]["evidence_attached"] is True
    assert len(result["evidence"]) == 1


async def test_generate_grounded_puts_evidence_text_into_prompt_only():
    """生成用证据**原文**（来自能力内部），但回传策略层的只有可定位引用（§6.4）。"""
    host = RecordingHost(evidence=EVIDENCE, model_text="讲解正文")
    dispatcher, _ = make_dispatcher(
        {
            "teach": {
                "capability": "generate_grounded",
                "require_evidence": True,
                "evidence": {"capability": "retrieve_evidence"},
                "insufficient_text": "无依据",
                "params": {
                    "system_prompt": "只依据教材片段作答。",
                    "prompt_template": "知识点：{concept}\n教材片段：\n{evidence_block}",
                    "values": {"concept": "${concept}"},
                    "max_chars": 20,
                },
            }
        },
        host,
    )
    result = await dispatcher.execute({"action": "teach", "concept": "极限", "require_evidence": True}, {})

    assert result["content"] == "讲解正文"
    request = host.called("generate")[0]
    user_message = request["messages"][-1]["content"]
    assert request["messages"][0]["role"] == "system"
    assert "极限" in user_message
    assert "原文不应外泄" in user_message
    assert "[片段1]" in user_message
    # 回传策略层的结果不得带原文
    assert result["evidence"] == [
        {"document_id": "doc1", "chunk_id": "c1", "page": 12, "source": "textbook.pdf"}
    ]


async def test_retrieve_evidence_via_skill_normalizes_and_reports_status():
    """检索走绑定声明的 Skill（可做规范化/过滤），原始命中供生成、引用只留定位。"""
    host = RecordingHost(
        skill_result={
            "status": "ok",
            "skill": "rag",
            "evidence": [{"document_id": "d", "chunk_id": "c", "page": 3, "text": "片段原文", "source": "教材"}],
        }
    )
    dispatcher, _ = make_dispatcher(
        {"assess": {"capability": "retrieve_evidence", "params": {"skill": "rag", "input": {"query": "x"}}}},
        host,
    )
    result = await dispatcher.execute({"action": "assess"}, {})
    assert result["metadata"]["retrieval_status"] == "ok"
    assert result["evidence"] == [{"document_id": "d", "chunk_id": "c", "page": 3, "source": "教材"}]
    assert host.called("invoke_skill")[0]["name"] == "rag"


async def test_retrieve_evidence_via_skill_treats_non_ok_as_insufficient():
    host = RecordingHost(
        skill_result={"status": "insufficient_evidence", "evidence": [], "note": "无命中"}
    )
    dispatcher, _ = make_dispatcher(
        {"assess": {"capability": "retrieve_evidence", "params": {"skill": "rag"}}}, host
    )
    result = await dispatcher.execute({"action": "assess"}, {})
    assert result["evidence"] == []
    assert result["metadata"]["retrieval_status"] == "insufficient_evidence"


# ---- 前缀/后缀与绑定状态 ----

async def test_prefix_and_suffix_wrap_content():
    dispatcher, _ = make_dispatcher(
        {
            "teach": {
                "capability": "render_template",
                "prefix": {"template": "【讲解】"},
                "suffix": {"template": "（来源：教材）"},
                "params": {"template": "正文"},
            }
        }
    )
    result = await dispatcher.execute({"action": "teach"}, {})
    assert result["content"] == "【讲解】正文（来源：教材）"


async def test_select_picks_template_by_value():
    dispatcher, _ = make_dispatcher(
        {
            "test": {
                "capability": "render_template",
                "prefix": {
                    "templates": {"correct": "答对了。", "wrong": "再想想。", "unknown": "已记录。"},
                    "select": "${grading}",
                },
                "params": {"template": "解析"},
            }
        }
    )
    assert (await dispatcher.execute({"action": "test", "grading": "correct"}, {}))["content"] == "答对了。解析"
    assert (await dispatcher.execute({"action": "test", "grading": "wrong"}, {}))["content"] == "再想想。解析"


def test_binding_summary_is_secret_and_content_free():
    dispatcher = ActionDispatcher(RecordingHost(), {"teach": {"capability": "generate_grounded"}})
    assert dispatcher.binding_summary() == {"teach": "generate_grounded"}


def test_render_block_preserves_type_for_whole_placeholder():
    assert render_block({"template": "${count}"}, {"count": 3}) == "3"
    assert render_block({"template": "共 ${count} 条"}, {"count": 3}) == "共 3 条"


@pytest.mark.parametrize("block", [None, ""])
def test_render_block_empty(block):
    assert render_block(block, {}) == ""
