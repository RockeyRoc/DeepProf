"""Single-URL crawler with explicit allowlist, ToS and robots checks."""

from __future__ import annotations

import asyncio
import ipaddress
import time
from dataclasses import dataclass, field
from pathlib import PurePosixPath
from urllib.parse import urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx

from library.errors import LibraryError


@dataclass(slots=True)
class CrawlPolicy:
    allowlist: list[str] = field(default_factory=list)
    tos_confirmed_domains: list[str] = field(default_factory=list)
    delay_seconds: float = 1.0
    max_bytes: int = 50_000_000
    user_agent: str = "DeepProfResourceCrawler/0.6"

    def check(self, url: str) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise LibraryError("爬虫只允许 http/https URL", kind="invalid_url", url=url)
        host = parsed.hostname.lower().rstrip(".")
        try:
            address = ipaddress.ip_address(host)
        except ValueError:
            address = None
        if address is not None and (address.is_private or address.is_loopback or address.is_link_local):
            raise LibraryError("禁止抓取本机或私有网络地址", kind="domain_denied", domain=host)
        if not _matches(host, self.allowlist):
            raise LibraryError("URL 不在资源库白名单内", kind="domain_denied", domain=host)
        if not _matches(host, self.tos_confirmed_domains):
            raise LibraryError("URL 所在域名尚未确认 ToS", kind="tos_not_confirmed", domain=host)
        return host


@dataclass(slots=True)
class CrawlResult:
    url: str
    content: bytes
    extension: str
    content_type: str
    robots_allowed: bool = True


class SingleUrlCrawler:
    def __init__(
        self,
        policy: CrawlPolicy,
        *,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.policy = policy
        self._client = client
        self._last_request: dict[str, float] = {}

    async def fetch(self, url: str) -> CrawlResult:
        current = url
        client = self._client
        owns_client = client is None
        if client is None:
            client = httpx.AsyncClient(
                follow_redirects=False,
                headers={"User-Agent": self.policy.user_agent},
                timeout=30.0,
            )
        try:
            for _ in range(4):
                host = self.policy.check(current)
                await self._respect_rate_limit(host)
                await self._check_robots(client, current)
                try:
                    response = await client.get(current, follow_redirects=False)
                except httpx.HTTPError as exc:
                    raise LibraryError("抓取请求失败", kind="crawl_failed", url=current, error=str(exc)) from exc
                if response.is_redirect:
                    location = response.headers.get("location")
                    if not location:
                        raise LibraryError("重定向缺少 Location", kind="crawl_failed", url=current)
                    current = urljoin(current, location)
                    continue
                if response.status_code < 200 or response.status_code >= 300:
                    raise LibraryError(
                        f"抓取返回 HTTP {response.status_code}",
                        kind="http_error",
                        url=current,
                        status_code=response.status_code,
                    )
                declared = int(response.headers.get("content-length", "0") or 0)
                if declared > self.policy.max_bytes or len(response.content) > self.policy.max_bytes:
                    raise LibraryError("抓取内容超过大小限制", kind="size_limit", max_bytes=self.policy.max_bytes)
                content_type = response.headers.get("content-type", "").split(";", 1)[0].strip().lower()
                return CrawlResult(current, response.content, _extension(current, content_type), content_type)
            raise LibraryError("重定向次数超过限制", kind="redirect_limit", url=url)
        finally:
            if owns_client:
                await client.aclose()

    async def _respect_rate_limit(self, host: str) -> None:
        now = time.monotonic()
        wait = self.policy.delay_seconds - (now - self._last_request.get(host, 0.0))
        if wait > 0:
            await asyncio.sleep(wait)
        self._last_request[host] = time.monotonic()

    async def _check_robots(self, client: httpx.AsyncClient, url: str) -> None:
        parsed = urlparse(url)
        robots_url = f"{parsed.scheme}://{parsed.netloc}/robots.txt"
        try:
            response = await client.get(robots_url, follow_redirects=False)
        except httpx.HTTPError as exc:
            raise LibraryError("无法检查 robots.txt，已拒绝抓取", kind="robots_unavailable", error=str(exc)) from exc
        if response.status_code == 404:
            return
        if response.status_code < 200 or response.status_code >= 300:
            raise LibraryError(
                f"robots.txt 返回 HTTP {response.status_code}",
                kind="robots_denied",
                status_code=response.status_code,
            )
        parser = RobotFileParser()
        parser.set_url(robots_url)
        parser.parse(response.text.splitlines())
        if not parser.can_fetch(self.policy.user_agent, url):
            raise LibraryError("robots.txt 禁止抓取该 URL", kind="robots_denied", url=url)


def _matches(host: str, entries: list[str]) -> bool:
    normalized = [entry.lower().strip().rstrip(".") for entry in entries if entry.strip()]
    return any(host == entry or host.endswith(f".{entry}") for entry in normalized)


def _extension(url: str, content_type: str) -> str:
    suffix = PurePosixPath(urlparse(url).path).suffix.lower()
    if suffix in {".pdf", ".epub", ".docx", ".pptx", ".md", ".markdown", ".txt", ".html", ".htm"}:
        return suffix
    return {
        "application/pdf": ".pdf",
        "application/epub+zip": ".epub",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation": ".pptx",
        "text/markdown": ".md",
        "text/plain": ".txt",
        "text/html": ".html",
    }.get(content_type, ".html" if content_type == "application/xhtml+xml" else "")


__all__ = ["CrawlPolicy", "CrawlResult", "SingleUrlCrawler"]
