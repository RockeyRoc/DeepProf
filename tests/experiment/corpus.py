"""实验装置：本地教材小语料与检索工具（教育组真实实验专用）。

为什么需要它：`tools/retrieval/search_textbook.py` 是诚实的占位实现——
课程语料与向量索引尚未接入（§16.2，责任人：许阳毅），永远返回
insufficient_evidence。没有证据链路，"Teach 带可定位引用"这条验收路径
在真实实验里永远走不到。

实验约定（与 §12"不编造引用"一致）：
- 语料是**真实可查的**微型教材片段（不是模型生成），检索是**真实的关键词匹配**；
- 工具仍按 Evidence 契约返回 document_id / chunk_id / page，缺定位标识的命中丢弃；
- 本装置只存在于 tests/experiment/，通过 unregister + register **显式覆盖**
  生产占位工具，并在实验报告里声明"教材检索为实验装置，语料待 RAG 组接入"；
- 概念不在语料中时返回 insufficient_evidence（用于"无证据不编造引用"场景）。
"""
from __future__ import annotations

from typing import Any

from runtime.sandbox.policy import PERM_FS_READ
from runtime.tools.base import FunctionTool, ToolContext, ToolResult
from tools.retrieval.search_textbook import SearchTextbookInput, TOOL_NAME

# ======================================================================
# 微型教材语料：真实教材内容片段（高数·导数与梯度），每条带可定位标识
# ======================================================================
CORPUS: list[dict[str, Any]] = [
    {
        "document_id": "gksx",  # 《高等数学（第七版）》上册
        "chunk_id": "c2-1",
        "page": 81,
        "source": "高等数学·第二章 导数与微分",
        "text": (
            "导数的定义：设函数 y=f(x) 在点 x0 的某邻域内有定义，"
            "若自变量增量 Δx→0 时，极限 lim(Δy/Δx) 存在，则称 f(x) 在 x0 处可导，"
            "该极限值称为 f(x) 在 x0 处的导数，记作 f'(x0)。"
        ),
        "keywords": ("导数", "定义", "可导", "极限"),
    },
    {
        "document_id": "gksx",
        "chunk_id": "c2-2",
        "page": 86,
        "source": "高等数学·第二章 导数与微分",
        "text": (
            "导数的几何意义：f'(x0) 是曲线 y=f(x) 在点 (x0, f(x0)) 处切线的斜率，"
            "切线方程为 y−f(x0)=f'(x0)(x−x0)。"
        ),
        "keywords": ("导数", "几何意义", "切线", "斜率"),
    },
    {
        "document_id": "gksx",
        "chunk_id": "c2-3",
        "page": 92,
        "source": "高等数学·第二章 导数与微分",
        "text": (
            "函数的和、差、积、商的求导法则：若 u、v 可导，"
            "则 (uv)'=u'v+uv'，(u/v)'=(u'v−uv')/v²（v≠0）。"
        ),
        "keywords": ("求导", "法则", "乘积", "商", "求导法则"),
    },
    {
        "document_id": "mlxx",  # 《机器学习》周志华
        "chunk_id": "c1-6",
        "page": 22,
        "source": "机器学习·第1章 绪论（梯度相关背景）",
        "text": (
            "梯度下降是一种迭代优化方法：沿负梯度方向更新参数以减小损失函数，"
            "步长由学习率控制；学习率过大可能振荡或发散，过小则收敛缓慢。"
        ),
        "keywords": ("梯度下降", "梯度", "学习率", "负梯度", "优化", "损失"),
    },
    {
        "document_id": "mlxx",
        "chunk_id": "c3-2",
        "page": 54,
        "source": "机器学习·第3章 线性模型",
        "text": (
            "对线性回归以均方误差为损失时，参数更新可采用梯度下降："
            "w ← w − η·∂E/∂w，其中 η 为学习率，∂E/∂w 为损失对参数的偏导数。"
        ),
        "keywords": ("梯度下降", "偏导", "更新", "参数", "损失函数"),
    },
]


def search_corpus(query: str, top_k: int = 4) -> list[dict[str, Any]]:
    """关键词检索：命中的片段按命中关键词数排序，保留契约必需的定位标识。"""
    query = str(query or "")
    scored: list[tuple[int, dict[str, Any]]] = []
    for item in CORPUS:
        hits = sum(1 for kw in item["keywords"] if kw in query)
        if hits > 0:
            scored.append((hits, item))
    scored.sort(key=lambda pair: pair[0], reverse=True)
    return [
        {
            "document_id": item["document_id"],
            "chunk_id": item["chunk_id"],
            "page": item["page"],
            "text": item["text"],
            "source": item["source"],
        }
        for _, item in scored[: max(1, top_k)]
    ]


async def _experiment_search(arguments: SearchTextbookInput, ctx: ToolContext) -> ToolResult:
    """实验版检索 handler：真实关键词匹配，无命中诚实返回 insufficient_evidence。"""
    hits = search_corpus(arguments.query, arguments.top_k)
    if not hits:
        return ToolResult.success(
            content=(
                "[证据不足] 实验语料中没有命中相关片段"
                f"（query={arguments.query!r}）。不编造任何来源。"
            ),
            status="insufficient_evidence",
            evidence=[],
            query=arguments.query,
            missing=["corpus_match"],
        )
    return ToolResult.success(
        content=f"[实验检索] 命中 {len(hits)} 个可定位片段。",
        status="ok",
        evidence=hits,
        query=arguments.query,
    )


def build_experiment_search_tool() -> FunctionTool:
    """构造实验装置版 search_textbook（与生产工具同名，实验时装入）。"""
    return FunctionTool(
        name=TOOL_NAME,
        description=(
            "检索课程教材中的可定位证据片段（实验装置：本地微型语料 + 关键词检索）。"
            "无命中时返回 insufficient_evidence，不编造来源。"
        ),
        input_model=SearchTextbookInput,
        handler=_experiment_search,
        permissions=frozenset({PERM_FS_READ}),
        requires_approval=False,
    )
