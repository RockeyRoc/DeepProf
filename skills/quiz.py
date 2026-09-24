"""Skill for issuing one reviewed, version-frozen course question."""

from __future__ import annotations

from typing import Any


class QuizSkill:
    name = "quiz"
    description = "从已审核且版本固定的题库中选择一题，不自动采纳 OCR 候选项"
    uses_host = True

    async def invoke(self, input: dict[str, Any], ctx: dict[str, Any], host: Any) -> dict[str, Any]:
        result = await host.call_tool("issue_quiz", {
            "concept_id": str(input.get("concept_id") or ""),
            "difficulty": input.get("difficulty"),
        }, ctx)
        if str(result.get("status") or "") != "ok":
            error = dict(result.get("error") or {})
            return {"status": "ok", "source": "tool:issue_quiz", "question": {
                "prompt": str(error.get("message") or "当前没有可用的已审核题目。"), "available": False,
            }, "result_data": {}}
        question = dict(result.get("question") or {})
        return {"status": "ok", "source": "tool:issue_quiz", "question": question,
                "result_data": dict(result.get("result_data") or question)}


__all__ = ["QuizSkill"]
