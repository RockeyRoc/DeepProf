"""PaperReader Skill：论文结构化理解与讨论（DESIGNv0.6 §6.3）。

**诚实边界**：
- PDF 解析与论文向量检索链路尚未接入（`library/`，§20.5），
  因此本 Skill **不会**自行读取文件、URL 或数据库；
- 只有调用方把论文正文文本通过 ``input.text`` 传进来时，才调用模型做结构化理解；
  未提供正文时返回 not_implemented，并说明缺什么、由谁补。

输出边界：结构化结果由模型生成（``structure_text``），
本 Skill 不假装做过 schema 解析（structured=False），也不声称内容已核对原文。

责任人：许阳毅（§20 资源库与课程/论文语料）。
"""

from __future__ import annotations

from typing import Any

from runtime.skills import collect_stream

__all__ = ["PaperReaderSkill"]

#: 论文正文送模型前的截断长度（避免超长与不必要的正文复制）
_MAX_TEXT_CHARS = 6000

_SYSTEM_PROMPT = (
    "你是面向高校学生的论文阅读助手。用中文回答，"
    "只依据给定的论文正文，不得补充正文之外的信息或引用；"
    "正文未提及的内容必须明确写「正文未提及」。"
)


class PaperReaderSkill:
    """论文结构化理解 Skill（无正文时返回 not_implemented）。"""

    name = "paper_reader"
    description = "对论文正文做结构化理解（研究问题/方法/结论/局限）并支持讨论"
    uses_host = True

    async def invoke(self, input: dict[str, Any], ctx: dict[str, Any], host: Any) -> dict[str, Any]:
        """入参：text（论文正文，必需）/ question（可选讨论问题）。"""
        text = str(input.get("text") or "").strip()
        if not text:
            return {
                "status": "not_implemented",
                "skill": self.name,
                "note": (
                    "缺少论文正文：PDF 解析、论文切块与检索链路尚未接入"
                    "（许阳毅负责，§20）；本 Skill 不读取文件或网络，"
                    "请由调用方传入 input.text 后再使用"
                ),
                "pdf_parsing_connected": False,
                "retrieval_connected": False,
            }

        question = str(input.get("question") or "请结构化总结这篇论文").strip()
        structure_text = await collect_stream(
            host,
            {
                "messages": [
                    {"role": "system", "content": _SYSTEM_PROMPT},
                    {
                        "role": "user",
                        "content": (
                            f"论文正文（已截断到 {_MAX_TEXT_CHARS} 字）：\n"
                            f"{text[:_MAX_TEXT_CHARS]}\n\n"
                            f"学生的需求：{question}\n\n"
                            "请按四部分输出：1) 研究问题；2) 方法；3) 主要结论；4) 局限与未回答问题。"
                            "正文未提及的部分写「正文未提及」。"
                        ),
                    },
                ],
                "temperature": 0.3,
            },
            ctx,
        )
        if not structure_text:
            return {
                "status": "error",
                "skill": self.name,
                "note": "模型未返回内容",
                "error": {"code": "model_empty", "message": "generate 未返回文本"},
            }
        return {
            "status": "ok",
            "skill": self.name,
            "structure_text": structure_text,
            "structured": False,  # 模型自由文本，未做 schema 解析
            "source": "model_generated_from_provided_text",
            "note": "结果基于调用方提供的正文生成，未经原文逐句核对，请以原文为准",
        }