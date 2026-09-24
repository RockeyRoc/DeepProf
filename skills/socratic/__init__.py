"""Socratic Skill：苏格拉底式递进提问（DESIGNv0.6 §6.3）。

职责：生成“下一个能推进思考的问题”，引导学生自己得出结论。
硬性要求：**避免过早泄露答案**——提示词里明确禁止给出答案、结论与推导结果；
严禁把最终解法写进问题。

模型输出会在内部缓冲后经过格式与明显答案泄露规则检查；未通过时丢弃，
由绑定表提供确定性追问兜底。校验不做语义证明，教学组仍需抽查。

责任人：孙一新（§16.3 教学策略与测验闭环）。
"""

from __future__ import annotations

import re
from typing import Any

from runtime.skills import collect_stream

__all__ = ["SocraticSkill"]

_ANSWER_LEAK_PATTERNS = (
    re.compile(r"(?:答案|结论|最终解法|正确结果|计算结果)\s*(?:是|为|：|:)?"),
    re.compile(r"(?:因此|由此可得|所以可得)\s*[^？?]{0,48}"),
    re.compile(r"(?:公式|结果)\s*(?:是|为|=|：|:)"),
)


def validate_socratic_question(text: str, *, max_chars: int = 180) -> str:
    """Accept one short question only; reject answers, prose, and malformed output."""
    question = " ".join(str(text or "").strip().split())
    if not question or len(question) > max_chars:
        return ""
    if question.count("？") + question.count("?") != 1:
        return ""
    if question[-1] not in "？?":
        return ""
    if any(pattern.search(question) for pattern in _ANSWER_LEAK_PATTERNS):
        return ""
    if any(mark in question[:-1] for mark in "。！!；;"):
        return ""
    return question

#: 只输出一个问题的系统提示（约束模型不要“顺手把答案说了”）
_SYSTEM_PROMPT = (
    "你是苏格拉底式提问者。只输出一个中文问题（必要时加一句不超过 20 字的铺垫），"
    "不得给出答案、结论、公式推导结果或最终解法，也不得直接指出学生错在哪里。"
)


class SocraticSkill:
    """把学生当前的说法转成一个递进追问。"""

    name = "socratic"
    description = "按递进提问引导学生自己得出结论，避免过早泄露答案"
    uses_host = True

    async def invoke(self, input: dict[str, Any], ctx: dict[str, Any], host: Any) -> dict[str, Any]:
        """入参：concept / learner_input / learning_goal / hint_level / prior_attempts。

        出参：``{"status", "question", ...}``；未取得内容时返回 ``status="error"``，
        由调用方（绑定声明的兜底模板）改用一个朴素但不泄露答案的问题。
        """
        concept = str(input.get("concept") or "当前知识点")
        learner_input = str(input.get("learner_input") or "").strip()
        learning_goal = str(input.get("learning_goal") or "（未填写）")
        hint_level = int(input.get("hint_level") or 0)
        prior_attempts = int(input.get("prior_attempts") or 0)
        avoid_answer = bool(input.get("avoid_answer", True))

        prompt = (
            f"知识点：{concept}\n"
            f"学习目标：{learning_goal}\n"
            f"学生目前的说法：{learner_input or '（学生尚未给出推理）'}\n"
            f"已给出的提示级别：{hint_level}（级别越高越具体）\n"
            f"学生已尝试次数：{prior_attempts}\n"
        )
        prompt += (
            f"约束：必须避免过早泄露答案（avoid_answer={avoid_answer}）。\n"
            "请提出下一个问题，让学生自己前进一步。"
        )
        question = await collect_stream(
            host,
            {
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.2,
            },
            {**ctx, "suppress_user_stream": True},
        )
        question = validate_socratic_question(question)
        if not question:
            return {
                "status": "error",
                "skill": self.name,
                "question": "",
                "note": "模型未返回内容，调用方应改用兜底提问模板",
                "error": {"code": "model_empty", "message": "generate 未返回文本"},
            }
        return {
            "status": "ok",
            "skill": self.name,
            "question": question,
            "cognitive_goal": f"让学生澄清「{concept}」的条件与适用范围",
            "avoid_answer": avoid_answer,
            "answer_guard": "rule_checked_buffered_output",
            "source": "model_generated",
        }
