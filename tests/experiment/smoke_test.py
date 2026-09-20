"""Provider 容错冒烟实验（吸收自孙一新实验的 smoke_test.py 设计，改造为走 Runtime）。

与原版的差别：不裸调 DeepSeek SDK，而是通过 Runtime 的 Provider/端口验证——
失败必须被包装成结构化 ProviderError 且保留根因（__cause__），调用方才能
按类别决定降级策略（§7.3）。

五个检查项：
  1. 配置加载      —— .env 里的 key 非空且非占位符
  2. Runtime 连通  —— 真实 service.generate 端口跑一条最短消息，
                      断言非空回复且 usage 非零（顺带回归超时/usage 修复）
  3. 无效 key 容错 —— 注入伪造 key 的 Provider，断言 ProviderError 且根因是 401
  4. 超时容错      —— 注入 1ms 超时的 Provider，断言快速失败（<15s）且根因是超时
  5. 额度不足分类  —— 构造 HTTP 402 异常（不真实耗尽余额），断言分类器识别

运行方式：
    py tests/experiment/smoke_test.py

输出：tests/experiment/output/smoke_results.csv（控制台同步打印，密钥全程脱敏）。
"""
from __future__ import annotations

import asyncio
import csv
import sys
import time
from pathlib import Path
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[2]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import settings  # noqa: E402
from runtime.core.errors import ProviderError  # noqa: E402
from runtime.core.message import Message  # noqa: E402
from runtime.providers.base import ModelRequest  # noqa: E402
from runtime.service import build_runtime_service  # noqa: E402
from runtime.storage.sqlite_store import SqliteDatabase  # noqa: E402

from tests.experiment import common  # noqa: E402

OUT_DIR = Path(__file__).resolve().parent / "output"

# 超时容错用：1ms 读超时，几乎必然触发 APITimeoutError
TIMEOUT_TINY = 0.001


def _result(check: str, status: str, detail: str, latency_ms: float, category: str = "无") -> dict:
    return {
        "check": check,
        "status": status,
        "detail": detail,
        "latency_ms": round(latency_ms, 2),
        "error_category": category,
        "occurred_at": common.now_ts(),
    }


async def check_config() -> dict:
    ok = common.has_real_key()
    info = common.provider_info()
    return _result(
        "1-配置加载",
        "PASS" if ok else "FAIL",
        f"provider={info['provider']} model={info['model']} key={info['api_key_masked']}",
        0.0,
        "无" if ok else "密钥未配置",
    )


async def check_runtime_connectivity() -> dict:
    """真实链路连通：service.generate 端口 + usage 采集回归。"""
    if not common.has_real_key():
        return _result("2-Runtime连通", "SKIP", "无有效密钥，跳过", 0.0, "密钥未配置")
    from api.deps import configure_runtime

    service = build_runtime_service(db=SqliteDatabase(":memory:"))
    configure_runtime(service)
    ctx = {"session_id": "smoke_sess", "learner_id": "smoke_learner", "trace_id": "smoke_trace_conn"}
    request = {"messages": [Message.user("请用不超过五个字回复：你好")]}

    t0 = time.time()
    text_parts: list[str] = []
    usage: dict = {}
    error: dict | None = None
    try:
        async for chunk in service.generate(request, ctx):
            if chunk.get("type") == "delta":
                text_parts.append(str(chunk.get("text") or ""))
            elif chunk.get("type") == "done":
                usage = dict(chunk.get("usage") or {})
            elif chunk.get("type") == "error":
                error = dict(chunk.get("error") or {})
    except Exception as exc:  # noqa: BLE001 —— 冒烟要吞掉所有异常并归类
        return _result("2-Runtime连通", "FAIL", f"{type(exc).__name__}: {exc}",
                       (time.time() - t0) * 1000, common.classify_error(exc))
    ms = (time.time() - t0) * 1000
    text = "".join(text_parts).strip()
    if error:
        return _result("2-Runtime连通", "FAIL", f"结构化错误: {error}", ms,
                       str(error.get("code") or ""))
    ok = bool(text) and int(usage.get("total_tokens") or 0) > 0
    detail = f"回复={text[:30]!r}… tokens={usage.get('total_tokens')}"
    return _result("2-Runtime连通", "PASS" if ok else "FAIL", detail, ms)


