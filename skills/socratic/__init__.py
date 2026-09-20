"""Socratic Skill：苏格拉底式递进提问（DESIGNv0.4 §6.3）。

职责：生成"下一个能推进思考的问题"，引导学生自己得出结论。
硬性要求：**避免过早泄露答案**——提示词里明确禁止给出答案、结论与推导结果；
严禁把最终解法写进问题。

诚实边界：当前的"不泄露答案"只由提示词约束（prompt_only），
没有输出侧校验器；需要在教师评审（§16.7）中抽检复核，
后续可加规则/模型二次校验。

责任人：孙一新（§16.3 教学策略与测验闭环）。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimeContext, RuntimePort
from runtime.skills import Skill, SkillDescriptor

from .. import collect_stream, to_ctx_dict

#: 只输出一个问题的系统提示（约束模型不要"顺手把答案说了"）
_SYSTEM_PROMPT = (
    "你是苏格拉底式提问者。只输出一个中文问题（必要时加一句不超过 20 字的铺垫），"
    "不得给出答案、结论、公式推导结果或最终解法，也不得直接指出学生错在哪里。"
)


class SocraticSkill(Skill):
    """把学生当前的说法转成一个递进追问。"""

    descriptor = SkillDescriptor(
        name="socratic",
        description="按递进提问引导学生自己得出结论，避免过早泄露答案",
        when_to_use="学生具备推理基础、需要用追问推进时（图节点 Ask）",
        version="0.1.0",
        owner="孙一新",
        tags=["pedagogy", "questioning", "socratic"],
    )

    async def handle(
        self, payload: dict, ctx: RuntimeContext, port: RuntimePort
    ) -> dict[str, Any]:
        """入参：concept / learner_input / learning_goal / hint_level / prior_attempts。

        出参：{"status", "question", "cognitive_goal", ...}；
        未取得内容时返回 status="error"，由调用方（Ask 节点）退回策略模板。
        """
        concept = str(payload.get("concept") or "当前知识点")
        learner_input = str(payload.get("learner_input") or "").strip()
        learning_goal = str(payload.get("learning_goal") or "（未填写）")
        hint_level = int(payload.get("hint_level") or 0)
        prior_attempts = int(payload.get("prior_attempts") or 0)
        avoid_answer = bool(payload.get("avoid_answer", True))
        # 学情记忆摘要（图侧 Assess 压缩产出，非空才拼接）：只作个性化参考，
        # 不是教材依据——追问本身不需要引用，因此这里不做证据校验。
        memory_note = str(payload.get("memory_note") or "").strip()

        prompt = (
            f"知识点：{concept}\n"
            f"学习目标：{learning_goal}\n"
            f"学生目前的说法：{learner_input or '（学生尚未给出推理）'}\n"
            f"已给出的提示级别：{hint_level}（级别越高越具体）\n"
            f"学生已尝试次数：{prior_attempts}\n"
        )
        if memory_note:
            prompt += f"{memory_note}\n"
        prompt += (
            f"约束：必须避免过早泄露答案（avoid_answer={avoid_answer}）。\n"
            "请提出下一个问题，让学生自己前进一步。"
        )
        request = {
            "messages": [
                {"role": "system", "content": _SYSTEM_PROMPT},
                {"role": "user", "content": prompt},
            ],
            "temperature": 0.5,
            "stream": True,
            "metadata": {"skill": self.name, "concept": concept, "avoid_answer": avoid_answer},
        }
        question = await collect_stream(port, request, to_ctx_dict(ctx))
        if not question:
            return {
                "status": "error",
                "skill": self.name,
                "question": "",
                "note": "模型未返回内容，调用方应退回策略模板提问",
                "error": {"code": "model_empty", "message": "generate 未返回文本"},
            }
        return {
            "status": "ok",
            "skill": self.name,
            "question": question,
            "cognitive_goal": f"让学生澄清「{concept}」的条件与适用范围",
            "avoid_answer": avoid_answer,
            "answer_guard": "prompt_only（仅提示词约束，未做输出侧校验，需教师抽检）",
            "source": "model_generated",
        }


__all__ = ["SocraticSkill"]