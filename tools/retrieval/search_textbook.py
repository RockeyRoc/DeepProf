"""教材检索工具（DESIGNv0.4 §5.3 / §16.2 / §12"不编造引用"）。

**当前状态：向量库与课程语料尚未接入。**
§16.2 规定由许阳毅在本轮完成 PDF→章节/页码→切块→向量→检索，
并保留 document_id / page / chunk_id；在此之前 Runtime 里没有任何可查询的索引。
因此本工具**不返回任何 document_id / page / 原文片段**，
只诚实地返回 ``insufficient_evidence``，由教学图转入澄清或保守回答（§7.3）。

缺什么、谁来做：
- 缺课程语料与索引：``data/course_manifest`` + 向量库（§16.2 / §16.6 交接）；
- 责任人：许阳毅（RAG 与课程语料），索引就绪后再把真实检索结果填进 ``evidence``；
- 在那之前，任何"看起来像引用"的输出都属于编造，必须禁止（§12）。

权限：只读检索 → 仅声明 ``fs.read``；不申请写、删除、网络与密钥权限（默认最小权限，§13.3）。
"""
from __future__ import annotations

from pydantic import BaseModel, Field

from runtime.sandbox.policy import PERM_FS_READ
from runtime.tools.base import FunctionTool, ToolContext, ToolResult

__all__ = ["SearchTextbookInput", "build_search_textbook_tool", "search_textbook"]

#: 工具名（教学图与模型按此名调用）
TOOL_NAME = "search_textbook"

#: 检索未就绪时的状态标识（教学图据此走"证据不足"分支，§7.3）
STATUS_INSUFFICIENT = "insufficient_evidence"


class SearchTextbookInput(BaseModel):
    """检索参数（schema 校验由 ToolRegistry 统一执行，§5.3）。"""

    query: str = Field(min_length=1, max_length=400, description="学生问题中的检索关键词或问句")
    course_id: str = Field(default="", description="课程标识；试点课程由教育组确定（D-2）")
    concept_ids: list[str] = Field(default_factory=list, description="期望命中的知识点，用于过滤")
    top_k: int = Field(default=5, ge=1, le=20, description="期望返回的证据条数上限")


async def search_textbook(arguments: SearchTextbookInput, ctx: ToolContext) -> ToolResult:
    """检索教材证据。

    Returns:
        ``ok=True`` 且 ``data={"evidence": [], "status": "insufficient_evidence", ...}``。

        这里刻意用"成功但无证据"而非错误：没有证据是事实，不是工具故障；
        教学图据 ``status`` 决定澄清或保守回答，而 ``ok=False`` 会走到"工具失败"分支
        （重试/换工具），语义并不相同（§7.3）。
    """
    return ToolResult.success(
        content=(
            "[证据不足] 教材检索尚未接入：向量库与课程语料未就绪（§16.2，责任人：许阳毅）。"
            f"本次未找到任何可引用的教材片段（query={arguments.query!r}）。"
            "请勿凭记忆编造 document_id / page，应改为澄清提问或说明证据不足。"
        ),
        status=STATUS_INSUFFICIENT,
        evidence=[],  # 永不伪造引用：没有索引就没有证据（§12）
        query=arguments.query,
        course_id=arguments.course_id,
        concept_ids=arguments.concept_ids,
        top_k=arguments.top_k,
        missing=["course_corpus", "vector_index"],  # 缺什么，写清楚
        owner="许阳毅",  # 谁来做
    )


def build_search_textbook_tool() -> FunctionTool:
    """构造注册用的 Tool 实例（权限为默认最小集合：只读）。"""
    return FunctionTool(
        name=TOOL_NAME,
        description=(
            "检索课程教材中的可定位证据片段（返回 document_id / chunk_id / page）。"
            "当前索引未接入，只会返回 insufficient_evidence；"
            "没有证据时必须说明证据不足，不得编造来源。"
        ),
        input_model=SearchTextbookInput,
        handler=search_textbook,
        permissions=frozenset({PERM_FS_READ}),
        requires_approval=False,  # 只读检索无需审批；联网检索另需 PERM_NETWORK 并会被 Sandbox 默认拒绝
    )