async def _provider_failure_category(provider) -> tuple[str, float]:
    """调用注入的 Provider，返回 (错误分类, 耗时ms)；成功则返回 ("无", ms)。"""
    t0 = time.time()
    try:
        await provider.generate(ModelRequest(messages=[Message.user("hi")]))
    except ProviderError as exc:
        cause = exc.__cause__
        category = common.classify_error(cause) if cause is not None else "根因缺失"
        return category, (time.time() - t0) * 1000
    return "无", (time.time() - t0) * 1000


def _fault_provider(*, api_key: str | None = None, timeout: float) -> Any:
    """构造带故障注入的 Provider（工厂会固定传 settings 的 key，这里直接构造）。"""
    from runtime.providers.openai_compat import OpenAICompatProvider

    return OpenAICompatProvider(
        api_key=api_key if api_key is not None else settings.deepseek_api_key,
        base_url=settings.deepseek_base_url,
        model=settings.deepseek_model,
        name="deepseek",
        timeout=timeout,
    )


async def check_invalid_key() -> dict:
    """无效 key 必须变成结构化 ProviderError，且根因可归为 401。"""
    provider = _fault_provider(api_key="sk-invalid-invalid-invalid", timeout=10.0)
    category, ms = await _provider_failure_category(provider)
    ok = category == "无效密钥(401)"
    return _result(
        "3-无效key容错",
        "PASS" if ok else "FAIL",
        f"伪造密钥→ProviderError 根因归类为 {category!r}",
        ms,
        category,
    )


async def check_timeout() -> dict:
    """极短超时必须快速失败（而非挂起 600s），且根因可归为超时。

    回归点：真实实验曾因 SDK 默认 600s 读超时挂起 603 秒（见 realrun_report.md）。
    """
    provider = _fault_provider(timeout=TIMEOUT_TINY)
    category, ms = await _provider_failure_category(provider)
    ok = category == "超时" and ms < 15_000
    return _result(
        "4-超时容错",
        "PASS" if ok else "FAIL",
        f"1ms 超时→{ms:.0f}ms 快速失败，根因归类为 {category!r}",
        ms,
        category,
    )


def check_quota_classifier() -> dict:
    """构造 HTTP 402 异常验证分类器（真实额度耗尽不可复现，不做真实扣减）。"""
    import openai

    class _Req:
        method = "POST"
        url = "https://api.deepseek.com/chat/completions"
        headers: dict = {}

    class _Resp:
        request = _Req()
        status_code = 402
        headers: dict = {}

    class _Fake402(openai.APIStatusError):
        def __init__(self) -> None:
            super().__init__(
                message="Insufficient Balance",
                response=_Resp(),  # type: ignore[arg-type]
                body={"error": {"message": "Insufficient Balance"}},
            )
            self.status_code = 402

    category = common.classify_error(_Fake402())
    ok = category == "额度不足(402)"
    return _result(
        "5-额度不足分类",
        "PASS" if ok else "FAIL",
        f"HTTP 402 归类为 {category!r}（不做真实余额耗尽）",
        0.0,
        category,
    )


async def main() -> int:
    results = [
        await check_config(),
        await check_runtime_connectivity(),
        await check_invalid_key(),
        await check_timeout(),
        check_quota_classifier(),
    ]

    print("=" * 60)
    print("Provider 容错冒烟实验（走 Runtime 链路）")
    print("=" * 60)
    for r in results:
        flag = {"PASS": "[通过]", "FAIL": "[失败]", "SKIP": "[跳过]"}[r["status"]]
        print(f"{flag} {r['check']}: {r['detail']} (latency={r['latency_ms']}ms)")
    n_pass = sum(1 for r in results if r["status"] == "PASS")
    n_fail = sum(1 for r in results if r["status"] == "FAIL")
    n_skip = sum(1 for r in results if r["status"] == "SKIP")
    print("-" * 60)
    print(f"结论：{n_pass} 通过 / {n_fail} 失败 / {n_skip} 跳过 / 共 {len(results)} 项")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    out = OUT_DIR / "smoke_results.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(results[0]))
        writer.writeheader()
        writer.writerows(results)
    print(f"CSV 已写出：{out}")

    return 0 if n_fail == 0 else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
