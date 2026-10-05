"""Provider-native web search capability and response-source normalization.

No independent search provider or search-service credential is used here.
Only integrations documented by the selected model provider are enabled.
"""
from __future__ import annotations

import os
import httpx
from typing import Any
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

BAILIAN_SEARCH_DOC = "https://help.aliyun.com/zh/model-studio/web-search"
ZHIPU_MCP_DOC = "https://docs.z.ai/guides/capabilities/mcp-call"
OLLAMA_SEARCH_DOC = "https://docs.ollama.com/capabilities/web-search"
OLLAMA_SEARCH_SECRET_REF = "web-search:ollama"
OLLAMA_SEARCH_URL = "https://ollama.com/api/web_search"
TAVILY_SEARCH_SECRET_REF = "web-search:tavily"
TAVILY_SEARCH_URL = "https://api.tavily.com/search"
TAVILY_EXTRACT_URL = "https://api.tavily.com/extract"
ARK_SEARCH_DOC = "https://docs.volcengine.com/docs/ark/online-content-plugin-guide?lang=zh"


class WebSearchError(RuntimeError):
    def __init__(self, code: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.code = code
        self.retryable = retryable


def normalize_source_url(value: str) -> str:
    """Canonicalize a public HTTP(S) source URL for stable de-duplication."""
    try:
        parsed = urlsplit(str(value).strip())
        scheme = parsed.scheme.casefold()
        host = (parsed.hostname or "").casefold().rstrip(".")
        if scheme not in {"http", "https"} or not host or parsed.username or parsed.password:
            return ""
        port = parsed.port
        authority = f"[{host}]" if ":" in host else host
        if port and not (scheme == "http" and port == 80 or scheme == "https" and port == 443):
            authority += f":{port}"
        path = parsed.path or "/"
        if path != "/":
            path = path.rstrip("/") or "/"
        query = [(key, item) for key, item in parse_qsl(parsed.query, keep_blank_values=True)
                 if not key.casefold().startswith("utm_") and key.casefold() not in {"gclid", "fbclid"}]
        query.sort()
        return urlunsplit((scheme, authority, path, urlencode(query, doseq=True), ""))
    except (TypeError, ValueError):
        return ""


def ollama_search_api_key(secret_store: Any) -> str:
    """Read the DPAPI-backed key first, with Ollama's documented env var fallback."""
    return str(secret_store.get(OLLAMA_SEARCH_SECRET_REF) or os.environ.get("OLLAMA_API_KEY") or "")


def native_search_capability(vendor_id: str, model: str, *, api_mode: str = "auto") -> dict[str, Any]:
    """Return only API integrations supported by official provider contracts."""
    vendor = (vendor_id or "").casefold()
    name = (model or "").casefold()
    unsupported = {"supported": False, "kind": "unsupported", "official_docs": ""}

    if vendor == "qwen":
        # Alibaba documents GLM-5.2 and Kimi-K3 web search only through its
        # OpenAI-compatible Responses API. These are model-specific exceptions
        # to the Chat Completions enable_search integration below.
        if "glm-5.2" in name or "kimi-k3" in name:
            if api_mode == "chat_completions":
                return {**unsupported, "vendor": vendor, "model": model,
                        "reason": "此模型的百炼原生联网仅支持 Responses API，请将接口模式设为自动或 Responses。"}
            return {"supported": True, "kind": "bailian_responses_web_search", "vendor": vendor,
                    "api_mode": "responses", "official_docs": BAILIAN_SEARCH_DOC,
                    "always_search": False, "search_confirmation": "response_web_search_call"}
        # The provider documents these Chat Completions families. Omni models
        # need a distinct multimodal endpoint, so don't send chat-completion
        # search flags to them from the text-only adapter.
        supported_family = any(tag in name for tag in (
            "qwen-plus", "qwen-max", "qwen-flash", "qwen-turbo", "qwen3-max",
            "qwen3.5", "qwen3.6", "qwen3.7", "qwen3.8", "qwq-plus", "deepseek-v4",
        ))
        if supported_family and "omni" not in name:
            return {"supported": True, "kind": "qwen_enable_search", "vendor": vendor,
                    "official_docs": BAILIAN_SEARCH_DOC, "always_search": True,
                    "search_confirmation": "provider_metadata"}
    elif vendor == "volcengine":
        # Ark's Responses API built-in web_search tool is documented for the
        # current Seed 2.1 Pro model. Do not infer support for every Ark model.
        if "doubao-seed-2-1-pro-260628" in name:
            if api_mode == "chat_completions":
                return {**unsupported, "vendor": vendor, "model": model,
                        "reason": "此方舟模型的原生联网使用 Responses API，请将接口模式设为自动或 Responses。"}
            return {"supported": True, "kind": "ark_responses_web_search", "vendor": vendor,
                    "api_mode": "responses", "official_docs": ARK_SEARCH_DOC,
                    "always_search": False, "search_confirmation": "response_web_search_call"}
    elif vendor == "glm":
        # Z.AI exposes its own webSearchPrime MCP server through chat/completions.
        # This uses the configured GLM API credential and never falls back to an
        # unrelated search service.
        return {"supported": True, "kind": "zhipu_web_search_prime", "vendor": vendor,
                "official_docs": ZHIPU_MCP_DOC, "always_search": False,
                "search_confirmation": "provider_response"}
    elif vendor == "ollama":
        # Ollama exposes an official web-search REST API. It requires a separate
        # Ollama account key even when the selected inference model is local.
        return {"supported": True, "kind": "ollama_web_search", "vendor": vendor,
                "official_docs": OLLAMA_SEARCH_DOC, "always_search": True,
                "requires_api_key": True, "search_confirmation": "ollama_search_api"}

    return {**unsupported, "vendor": vendor, "model": model,
            "reason": "当前模型或接口没有已确认的原生联网搜索 API。"}


def native_search_request(capability: dict[str, Any], mode: str) -> dict[str, Any]:
    """Build provider-native API fields; callers never synthesize search results."""
    if not capability.get("supported"):
        raise WebSearchError("native_search_unsupported", "当前模型没有已确认的原生联网搜索 API。")
    if mode == "always" and not capability.get("always_search"):
        raise WebSearchError("native_search_force_unsupported", "当前模型 API 不能强制执行联网搜索。")
    kind = str(capability.get("kind") or "")
    if kind == "qwen_enable_search":
        result: dict[str, Any] = {"enable_search": True}
        if mode == "always":
            result["search_options"] = {"forced_search": True}
        return {"body": result, "tools": [], "tool_choice": None}
    if kind in {"bailian_responses_web_search", "ark_responses_web_search"}:
        return {"api_mode": "responses", "body": {}, "tools": [{"type": "web_search"}],
                "tool_choice": None}
    if kind == "zhipu_web_search_prime":
        return {
            "body": {},
            "tools": [{"type": "mcp", "mcp": {
                "server_label": "mcp code",
                "transport_type": "streamable-http",
                "allowed_tools": ["webSearchPrime"],
            }}],
            "tool_choice": "auto",
            "instruction": "For questions about current facts or when the user asks for web research, use the provider's webSearchPrime tool before answering. Do not claim a search happened unless the provider returns search results.",
        }
    if kind == "ollama_web_search":
        # Ollama search runs before the final model call; the selected model gets
        # the real source snippets as untrusted context, not fabricated tools.
        return {"body": {}, "tools": [], "tool_choice": None}
    raise WebSearchError("native_search_unsupported", "当前模型联网适配器不可用。")


async def ollama_web_search(query: str, api_key: str, *, transport: httpx.AsyncBaseTransport | None = None,
                            client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    if not api_key.strip():
        raise WebSearchError("provider_credential_missing", "请配置 Ollama Web Search API Key。")
    owned = client is None
    shared = client or httpx.AsyncClient(timeout=10.0, transport=transport)
    try:
        response = await shared.post(OLLAMA_SEARCH_URL, timeout=10.0,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"},
            json={"query": query[:1000], "max_results": 5})
    except httpx.TimeoutException as exc:
        raise WebSearchError("search_timeout", "Ollama 联网搜索超时。", retryable=True) from exc
    except httpx.RequestError as exc:
        raise WebSearchError("search_connection_failed", "无法连接 Ollama 联网搜索 API。", retryable=True) from exc
    finally:
        if owned:
            await shared.aclose()
    if response.status_code in {401, 403}:
        raise WebSearchError("search_authentication_failed", "Ollama Web Search API Key 无效或未获授权。")
    if response.status_code == 402:
        raise WebSearchError("search_quota_exceeded", "Ollama 联网搜索额度不足。")
    if response.status_code == 429:
        raise WebSearchError("search_rate_limited", "Ollama 联网搜索请求过于频繁。", retryable=True)
    if response.status_code >= 500:
        raise WebSearchError("search_service_error", f"Ollama 联网搜索服务返回 HTTP {response.status_code}。", retryable=True)
    if response.status_code >= 400:
        raise WebSearchError("search_request_failed", f"Ollama 联网搜索请求失败（HTTP {response.status_code}）。")
    try:
        data = response.json()
    except ValueError as exc:
        raise WebSearchError("search_invalid_response", "Ollama 联网搜索返回了无效响应。") from exc
    results = data.get("results") if isinstance(data, dict) else None
    return extract_native_sources(results if isinstance(results, list) else [], vendor="ollama")


async def tavily_search(query: str, api_key: str, *, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    if not api_key.strip():
        raise WebSearchError("provider_credential_missing", "请配置 Tavily API Key。")
    owned = client is None
    shared = client or httpx.AsyncClient(timeout=20.0)
    try:
        response = await shared.post(TAVILY_SEARCH_URL, timeout=20.0,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"},
            json={"query": query[:1000], "search_depth": "advanced", "max_results": 5,
                  "include_answer": False, "include_raw_content": False})
    except httpx.TimeoutException as exc:
        raise WebSearchError("search_timeout", "Tavily 搜索超时。", retryable=True) from exc
    except httpx.RequestError as exc:
        raise WebSearchError("search_connection_failed", "无法连接 Tavily 搜索服务。", retryable=True) from exc
    finally:
        if owned:
            await shared.aclose()
    if response.status_code in {401, 403}:
        raise WebSearchError("search_authentication_failed", "Tavily API Key 无效或未获授权。")
    if response.status_code == 429:
        raise WebSearchError("search_rate_limited", "Tavily 搜索请求过于频繁。", retryable=True)
    if response.status_code >= 500:
        raise WebSearchError("search_service_error", f"Tavily 返回 HTTP {response.status_code}。", retryable=True)
    if response.status_code >= 400:
        raise WebSearchError("search_request_failed", f"Tavily 搜索请求失败（HTTP {response.status_code}）。")
    try:
        data = response.json()
    except ValueError as exc:
        raise WebSearchError("search_invalid_response", "Tavily 返回了无效响应。") from exc
    rows = data.get("results") if isinstance(data, dict) else None
    sources = extract_native_sources(rows if isinstance(rows, list) else [], vendor="tavily")
    by_url = {str(item.get("url")): item for item in rows or [] if isinstance(item, dict)}
    for source in sources:
        original = by_url.get(str(source.get("url")), {})
        source["snippet"] = str(original.get("content") or source.get("snippet") or "")[:2500]
        source["score"] = original.get("score")
    return sources


async def tavily_extract(urls: list[str], api_key: str, *, client: httpx.AsyncClient | None = None) -> list[dict[str, Any]]:
    if not api_key.strip():
        raise WebSearchError("provider_credential_missing", "请配置 Tavily API Key。")
    urls = list(dict.fromkeys(url for url in urls if str(url).startswith(("https://", "http://"))))[:20]
    if not urls:
        return []
    owned = client is None
    shared = client or httpx.AsyncClient(timeout=30.0)
    try:
        response = await shared.post(TAVILY_EXTRACT_URL, timeout=30.0,
            headers={"Authorization": f"Bearer {api_key.strip()}", "Content-Type": "application/json"},
            json={"urls": urls, "extract_depth": "advanced", "include_images": False})
    except httpx.TimeoutException as exc:
        raise WebSearchError("extract_timeout", "Tavily 正文提取超时。", retryable=True) from exc
    except httpx.RequestError as exc:
        raise WebSearchError("extract_connection_failed", "无法连接 Tavily 正文提取服务。", retryable=True) from exc
    finally:
        if owned:
            await shared.aclose()
    if response.status_code in {401, 403}:
        raise WebSearchError("search_authentication_failed", "Tavily API Key 无效或未获授权。")
    if response.status_code == 429:
        raise WebSearchError("search_rate_limited", "Tavily 正文提取请求过于频繁。", retryable=True)
    if response.status_code >= 500:
        raise WebSearchError("extract_service_error", f"Tavily 提取服务返回 HTTP {response.status_code}。", retryable=True)
    if response.status_code >= 400:
        raise WebSearchError("extract_request_failed", f"Tavily 正文提取失败（HTTP {response.status_code}）。")
    try:
        data = response.json()
    except ValueError as exc:
        raise WebSearchError("extract_invalid_response", "Tavily 提取服务返回了无效响应。") from exc
    results = data.get("results") if isinstance(data, dict) else None
    output: list[dict[str, Any]] = []
    for item in results if isinstance(results, list) else []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("url") or "").strip()
        if url.startswith(("https://", "http://")):
            output.append({"url": url[:2048], "content": str(item.get("raw_content") or item.get("content") or "")[:16000],
                           "source_type": "full_text"})
    return output


def extract_native_sources(value: Any, *, vendor: str = "") -> list[dict[str, Any]]:
    """Normalize only source objects explicitly returned by the provider API."""
    candidates: list[Any] = []

    def collect(node: Any) -> None:
        if isinstance(node, list):
            for child in node:
                collect(child)
            return
        if not isinstance(node, dict):
            return
        if node.get("url") or node.get("uri") or isinstance(node.get("url_citation"), dict):
            candidates.append(node)
            return
        for key in ("search_info", "web_search", "web_search_results", "web_search_sources",
                    "search_results", "citations", "output", "action", "sources", "item",
                    "response", "choices", "message", "delta", "annotations", "content", "results"):
            child = node.get(key)
            if isinstance(child, (dict, list)):
                collect(child)

    collect(value)

    results: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in candidates:
        if not isinstance(item, dict):
            continue
        citation = item.get("url_citation") if isinstance(item.get("url_citation"), dict) else item
        url = str(citation.get("url") or citation.get("uri") or "").strip()
        title = str(citation.get("title") or citation.get("name") or "").strip()
        snippet = str(citation.get("snippet") or citation.get("content") or citation.get("description") or "").strip()
        if not url.startswith(("https://", "http://")) or url in seen:
            continue
        seen.add(url)
        results.append({"title": title[:300] or url, "url": url[:2048], "snippet": snippet[:2500],
                        "published_at": str(citation.get("published_at") or citation.get("date") or "")[:100],
                        "provider": vendor})
    return results[:10]
