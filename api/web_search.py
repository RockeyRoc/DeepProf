"""Status and explicit probe for model-provider-native web search."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel

from runtime.core.events import new_id
from runtime.web_search import (OLLAMA_SEARCH_SECRET_REF, TAVILY_SEARCH_SECRET_REF, WebSearchError, extract_native_sources,
                                native_search_capability, native_search_request, ollama_search_api_key,
                                ollama_web_search)

router = APIRouter(tags=["web-search"])


class NativeSearchProbeIn(BaseModel):
    profile_id: str = ""
    model: str = ""


class NativeSearchSettingsIn(BaseModel):
    api_key: str = ""
    clear_key: bool = False


class DeepResearchSettingsIn(BaseModel):
    api_key: str = ""
    clear_key: bool = False


def _service(request: Request) -> Any:
    return request.app.state.service


def _selection(service: Any, profile_id: str, model: str) -> tuple[Any | None, str]:
    if not profile_id:
        binding = service.router.roles.get("tutor.default")
        if binding:
            profile_id, bound_model = binding
            model = model or bound_model
        else:
            profiles = [item for item in service.router.profiles() if item.enabled]
            if profiles:
                profile_id = profiles[0].profile_id
    if not profile_id:
        return None, model
    try:
        profile = service.router.profile(profile_id)
    except ValueError:
        return None, model
    return profile, model or profile.default_model


def _status(service: Any, profile_id: str = "", model: str = "") -> dict[str, Any]:
    profile, selected_model = _selection(service, profile_id, model)
    if profile is None:
        return {"status": "provider_not_configured", "configured": False, "supported": False,
                "provider": "", "model": "", "mode_default": "auto",
                "message": "请先配置并选择模型服务。"}
    capability = native_search_capability(profile.vendor_id, selected_model, api_mode=profile.api_mode)
    configured = (bool(ollama_search_api_key(service.router.secret_store))
                  if capability.get("requires_api_key") else
                  service.router.has_secret(profile.profile_id) or profile.protocol == "local")
    status = "unsupported" if not capability.get("supported") else "ready" if configured else "provider_credential_missing"
    return {**capability, "status": status, "configured": configured,
            "profile_id": profile.profile_id, "provider_name": profile.display_name or profile.vendor_id,
            "model": selected_model, "mode_default": "auto",
            "message": ("当前模型的原生联网 API 可用。" if status == "ready" else
                        "当前模型支持原生联网，但模型服务 API Key 尚未配置。" if status == "provider_credential_missing" else
                        str(capability.get("reason") or "当前模型不支持原生联网搜索。"))}


@router.put("/settings/web-search")
async def put_web_search_settings(payload: NativeSearchSettingsIn, request: Request,
                                  profile_id: str = Query(default=""),
                                  model: str = Query(default="")) -> dict[str, Any]:
    service = _service(request)
    profile, selected_model = _selection(service, profile_id, model)
    if profile is None:
        raise HTTPException(status_code=404, detail={"code": "provider_not_configured", "message": "请先配置模型服务。"})
    capability = native_search_capability(profile.vendor_id, selected_model, api_mode=profile.api_mode)
    if not capability.get("requires_api_key"):
        raise HTTPException(status_code=422, detail={"code": "native_search_key_not_required",
            "message": "当前模型使用其模型 API 凭据，无需额外联网密钥。"})
    try:
        if payload.clear_key:
            service.router.secret_store.delete(OLLAMA_SEARCH_SECRET_REF)
        elif payload.api_key.strip():
            service.router.secret_store.set(OLLAMA_SEARCH_SECRET_REF, payload.api_key.strip())
        else:
            raise HTTPException(status_code=422, detail={"code": "provider_credential_missing",
                "message": "请输入 Ollama Web Search API Key。"})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "credential_store_failed",
            "message": "无法安全保存 Ollama 搜索密钥。"}) from exc
    return _status(service, profile.profile_id, selected_model)


@router.get("/settings/web-search")
async def get_web_search_settings(request: Request, profile_id: str = Query(default=""),
                                  model: str = Query(default="")) -> dict[str, Any]:
    return _status(_service(request), profile_id, model)


@router.get("/settings/deep-research")
async def get_deep_research_settings(request: Request, profile_id: str = Query(default=""),
                                     model: str = Query(default="")) -> dict[str, Any]:
    service = _service(request)
    profile, selected_model = _selection(service, profile_id, model)
    tavily_configured = bool(service.router.secret_store.get(TAVILY_SEARCH_SECRET_REF))
    ollama_configured = bool(ollama_search_api_key(service.router.secret_store))
    capability = (native_search_capability(profile.vendor_id, selected_model, api_mode=profile.api_mode)
                  if profile is not None else {"supported": False, "kind": "unsupported"})
    native_configured = bool(capability.get("supported") and
        (ollama_configured if capability.get("requires_api_key") else
         service.router.has_secret(profile.profile_id) or profile.protocol == "local"))
    native_label = ("Ollama Web Search" if capability.get("kind") == "ollama_web_search"
                    else f"{profile.display_name or profile.vendor_id} 原生联网" if profile else "模型原生联网")
    search_service = (native_label + ("（Tavily备用）" if tavily_configured else "") if native_configured
                      else "Tavily Search + Extract" if tavily_configured
                      else "Ollama Web Search" if ollama_configured else "")
    return {"ready": bool(search_service), "search_service": search_service,
            "native_supported": bool(capability.get("supported")), "native_configured": native_configured,
            "native": capability, "tavily_configured": tavily_configured,
            "ollama_configured": ollama_configured, "profile_id": profile.profile_id if profile else "",
            "model": selected_model,
            "limits": {"max_rounds": 6, "max_searches": 24, "max_sources": 30,
                       "timeout_seconds": 1200, "concurrency": 3}}


@router.put("/settings/deep-research")
async def put_deep_research_settings(payload: DeepResearchSettingsIn, request: Request) -> dict[str, Any]:
    service = _service(request)
    try:
        if payload.clear_key:
            service.router.secret_store.delete(TAVILY_SEARCH_SECRET_REF)
        elif payload.api_key.strip():
            service.router.secret_store.set(TAVILY_SEARCH_SECRET_REF, payload.api_key.strip())
        else:
            raise HTTPException(status_code=422, detail={"code": "provider_credential_missing",
                "message": "请输入 Tavily API Key。"})
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(status_code=503, detail={"code": "credential_store_failed",
            "message": "无法安全保存 Tavily 搜索密钥。"}) from exc
    return await get_deep_research_settings(request)


@router.post("/settings/web-search/probe")
async def post_web_search_probe(payload: NativeSearchProbeIn, request: Request) -> dict[str, Any]:
    service = _service(request)
    profile, model = _selection(service, payload.profile_id, payload.model)
    if profile is None:
        raise HTTPException(status_code=404, detail={"code": "provider_not_configured", "message": "请先配置模型服务。"})
    capability = native_search_capability(profile.vendor_id, model, api_mode=profile.api_mode)
    if not capability.get("supported"):
        raise HTTPException(status_code=422, detail={"code": "native_search_unsupported",
            "message": str(capability.get("reason") or "当前模型没有原生联网搜索 API。")})
    if capability.get("requires_api_key"):
        api_key = ollama_search_api_key(service.router.secret_store)
        if not api_key:
            raise HTTPException(status_code=409, detail={"code": "provider_credential_missing", "message": "请先配置 Ollama Web Search API Key。"})
        try:
            sources = await ollama_web_search("latest news today", api_key)
        except WebSearchError as exc:
            raise HTTPException(status_code=502, detail={"code": exc.code, "message": str(exc)}) from exc
        return {"status": "request_succeeded", "provider": profile.vendor_id, "profile_id": profile.profile_id,
                "model": model, "search_confirmed": bool(sources), "sources": sources,
                "message": "Ollama 原生联网搜索 API 请求成功。" if sources else "Ollama 搜索 API 请求成功，但没有返回来源。"}
    if not (service.router.has_secret(profile.profile_id) or profile.protocol == "local"):
        raise HTTPException(status_code=409, detail={"code": "provider_credential_missing", "message": "请先配置当前模型服务的 API Key。"})
    try:
        search = native_search_request(capability, "always" if capability.get("always_search") else "auto")
        provider = service.router.get(profile.profile_id)
        response = await provider.generate({
            "model": model,
            "messages": [
                {"role": "system", "content": "Use the provider's native web search tool for this connectivity check. Answer briefly and cite only returned search sources."},
                {"role": "user", "content": "Search the web for today's date and one recent headline."},
            ],
            "temperature": 0,
            "max_tokens": 256,
            "native_web_search": search,
            "tools": search.get("tools") or [],
        }, {"thinking_enabled": False, "timeout_seconds": 30})
    except Exception as exc:
        raise HTTPException(status_code=502, detail={"code": "native_search_probe_failed",
            "message": str(exc)[:500]}) from exc
    sources = extract_native_sources(response, vendor=profile.vendor_id)
    return {"status": "request_succeeded", "provider": profile.vendor_id, "profile_id": profile.profile_id,
            "model": model, "search_confirmed": bool(sources), "sources": sources,
            "answer": str(response.get("content") or "")[:2000],
            "message": "模型原生联网请求成功。" if sources else
                       "模型 API 请求成功，但本次响应没有返回可验证的来源元数据。"}
