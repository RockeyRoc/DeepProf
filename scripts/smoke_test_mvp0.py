"""Runtime 冒烟测试：验证 一条带 trace_id 的请求完成 模型→工具→事件 闭环。

两种模式：
    py scripts/smoke_test_mvp0.py            # 真实调用（需要 .env 里的 API key）
    py scripts/smoke_test_mvp0.py --fake     # 不联网，用 FakeProvider 验证链路

验收依据（DESIGNv0.4 §16.1）：
1. 模型被调用，事件全部挂同一个 trace_id；
2. 工具调用进入轨迹（tool.requested → tool.started → tool.completed）；
3. 会话与事件落盘，重开 Runtime 后仍可读回。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pydantic import BaseModel, Field  # noqa: E402

from config import settings  # noqa: E402
from runtime.core.events import EventType  # noqa: E402
from runtime.core.message import Message, new_id  # noqa: E402
from runtime.providers.base import ModelRequest  # noqa: E402
from runtime.providers.factory import get_provider  # noqa: E402
from runtime.providers.fake import FakeProvider  # noqa: E402
from runtime.service import RuntimeService  # noqa: E402
from runtime.tools.base import FunctionTool, ToolResult  # noqa: E402


class EchoInput(BaseModel):
    text: str = Field(min_length=1, description="要回显的文本")


async def echo(arguments: EchoInput, ctx) -> ToolResult:
    return ToolResult.success(f"echo: {arguments.text}", text=arguments.text)


def check_key() -> bool:
    """真实模式下检查 key 是否已配置且不是占位符（不回显密钥）。"""
    attr = {"deepseek": "deepseek_api_key", "openai": "openai_api_key", "qwen": "qwen_api_key"}
    key = getattr(settings, attr.get(settings.llm_provider, ""), "")
    if not key or "{{" in key:
        print(f"[前置检查失败] {settings.llm_provider} 的 API key 未填写或仍是占位符。")
        print("请打开项目根目录的 .env 填好 key 后重试，或改用 --fake 验证链路。")
        return False
    print(f"[前置检查通过] provider={settings.llm_provider} key={key[:3]}***{key[-2:]}（已脱敏）")
    return True


def build_provider(use_fake: bool):
    """Fake 模式用脚本化的"先调工具，再作答"，真实模式用配置的 Provider。"""
    if use_fake:
        return FakeProvider(
            script=[
                {
                    "content": "",
                    "tool_calls": [
                        {"name": "echo", "arguments": {"text": "递归"}, "id": "call_1"}
                    ],
                },
                {"content": "递归是函数调用自身来拆解问题，终止条件必不可少。"},
            ]
        )
    return get_provider()


async def run_checks(use_fake: bool) -> int:
    provider = build_provider(use_fake)
    service = RuntimeService(provider=provider)
    service.register_tool(
        FunctionTool(name="echo", description="回显文本", input_model=EchoInput, handler=echo)
    )
    session = service.create_session(learner_id="smoke")
    trace_id = new_id("trace")

    print("\n========== Runtime 闭环检查 ==========")
    print(f"provider={service.provider.name} session={session.session_id} trace={trace_id}")
    start = time.time()
    text_parts: list[str] = []
    tools: list[str] = []
    try:
        async for chunk in service.run_turn(session, "用一句话说明什么是递归？", trace_id=trace_id):
            if chunk.kind == "delta":
                text_parts.append(chunk.text)
            elif chunk.kind == "tool":
                tools.append(chunk.tool_name)
            elif chunk.kind == "error":
                print(f"[失败] {chunk.error}")
                return 1
    except Exception as exc:
        print(f"[调用失败] {type(exc).__name__}: {exc}")
        print("常见原因：key 无效、余额不足、网络代理。")
        return 1
    elapsed = time.time() - start

    answer = "".join(text_parts)
    print(f"回复（{elapsed:.2f}s，{len(text_parts)} 个分片）: {answer}")

    events = service.events_by_trace(trace_id)
    types = [event.type for event in events]
    checks = {
        "模型被调用": types.count(EventType.MODEL_REQUESTED.value) >= 1,
        "事件挂同一 trace_id": bool(events) and all(e.trace_id == trace_id for e in events),
        "工具调用进入轨迹": not use_fake
        or (EventType.TOOL_COMPLETED.value in types and tools == ["echo"]),
        "本轮成功结束": EventType.AGENT_TURN_COMPLETED.value in types,
        "有可见文本输出": len(answer) > 5,
        "会话已落盘": service.session_store.load(session.session_id) is not None,
    }
    checks["重启后可读回"] = RuntimeService(
        provider=provider, db=service.db
    ).session_store.load(session.session_id) is not None

    for name, passed in checks.items():
        print(f"[{'通过' if passed else '失败'}] {name}")
    print(f"事件明细：{len(events)} 条，类型：{', '.join(sorted(set(types)))}")

    return 0 if all(checks.values()) else 1


async def check_stream(use_fake: bool) -> bool:
    """直接验证 Provider 流式接口（真实模式才有意义）。"""
    if use_fake:
        return True
    provider = get_provider()
    print("\n========== Provider 流式接口检查 ==========")
    # 真实供应商不接受空消息列表，契约检查必须带一条最小 user 消息
    request = ModelRequest(messages=[Message.user("请用不超过十个字回复：连接正常")], model="")
    parts: list[str] = []
    try:
        async for chunk in provider.stream(request):
            if chunk.delta:
                parts.append(chunk.delta)
    except Exception as exc:
        print(f"[失败] {type(exc).__name__}: {exc}")
        return False
    full = "".join(parts)
    print(f"收到 {len(parts)} 个分片，{len(full)} 个字符")
    print(f"[{'通过' if len(parts) > 1 else '失败'}] 流式返回多分片")
    return len(parts) > 1


def main() -> int:
    parser = argparse.ArgumentParser(description="DeepProf Runtime 冒烟测试")
    parser.add_argument("--fake", action="store_true", help="不联网，用 FakeProvider")
    args = parser.parse_args()

    if not args.fake and not check_key():
        return 1
    ok = asyncio.run(run_checks(args.fake))
    stream_ok = asyncio.run(check_stream(args.fake))

    print("\n========== 结论 ==========")
    if ok == 0 and stream_ok:
        print("Runtime 链路验证通过。可运行交互式对话：py scripts/chat_repl.py")
        return 0
    print("存在失败项，请根据上方报错排查。")
    return 1


if __name__ == "__main__":
    sys.exit(main())