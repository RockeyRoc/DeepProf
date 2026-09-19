"""DeeepProf 终端 REPL（走 RuntimeService，DESIGNv0.4 §4.4）。

链路：终端输入 → Agent（模型→工具循环）→ 事件落盘 → 终端流式输出。

用法（项目根目录）：
    py scripts/chat_repl.py           # 用 .env 配置的 Provider（默认 deepseek）
    py scripts/chat_repl.py --fake    # 不联网，用 FakeProvider 验证 Runtime 链路

需要先在 .env 填入对应 API key（参考 .env.example）。
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

# 让脚本能从项目根目录导入包（scripts/ 不是 Python 包）
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from runtime.core.errors import ProviderError  # noqa: E402
from runtime.providers.factory import get_provider  # noqa: E402
from runtime.service import RuntimeService  # noqa: E402


async def repl(use_fake: bool) -> None:
    try:
        service = RuntimeService(provider=get_provider("fake" if use_fake else ""))
    except ProviderError as exc:
        print(f"[启动失败] {exc}")
        print("请在项目根目录的 .env 里填入 API key，或先用 --fake 验证链路。")
        return

    session = service.create_session(learner_id="local")
    print(f"DeeepProf 已就绪（provider={service.provider.name}，session={session.session_id}）。")
    print("输入 quit 退出，clear 重开会话。")
    print("-" * 50)

    while True:
        try:
            user = (await asyncio.to_thread(input, "你 > ")).strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break

        if not user:
            continue
        if user.lower() in ("quit", "exit"):
            break
        if user.lower() == "clear":
            service.close_session(session)
            session = service.create_session(learner_id="local")
            print(f"（已重开会话 {session.session_id}）")
            continue

        print("DeepProf > ", end="", flush=True)
        try:
            async for chunk in service.run_turn(session, user):
                if chunk.kind == "delta":
                    print(chunk.text, end="", flush=True)
                elif chunk.kind == "tool":
                    mark = "成功" if chunk.tool_ok else "失败"
                    print(f"\n[工具 {chunk.tool_name} {mark}] ", end="", flush=True)
                elif chunk.kind == "error":
                    print(f"\n[失败] {chunk.error}")
        except ProviderError as exc:
            print(f"\n[调用失败] {exc}")
        print()

    service.close_session(session)
    print("会话已结束（轨迹已落盘，可用 service.replay 复盘）。")


def main() -> None:
    parser = argparse.ArgumentParser(description="DeepProf 终端 REPL")
    parser.add_argument("--fake", action="store_true", help="使用 FakeProvider，不联网")
    args = parser.parse_args()
    asyncio.run(repl(args.fake))


if __name__ == "__main__":
    main()