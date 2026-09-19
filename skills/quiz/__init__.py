"""Quiz Skill：按知识点/难度/题型出题与评价（DESIGNv0.4 §6.3 / §18.1）。

**诚实边界（重要）**：课程题库与题目难度标定**尚未接入**
（题库、知识点标注与来源映射由许阳毅按 §16.2 交接；
Attempt 与判分链路由欧阳文凯按 §16.6 负责）。
因此本 Skill：
- 出题走 port.generate 的模型即时生成，返回 item_bank_connected=False，
  并在 note 里写明题目不可用于正式测评/成绩；
- 评价只给"学习性反馈"，**不返回确定性对错**（correct=None），
  避免把无标定的模型判断当成学情结论（§13.1 不给学生贴永久标签）。

待题库与 Attempt 链路接入后，这里应改为检索真实题目 + 结构化判分，
并输出 §18.2 的 Attempt 字段（attempt_id / item_id / concept_ids / correct / hint_count）。

责任人：孙一新（§16.3 教学策略与测验闭环）。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimeContext, RuntimePort
from runtime.skills import Skill, SkillDescriptor

from .. import collect_stream, to_ctx_dict

_GENERATE_SYSTEM_PROMPT = (
    "你是高校课程的出题助手。只输出一道中文简答题与必要的说明，"
    "不要给出答案或解题过程，题目必须能在 3 分钟内作答。"
)
_EVALUATE_SYSTEM_PROMPT = (
    "你是高校课程的答疑老师。对学生的作答只给出学习性反馈："
    "指出思路上的关键一步是否缺失、下一步该检查什么；"
    "不要给出最终答案，也不要给出确定的对错判分结论。"
)

_NOT_CONNECTED_NOTE = (
    "课程题库与自动判分尚未接入（许阳毅负责题库与来源映射，欧阳文凯负责 Attempt 与判分）；"
    "当前题目由模型即时生成，判分结论不写入学情。"
)


class QuizSkill(Skill):
    """出题与评价 Skill（题库未接入，仅提供即时生成与学习性反馈）。"""

    descriptor = SkillDescriptor(
        name="quiz",
        description="按知识点/难度/题型生成自检题并给出作答反馈（题库与自动判分未接入）",
        when_to_use="需要验证理解或间隔复习时（图节点 Test）",
        version="0.1.0",
        owner="孙一新",
        tags=["assessment", "quiz", "formative"],
    )

    async def handle(
        self, payload: dict, ctx: RuntimeContext, port: RuntimePort
    ) -> dict[str, Any]:
        """入参 mode=generate|evaluate；出参包含 item_bank_connected=False 与 note。"""
        mode = str(payload.get("mode") or "generate")
        if mode == "evaluate":
            return await self._evaluate(payload, ctx, port)
        return await self._generate(payload, ctx, port)

    # ---------- 出题 ----------
    async def _generate(
        self, payload: dict, ctx: RuntimeContext, port: RuntimePort
    ) -> dict[str, Any]:
        concept = str(payload.get("concept") or "当前知识点")
        difficulty = str(payload.get("difficulty") or "basic")
        item_type = str(payload.get("item_type") or "short_answer")
        prompt = (
            f"知识点：{concept}\n难度：{difficulty}\n题型：{item_type}\n"
            "请输出一道用于自我检验的题目。"
        )
        stem = await collect_stream(
            port,
            {
                "messages": [
                    {"role": "system", "content": _GENERATE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.4,
                "stream": True,
                "metadata": {"skill": self.name, "mode": "generate", "concept": concept},
            },
            to_ctx_dict(ctx),
        )
        if not stem:
            return {
                "status": "error",
                "skill": self.name,
                "item_bank_connected": False,
                "note": _NOT_CONNECTED_NOTE,
                "error": {"code": "model_empty", "message": "generate 未返回题目文本"},
            }
        return {
            "status": "ok",
            "skill": self.name,
            "item": {
                "item_id": "",  # 无题库 = 无可追踪 item_id（§18.2 Attempt 契约待补）
                "stem": stem,
                "concept": concept,
                "difficulty": difficulty,
                "item_type": item_type,
                "structured": False,  # 模型自由文本，未按题目 schema 解析
            },
            "item_bank_connected": False,
            "note": _NOT_CONNECTED_NOTE,
            "source": "model_generated",
        }

    # ---------- 评价 ----------
    async def _evaluate(
        self, payload: dict, ctx: RuntimeContext, port: RuntimePort
    ) -> dict[str, Any]:
        concept = str(payload.get("concept") or "当前知识点")
        student_answer = str(payload.get("student_answer") or "").strip()
        if not student_answer:
            return {
                "status": "error",
                "skill": self.name,
                "item_bank_connected": False,
                "note": "缺少学生作答，无法评价",
                "error": {"code": "missing_answer", "message": "student_answer 为空"},
            }
        prompt = (
            f"知识点：{concept}\n学生的作答：{student_answer}\n"
            "请指出思路上的关键一步是否缺失，并提示下一步该检查什么。"
        )
        feedback = await collect_stream(
            port,
            {
                "messages": [
                    {"role": "system", "content": _EVALUATE_SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                "temperature": 0.3,
                "stream": True,
                "metadata": {"skill": self.name, "mode": "evaluate", "concept": concept},
            },
            to_ctx_dict(ctx),
        )
        if not feedback:
            return {
                "status": "error",
                "skill": self.name,
                "item_bank_connected": False,
                "note": _NOT_CONNECTED_NOTE,
                "error": {"code": "model_empty", "message": "generate 未返回反馈文本"},
            }
        return {
            "status": "ok",
            "skill": self.name,
            "evaluation": {
                "correct": None,  # 明确不给确定性对错：判分链路未接入
                "auto_grading_connected": False,
                "feedback": feedback,
            },
            "item_bank_connected": False,
            "note": _NOT_CONNECTED_NOTE,
            "source": "model_generated",
        }


__all__ = ["QuizSkill"]