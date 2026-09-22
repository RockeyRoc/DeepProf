"""Pi RPC 客户端：严格 LF 分隔的 JSONL 帧，按 id 关联请求与响应。

仅用于开发者模式嵌入实验；与 Learner Session 严格隔离，不得写入学习记忆。
Pi 不内置权限系统，因此本适配器只在受控开发环境使用（§11.4）。
"""

from __future__ import annotations

import asyncio
import json
from typing import Any, AsyncIterator, Awaitable, Callable

DEFAULT_COMMAND = ("pi", "--mode", "rpc")


class PiRpcError(RuntimeError):
    def __init__(self, message: str, *, payload: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.payload = dict(payload or {})


def encode_frame(payload: dict[str, Any]) -> bytes:
    """编码为一行 JSONL；分隔符严格使用 LF，且不含内嵌换行。"""
    line = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    if "\n" in line:  # pragma: no cover - json.dumps 默认不产生裸换行
        raise PiRpcError("frame 含内嵌换行，违反 LF 分隔约定")
    return (line + "\n").encode("utf-8")


def decode_frame(line: bytes | str) -> dict[str, Any] | None:
    """解码一行；空行返回 None，非法 JSON 抛 PiRpcError。"""
    text = line.decode("utf-8") if isinstance(line, bytes) else line
    text = text.rstrip("\r\n")
    if not text.strip():
        return None
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise PiRpcError(f"非法 JSONL 帧: {text[:120]}") from exc
    if not isinstance(payload, dict):
        raise PiRpcError("帧必须是 JSON 对象")
    return payload


class PiRpcClient:
    """基于读写流的 RPC 客户端（读写流可注入，便于测试）。"""

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: Any,
        *,
        request_timeout: float = 60.0,
    ) -> None:
        self._reader = reader
        self._writer = writer
        self._timeout = request_timeout
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._counter = 0
        self._notifications: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._pump: asyncio.Task[None] | None = None

    def start(self) -> None:
        if self._pump is None:
            self._pump = asyncio.create_task(self._read_loop())

    async def close(self) -> None:
        if self._pump is not None:
            self._pump.cancel()
            try:
                await self._pump
            except (asyncio.CancelledError, Exception):  # noqa: BLE001
                pass
            self._pump = None
        close = getattr(self._writer, "close", None)
        if callable(close):
            close()

    def _next_id(self) -> str:
        self._counter += 1
        return f"pi-{self._counter}"

    async def request(self, method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.start()
        request_id = self._next_id()
        future: asyncio.Future[dict[str, Any]] = asyncio.get_running_loop().create_future()
        self._pending[request_id] = future
        self._writer.write(encode_frame({"id": request_id, "method": method, "params": params or {}}))
        drain = getattr(self._writer, "drain", None)
        if callable(drain):
            await drain()
        try:
            return await asyncio.wait_for(future, timeout=self._timeout)
        except asyncio.TimeoutError as exc:
            raise PiRpcError(f"Pi RPC 超时: {method}") from exc
        finally:
            self._pending.pop(request_id, None)

    async def notifications(self) -> AsyncIterator[dict[str, Any]]:
        self.start()
        while True:
            yield await self._notifications.get()

    async def _read_loop(self) -> None:
        while True:
            line = await self._reader.readline()
            if not line:
                self._fail_pending("Pi 进程已关闭连接")
                return
            try:
                frame = decode_frame(line)
            except PiRpcError:
                continue
            if frame is None:
                continue
            request_id = frame.get("id")
            if request_id and request_id in self._pending:
                future = self._pending[request_id]
                if not future.done():
                    if frame.get("error"):
                        future.set_exception(PiRpcError("Pi 返回错误", payload=frame))
                    else:
                        future.set_result(frame)
            else:
                self._notifications.put_nowait(frame)

    def _fail_pending(self, message: str) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(PiRpcError(message))


async def spawn_pi(
    command: tuple[str, ...] = DEFAULT_COMMAND,
    *,
    cwd: str | None = None,
) -> tuple[asyncio.subprocess.Process, PiRpcClient]:
    """启动 ``pi --mode rpc`` 并返回进程与客户端。

    ``windowsHide`` 避免弹出控制台窗口（与项目其它子进程一致）。
    """
    process = await asyncio.create_subprocess_exec(
        *command,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        creationflags=_windows_hide_flag(),
    )
    assert process.stdout is not None and process.stdin is not None
    return process, PiRpcClient(process.stdout, process.stdin)


def _windows_hide_flag() -> int:
    import subprocess
    import sys

    if sys.platform != "win32":  # pragma: no cover - 平台相关
        return 0
    return getattr(subprocess, "CREATE_NO_WINDOW", 0)


def session_isolation_guard(*, writes_memory: bool) -> None:
    """开发者模式不得写入 Learner Memory（§19.7）。"""
    if writes_memory:
        raise PiRpcError("Pi 开发者模式禁止写入 Learner Memory")


__all__ = [
    "DEFAULT_COMMAND",
    "PiRpcClient",
    "PiRpcError",
    "decode_frame",
    "encode_frame",
    "session_isolation_guard",
    "spawn_pi",
]


# 供上层按需注入的响应回调类型
ResponseHook = Callable[[dict[str, Any]], Awaitable[None]]