"""检索类工具（DESIGNv0.4 §16.2 归属许阳毅：教材解析与检索）。

当前包含 ``search_textbook``：只声明契约与权限，索引接入前诚实返回证据不足。
"""
from .search_textbook import (
    STATUS_INSUFFICIENT,
    TOOL_NAME,
    SearchTextbookInput,
    build_search_textbook_tool,
    search_textbook,
)

__all__ = [
    "TOOL_NAME",
    "STATUS_INSUFFICIENT",
    "SearchTextbookInput",
    "build_search_textbook_tool",
    "search_textbook",
]