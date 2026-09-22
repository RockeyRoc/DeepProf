"""结构化错误与外部失败分类。

分类必须**优先看 HTTP 状态码**，再看响应体关键字；
仅靠响应体子串匹配会产生误分类（例如 403 地区限制被元数据里的 "401" 带偏）。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# 错误种类（写入 ProviderError.details["kind"]）
KIND_AUTH_FAILED = "auth_failed"
KIND_REGION_OR_PERMISSION_BLOCKED = "region_or_permission_blocked"
KIND_RATE_LIMITED = "rate_limited"
KIND_TIMEOUT = "timeout"
KIND_CONNECTION_FAILED = "connection_failed"
KIND_UPSTREAM_ERROR = "upstream_error"
KIND_NOT_FOUND = "not_found"
KIND_MISSING_CREDENTIAL = "missing_credential"
KIND_CAPABILITY_MISSING = "capability_missing"
KIND_MODEL_TRUNCATED = "model_truncated"
KIND_UNKNOWN = "unknown"

RETRYABLE_KINDS = {KIND_RATE_LIMITED, KIND_TIMEOUT, KIND_CONNECTION_FAILED, KIND_UPSTREAM_ERROR}


class RuntimeFailure(Exception):
    """Runtime 内部可分类失败的基类。"""

    code = "runtime_error"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details: dict[str, Any] = dict(details or {})

    def to_dict(self) -> dict[str, Any]:
        return {"code": self.code, "message": self.message, "details": dict(self.details)}


class ProviderError(RuntimeFailure):
    """Provider 层的结构化错误。"""

    code = "provider_error"

    def __init__(
        self,
        message: str,
        *,
        kind: str = KIND_UNKNOWN,
        status_code: int | None = None,
        details: dict[str, Any] | None = None,
    ) -> None:
        merged = dict(details or {})
        merged["kind"] = kind
        if status_code is not None:
            merged["http_status"] = status_code
        merged.setdefault("retryable", kind in RETRYABLE_KINDS)
        super().__init__(message, details=merged)
        self.kind = kind

    @property
    def retryable(self) -> bool:
        return bool(self.details.get("retryable"))


@dataclass(slots=True)
class FailureClassification:
    kind: str
    retryable: bool
    http_status: int | None = None
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "retryable": self.retryable,
            "http_status": self.http_status,
            "detail": self.detail,
        }


_BODY_HINTS: tuple[tuple[str, tuple[str, ...]], ...] = (
    (KIND_MISSING_CREDENTIAL, ("missing api key", "no api key", "api key is required", "未配置密钥")),
    (KIND_AUTH_FAILED, ("invalid api key", "incorrect api key", "authentication failed", "unauthorized")),
    (KIND_REGION_OR_PERMISSION_BLOCKED, ("region", "not available in your country", "permission denied", "forbidden")),
    (KIND_RATE_LIMITED, ("rate limit", "too many requests", "quota")),
    (KIND_NOT_FOUND, ("model not found", "does not exist", "no such model")),
    (KIND_CAPABILITY_MISSING, ("does not support", "unsupported", "not supported")),
    (KIND_TIMEOUT, ("timed out", "timeout")),
)


def classify_external_failure(
    *,
    status_code: int | None = None,
    body: str | None = None,
    error: BaseException | None = None,
) -> FailureClassification:
    """把外部失败归一到结构化分类。HTTP 状态码优先于响应体关键字。"""
    if status_code is not None:
        if status_code == 401:
            return FailureClassification(KIND_AUTH_FAILED, False, status_code, "http 401")
        if status_code == 402:
            return FailureClassification(KIND_RATE_LIMITED, False, status_code, "http 402")
        if status_code == 403:
            return FailureClassification(KIND_REGION_OR_PERMISSION_BLOCKED, False, status_code, "http 403")
        if status_code == 404:
            return FailureClassification(KIND_NOT_FOUND, False, status_code, "http 404")
        if status_code == 408:
            return FailureClassification(KIND_TIMEOUT, True, status_code, "http 408")
        if status_code == 429:
            return FailureClassification(KIND_RATE_LIMITED, True, status_code, "http 429")
        if 500 <= status_code <= 599:
            return FailureClassification(KIND_UPSTREAM_ERROR, True, status_code, f"http {status_code}")
        if 400 <= status_code <= 499:
            kind = _match_body(body) or KIND_UNKNOWN
            return FailureClassification(kind, kind in RETRYABLE_KINDS, status_code, f"http {status_code}")

    if error is not None:
        name = type(error).__name__.lower()
        text = f"{name}: {error}".lower()
        if "timeout" in text:
            return FailureClassification(KIND_TIMEOUT, True, status_code, name)
        if isinstance(error, (ConnectionError, OSError)) or "connect" in text:
            return FailureClassification(KIND_CONNECTION_FAILED, True, status_code, name)

    kind = _match_body(body)
    if kind:
        return FailureClassification(kind, kind in RETRYABLE_KINDS, status_code, "body match")
    return FailureClassification(KIND_UNKNOWN, False, status_code, "")


def _match_body(body: str | None) -> str | None:
    if not body:
        return None
    lowered = body.lower()
    for kind, hints in _BODY_HINTS:
        if any(hint in lowered for hint in hints):
            return kind
    return None