"""Quiz Skill：按知识点/难度/题型出题与评价（DESIGNv0.6 §6.3 / §18.1）。

**诚实边界（重要）**：课程题库与题目难度标定**尚未接入**
（题库、知识点标注与来源映射由许阳毅按 §20 交接；
自动判分链路由欧阳文凯按 §16.6 负责）。
因此本 Skill：

- 出题走模型即时生成，返回 ``item_bank_connected=False``，
  并在 note 里写明题目不可用于正式测评/成绩；
- 评价只给“学习性反馈”，**不返回确定性对错**（``correct=None``），
  避免把无标定的模型判断当成学情结论（§13.1 不给学生贴永久标签）。

Attempt（§18.2）的责任分工：**契约**由数据组定义（models/learner/attempt.py），
**产出与发送**由教育组在 Test / Correct 节点完成（§16.3）——
本 Skill 只在题库接入后负责给出可追踪的 item_id 与结构化判分。

责任人：孙一新（§16.3 教学策略与测验闭环）。
"""

from __future__ import annotations

from typing import Any

from runtime.skills import collect_stream

__all__ = ["QuizSkill"]

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
    "课程题库与自动判分尚未接入（许阳毅负责题库与来源映射，欧阳文凯负责判分链路）；"
    "当前题目由模型即时生成，判分结论不写入学情。"
)


class QuizSkill:
    """出题与评价 Skill（题库未接入，仅提供即时生成与学习性反馈）。"""

    name = "quiz"
    description = "按知识点/难度/题型生成自检题并给出作答反馈（题库与自动判分未接入）"
    uses_host = True

    async def invoke(self, input: dict[str, Any], ctx: dict[str, Any], host: Any) -> dict[str, Any]:
        """入参 mode=generate|evaluate；出参含 ``item_bank_connected`` 与 note。"""
        if str(input.get("mode") or "generate") == "evaluate":
            return await self._evaluate(input, ctx, host)
        return await self._generate(input, ctx, host)

    # ---------- 出题 ----------
    async def _generate(self, input: dict[str, Any], ctx: dict[str, Any], host: Any) -> dict[str, Any]:
        concept = str(input.get("concept") or "当前知识点")
        difficulty = str(input.get("difficulty") or "basic")
        item_type = str(input.get("item_type") or "short_answer")
        stem = await collect_stream(
            host,
            {
                "messages": [
                    {"role": "system", "content": _GENERATE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"知识点：{concept}\n难度：{difficulty}\n题型：{item_type}\n"
                            "请输出一道用于自我检验的题目。"
                        ),
                    },
                ],
                "temperature": 0.4,
            },
            ctx,
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
    async def _evaluate(self, input: dict[str, Any], ctx: dict[str, Any], host: Any) -> dict[str, Any]:
        concept = str(input.get("concept") or "当前知识点")
        student_answer = str(input.get("student_answer") or "").strip()
        if not student_answer:
            return {
                "status": "error",
                "skill": self.name,
                "item_bank_connected": False,
                "note": "缺少学生作答，无法评价",
                "error": {"code": "missing_answer", "message": "student_answer 为空"},
            }
        feedback = await collect_stream(
            host,
            {
                "messages": [
                    {"role": "system", "content": _EVALUATE_SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"知识点：{concept}\n学生的作答：{student_answer}\n"
                            "请指出思路上的关键一步是否缺失，并提示下一步该检查什么。"
                        ),
                    },
                ],
                "temperature": 0.3,
            },
            ctx,
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