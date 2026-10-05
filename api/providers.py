"""Provider catalog, temporary model discovery, and local configuration APIs."""

from __future__ import annotations

import asyncio
import re
from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from api.schemas import ProbeRequest, ProviderProfileIn, ProviderProfileOut, ProviderSelection
from runtime.providers.catalog import get_provider, search_providers
from runtime.providers.openai_compatible import OpenAICompatibleProvider
from runtime.providers.profiles import ProviderProfile
from runtime.providers.registry import DEFAULT_ROLE
from runtime.providers.secrets import InMemorySecretStore, env_var_for
from runtime.model_options import model_options, resolve_model_options
from runtime.web_search import native_search_capability
from runtime.core.errors import ProviderError
from runtime.service import RuntimeService
from runtime.providers.factory import save_profiles, save_role_binding

router = APIRouter(tags=["providers"])


class ProviderDiscoveryIn(BaseModel):
    vendor_id: str = "custom"
    profile_id: str = "discovery"
    display_name: str = ""
    protocol: str = "openai_compatible"
    base_url: str = ""
    api_key: str | None = None
    api_key_ref: str = ""
    models: list[str] = Field(default_factory=list)
    model: str = ""
    capabilities: dict[str, bool] = Field(default_factory=dict)
    model_capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)
    timeout_ms: int = 30000


class ModelsUpdateIn(BaseModel):
    models: list[str] = Field(default_factory=list)
    default_model: str = ""
    model_capabilities: dict[str, dict[str, Any]] = Field(default_factory=dict)


class ProviderDeleteIn(BaseModel):
    replacement_profile_id: str = ""
    replacement_model: str = ""


class ModelDeleteIn(BaseModel):
    replacement_profile_id: str = ""
    replacement_model: str = ""


def _service(request: Request) -> RuntimeService:
    return request.app.state.service


def _to_out(profile: ProviderProfile, has_secret: bool) -> ProviderProfileOut:
    data = profile.to_dict()
    data.pop("extra_headers", None)
    return ProviderProfileOut(**data, has_secret=has_secret)


def _model_capabilities(vendor_id: str, model: str, api_mode: str = "auto") -> dict[str, Any]:
    options = model_options(vendor_id, model)
    options["native_web_search"] = native_search_capability(vendor_id, model, api_mode=api_mode)
    return options


def _normalize_base(vendor_id: str, base_url: str) -> str:
    value = base_url.strip().rstrip("/")
    if vendor_id == "deepseek":
        value = re.sub(r"/v1$", "", value, flags=re.IGNORECASE)
    return value


@router.get("/providers/catalog")
async def provider_catalog(query: str = Query(default="")) -> list[dict[str, Any]]:
    return search_providers(query)


@router.post("/providers/discover")
async def discover_provider(payload: ProviderDiscoveryIn) -> dict[str, Any]:
    vendor = get_provider(payload.vendor_id) or get_provider("custom")
    base_url = _normalize_base(payload.vendor_id, payload.base_url or str((vendor or {}).get("base_url") or ""))
    if not base_url:
        raise HTTPException(status_code=422, detail={"code": "base_url_required", "message": "请提供兼容接口地址。"})
    profile_id = payload.profile_id.strip() or "discovery"
    api_key_ref = payload.api_key_ref.strip() or f"discovery:{profile_id}"
    is_local = payload.protocol == "local" or payload.vendor_id == "ollama"
    profile = ProviderProfile(
        profile_id=profile_id,
        display_name=payload.display_name or str((vendor or {}).get("name") or "自定义接口"),
        protocol="local" if is_local else "openai_compatible",
        base_url=base_url,
        api_key_ref=api_key_ref,
        default_model=payload.model,
        models=list(payload.models),
        vendor_id=payload.vendor_id,
        model_capabilities=dict(payload.model_capabilities),
        capabilities=dict(payload.capabilities),
        timeout_ms=min(max(payload.timeout_ms, 1000), 1800000),
    )
    secrets = InMemorySecretStore({api_key_ref: payload.api_key} if payload.api_key else {})
    provider = OpenAICompatibleProvider(profile, secrets, timeout_seconds=min(profile.timeout_ms / 1000, 30))
    try:
        models = await provider.list_models()
    except ProviderError as exc:
        kind = str(exc.details.get("kind") or exc.kind or "provider_error")
        http_status = exc.details.get("http_status")
        if http_status in {401, 403} or kind in {"authentication_error", "invalid_api_key", "unauthorized", "auth_failed"}:
            return {"status": "authentication_failed", "models": [], "message": "认证失败，请检查 API Key。", "kind": kind}
        if http_status in {404, 405, 501} or kind == "model_list_unsupported":
            return {"status": "unsupported", "models": [], "message": "该接口不支持模型列表，请手动填写模型 ID。", "kind": "model_list_unsupported"}
        return {"status": "network_error", "models": [], "message": "无法连接或读取模型列表，请检查地址和网络。", "kind": kind}
    return {
        "status": "ok",
        "models": [{"id": model, **_model_capabilities(payload.vendor_id, model)} for model in models],
        "message": "已读取可用模型列表。",
    }


@router.get("/providers", response_model=list[ProviderProfileOut])
async def list_providers(request: Request) -> list[ProviderProfileOut]:
    service = _service(request)
    return [_to_out(profile, service.router.has_secret(profile.profile_id)) for profile in service.router.profiles()]


@router.put("/providers/{profile_id}", response_model=ProviderProfileOut)
async def upsert_provider(profile_id: str, payload: ProviderProfileIn, request: Request) -> ProviderProfileOut:
    service = _service(request)
    if payload.profile_id != profile_id:
        raise HTTPException(status_code=400, detail="profile_id mismatch")
    try:
        prior_profile = service.router.profile(profile_id)
    except ValueError:
        prior_profile = None
    profile_data = payload.model_dump(exclude={"api_key"})
    if prior_profile:
        for field in (
            "display_name", "protocol", "base_url", "api_key_ref", "default_model", "models",
            "api_mode", "extra_headers", "timeout_ms", "max_retries", "capabilities", "vendor_id",
            "model_selection_mode", "model_capabilities", "enabled",
        ):
            if field not in payload.model_fields_set:
                profile_data[field] = getattr(prior_profile, field)
    # Older profiles may use a non-standard secret reference. Preserve it when
    # editing the profile without rotating its credential.
    api_key_ref = str(profile_data.get("api_key_ref") or (prior_profile.api_key_ref if prior_profile else "") or f"provider:{profile_id}")
    profile_data["api_key_ref"] = api_key_ref
    profile = ProviderProfile.from_dict({**profile_data, "base_url": _normalize_base(str(profile_data.get("vendor_id") or ""), str(profile_data.get("base_url") or ""))})
    if profile.models and profile.default_model and profile.default_model not in profile.models:
        raise HTTPException(status_code=422, detail={"code": "default_model_not_added", "message": "默认模型必须在已添加列表中。"})
    if profile.vendor_id == "" and (match := next((item for item in search_providers() if profile.base_url.startswith(str(item["base_url"])) and item["base_url"]), None)):
        profile.vendor_id = str(match["id"])
    role = service.router.roles.get(DEFAULT_ROLE)
    if prior_profile and role and role[0] == profile_id:
        if profile.models:
            if role[1] not in profile.models or (
                "default_model" in payload.model_fields_set and profile.default_model != prior_profile.default_model
            ):
                if profile.default_model not in profile.models:
                    raise HTTPException(status_code=409, detail={"code": "replacement_model_required", "message": "修改默认服务的模型前，请明确选择新的默认模型。"})
                service.router.set_role(DEFAULT_ROLE, profile_id, profile.default_model)
                save_role_binding(DEFAULT_ROLE, profile_id, profile.default_model)
        elif profile.model_selection_mode != "manual" or (
            "default_model" in payload.model_fields_set and profile.default_model != prior_profile.default_model
        ):
            alternatives = [item for item in service.router.profiles() if item.profile_id != profile_id and item.enabled]
            if alternatives:
                raise HTTPException(status_code=409, detail={"code": "replacement_provider_required", "message": "清空默认服务的模型前，请明确选择替代服务。"})
            service.router.unset_role(DEFAULT_ROLE)
            save_role_binding(DEFAULT_ROLE, "", "")
    if payload.api_key:
        service.router.secret_store.set(api_key_ref, payload.api_key)
    service.router.add(profile)
    if DEFAULT_ROLE not in service.router.roles:
        service.router.set_role(DEFAULT_ROLE, profile_id, profile.default_model)
        save_role_binding(DEFAULT_ROLE, profile_id, profile.default_model)
    save_profiles(service.router.profiles())
    return _to_out(profile, service.router.has_secret(profile_id))


@router.get("/providers/default", response_model=ProviderSelection)
async def get_default_provider(request: Request, role: str = Query(default=DEFAULT_ROLE)) -> ProviderSelection:
    service = _service(request)
    binding = service.router.roles.get(role)
    if binding is None:
        profiles = [item for item in service.router.profiles() if item.enabled]
        if not profiles:
            raise HTTPException(status_code=404, detail={"code": "no_provider_profile", "message": "尚未配置模型服务。"})
        profile = profiles[0]
        return ProviderSelection(role=role, profile_id=profile.profile_id, model=profile.default_model)
    return ProviderSelection(role=role, profile_id=binding[0], model=binding[1])


@router.patch("/providers/default", response_model=ProviderSelection)
async def set_default_provider(payload: ProviderSelection, request: Request) -> ProviderSelection:
    service = _service(request)
    try:
        profile = service.router.profile(payload.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    model = payload.model or profile.default_model
    if model and profile.models and model not in profile.models:
        raise HTTPException(status_code=422, detail={"code": "model_not_added", "message": "请先将模型添加到该服务。"})
    service.router.set_role(payload.role, profile.profile_id, model)
    save_role_binding(payload.role, profile.profile_id, model)
    return ProviderSelection(role=payload.role, profile_id=profile.profile_id, model=model)


@router.get("/providers/{profile_id}/model-catalog")
async def model_catalog(profile_id: str, request: Request,
                        refresh: bool = Query(default=False)) -> list[dict[str, Any]]:
    service = _service(request)
    try:
        profile = service.router.profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    try:
        catalog_loader = getattr(service.router, "list_model_catalog", None)
        if callable(catalog_loader):
            catalog_state = await catalog_loader(profile_id, refresh=refresh)
            discovered = list(catalog_state.get("models") or [])
            catalog_status = str(catalog_state.get("status") or "unverified")
        else:
            discovered = await service.router.get(profile_id).list_models()
            catalog_status = "verified"
    except Exception:
        discovered = []
        catalog_status = "unavailable"
    all_models = list(dict.fromkeys([*profile.models, *discovered]))
    describe = getattr(service.router, "describe_model_options", None)
    descriptions: dict[str, dict[str, Any]] = {}
    if profile.vendor_id in {"ollama", "openrouter"} and callable(describe):
        results = await asyncio.gather(*(describe(profile_id, model, refresh=refresh) for model in all_models),
                                       return_exceptions=True)
        descriptions = {model: result for model, result in zip(all_models, results)
                        if isinstance(result, dict)}
    rows: list[dict[str, Any]] = []
    for item in all_models:
        options = resolve_model_options(profile.vendor_id, item,
                                        profile.model_capabilities.get(item))
        options["native_web_search"] = native_search_capability(profile.vendor_id, item, api_mode=profile.api_mode)
        options["catalog_status"] = catalog_status
        discovered_options = descriptions.get(item) or {}
        options.update(discovered_options)
        if discovered_options.get("options_source") in {"ollama_api_show", "provider_model_metadata"}:
            profile.model_capabilities[item] = options
        rows.append({"id": item, **options, "selected": item in profile.models})
    if profile.vendor_id in {"ollama", "openrouter"} and any(item in profile.model_capabilities for item in all_models):
        save_profiles(service.router.profiles())
    return rows


@router.put("/providers/{profile_id}/models")
async def update_models(profile_id: str, payload: ModelsUpdateIn, request: Request) -> dict[str, Any]:
    service = _service(request)
    try:
        profile = service.router.profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if any(session_id in request.app.state.active_turns and (session := service.sessions.load(session_id)) is not None
           and str(session.metadata.get("provider_profile") or "") == profile_id for session_id in list(request.app.state.active_turns)):
        raise HTTPException(status_code=409, detail={"code": "provider_busy", "message": "该模型服务正在生成，暂时不能修改模型。"})
    models = list(dict.fromkeys(model.strip() for model in payload.models if model.strip()))
    default_model = payload.default_model.strip()
    role = service.router.roles.get(DEFAULT_ROLE)
    if profile.default_model and profile.default_model not in models and models and not default_model:
        raise HTTPException(status_code=409, detail={"code": "replacement_model_required", "message": "移除该服务的默认模型前，请明确指定新的默认模型。"})
    if default_model and default_model not in models:
        raise HTTPException(status_code=422, detail={"code": "default_model_not_added", "message": "默认模型必须在已添加列表中。"})
    if role and role[0] == profile_id and role[1] not in models:
        if models:
            if not default_model:
                raise HTTPException(status_code=409, detail={"code": "replacement_model_required", "message": "移除当前默认模型前，请明确指定新的默认模型。"})
        else:
            alternatives = [item for item in service.router.profiles() if item.profile_id != profile_id and item.enabled]
            if alternatives:
                raise HTTPException(status_code=409, detail={"code": "replacement_provider_required", "message": "移除默认服务的最后一个模型前，请明确选择替代服务。"})
            service.router.unset_role(DEFAULT_ROLE)
            save_role_binding(DEFAULT_ROLE, "", "")
    profile.models = models
    profile.default_model = default_model or (models[0] if models else "")
    profile.model_capabilities = {
        model: dict(payload.model_capabilities.get(model) or _model_capabilities(profile.vendor_id, model, profile.api_mode))
        for model in models
    }
    service.router.add(profile)
    if role and role[0] == profile_id and default_model:
        service.router.set_role(DEFAULT_ROLE, profile_id, default_model)
        save_role_binding(DEFAULT_ROLE, profile_id, default_model)
    save_profiles(service.router.profiles())
    return {"profile_id": profile_id, "models": models, "default_model": profile.default_model}


@router.delete("/providers/{profile_id}/models/{model_id:path}")
async def delete_model(profile_id: str, model_id: str, request: Request, payload: ModelDeleteIn | None = None) -> dict[str, Any]:
    service = _service(request)
    try:
        profile = service.router.profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    if any(session_id in request.app.state.active_turns and (session := service.sessions.load(session_id)) is not None
           and str(session.metadata.get("provider_profile") or "") == profile_id
           and str(session.metadata.get("model") or "") == model_id for session_id in list(request.app.state.active_turns)):
        raise HTTPException(status_code=409, detail={"code": "model_busy", "message": "该模型正在生成，暂时不能删除。"})
    legacy_unlisted_default = not profile.models and profile.model_selection_mode == "manual" and profile.default_model == model_id
    if model_id not in profile.models and not legacy_unlisted_default:
        raise HTTPException(status_code=404, detail={"code": "model_not_found", "message": "该模型未添加。"})
    payload = payload or ModelDeleteIn()
    current = service.router.roles.get(DEFAULT_ROLE)
    is_profile_default = profile.default_model == model_id
    if current == (profile_id, model_id):
        candidates = [item for item in profile.models if item != model_id]
        if candidates:
            replacement = payload.replacement_model.strip()
            if replacement not in candidates:
                raise HTTPException(status_code=409, detail={"code": "replacement_model_required", "message": "请先明确指定新的默认模型。"})
            if is_profile_default:
                profile.default_model = replacement
            service.router.set_role(DEFAULT_ROLE, profile_id, replacement)
            save_role_binding(DEFAULT_ROLE, profile_id, replacement)
        elif payload.replacement_profile_id:
            candidate = next((item for item in service.router.profiles() if item.profile_id == payload.replacement_profile_id and item.profile_id != profile_id and item.enabled), None)
            if candidate is None:
                raise HTTPException(status_code=422, detail={"code": "replacement_provider_invalid", "message": "替代服务不存在或已停用。"})
            replacement = payload.replacement_model or candidate.default_model
            if candidate.models and replacement not in candidate.models:
                raise HTTPException(status_code=422, detail={"code": "replacement_model_invalid", "message": "替代模型尚未添加。"})
            service.router.set_role(DEFAULT_ROLE, candidate.profile_id, replacement)
            save_role_binding(DEFAULT_ROLE, candidate.profile_id, replacement)
        else:
            alternatives = [item for item in service.router.profiles() if item.profile_id != profile_id and item.enabled]
            if alternatives:
                raise HTTPException(status_code=409, detail={"code": "replacement_provider_required", "message": "当前服务没有其他模型，请明确选择替代服务。"})
            service.router.unset_role(DEFAULT_ROLE)
            save_role_binding(DEFAULT_ROLE, "", "")
    elif is_profile_default:
        candidates = [item for item in profile.models if item != model_id]
        if candidates:
            replacement = payload.replacement_model.strip()
            if replacement not in candidates:
                raise HTTPException(status_code=409, detail={"code": "replacement_model_required", "message": "请先明确指定新的默认模型。"})
            profile.default_model = replacement
        elif payload.replacement_profile_id:
            candidate = next((item for item in service.router.profiles() if item.profile_id == payload.replacement_profile_id and item.profile_id != profile_id and item.enabled), None)
            if candidate is None:
                raise HTTPException(status_code=422, detail={"code": "replacement_provider_invalid", "message": "替代服务不存在或已停用。"})
            replacement = payload.replacement_model or candidate.default_model
            if not replacement or (candidate.models and replacement not in candidate.models):
                raise HTTPException(status_code=422, detail={"code": "replacement_model_invalid", "message": "替代模型尚未添加。"})
            profile.default_model = ""
        else:
            alternatives = [item for item in service.router.profiles() if item.profile_id != profile_id and item.enabled]
            if alternatives:
                raise HTTPException(status_code=409, detail={"code": "replacement_provider_required", "message": "当前服务没有其他模型，请明确选择替代服务。"})
            profile.default_model = ""
    profile.models = [item for item in profile.models if item != model_id]
    profile.model_capabilities.pop(model_id, None)
    if not profile.models:
        profile.model_selection_mode = "catalog"
    if profile.default_model == model_id:
        profile.default_model = profile.models[0] if profile.models else ""
    service.router.add(profile)
    save_profiles(service.router.profiles())
    return {"profile_id": profile_id, "deleted_model": model_id, "models": profile.models, "default_model": profile.default_model}


@router.delete("/providers/{profile_id}")
async def delete_provider(profile_id: str, request: Request, payload: ProviderDeleteIn | None = None) -> dict[str, Any]:
    service = _service(request)
    try:
        profile = service.router.profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    payload = payload or ProviderDeleteIn()
    active = []
    for session_id in list(request.app.state.active_turns):
        session = service.sessions.load(session_id) if service.sessions is not None else None
        if session and str(session.metadata.get("provider_profile") or "") == profile_id:
            active.append(session_id)
    if active:
        raise HTTPException(status_code=409, detail={"code": "provider_busy", "message": "该服务正在生成，暂时不能删除。"})
    remaining = [item for item in service.router.profiles() if item.profile_id != profile_id and item.enabled]
    current = service.router.roles.get(DEFAULT_ROLE)
    if current and current[0] == profile_id:
        replacement = payload.replacement_profile_id.strip()
        if remaining and not replacement:
            raise HTTPException(status_code=409, detail={"code": "replacement_provider_required", "message": "删除默认服务前，请明确选择替代服务。"})
        if replacement:
            candidate = next((item for item in remaining if item.profile_id == replacement), None)
            if candidate is None:
                raise HTTPException(status_code=422, detail={"code": "replacement_provider_invalid", "message": "替代服务不存在或已停用。"})
            model = payload.replacement_model or candidate.default_model
            if not model or (candidate.models and model not in candidate.models):
                raise HTTPException(status_code=422, detail={"code": "replacement_model_invalid", "message": "替代模型尚未添加。"})
            service.router.set_role(DEFAULT_ROLE, candidate.profile_id, model)
            save_role_binding(DEFAULT_ROLE, candidate.profile_id, model)
        else:
            service.router.unset_role(DEFAULT_ROLE)
            save_role_binding(DEFAULT_ROLE, "", "")
    remove_and_close = getattr(service.router, "remove_and_close", None)
    if callable(remove_and_close):
        await remove_and_close(profile_id)
    else:
        service.router.remove(profile_id)
    refs_still_used = {item.api_key_ref for item in service.router.profiles() if item.api_key_ref}
    if profile.api_key_ref and profile.api_key_ref not in refs_still_used:
        service.router.secret_store.delete(profile.api_key_ref)
    save_profiles(service.router.profiles())
    return {"deleted_profile": profile_id, "remaining_profiles": [item.profile_id for item in service.router.profiles()]}


@router.post("/providers/{profile_id}/probe")
async def probe_provider(profile_id: str, payload: ProbeRequest, request: Request) -> dict[str, Any]:
    service = _service(request)
    try:
        result = await service.router.probe(profile_id, model=payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return {"profile_id": profile_id, "status": result.get("status"), "kind": result.get("kind"),
            "message": result.get("message", ""), "capabilities": result.get("capabilities", {}),
            "secret_env_var": env_var_for(service.router.profile(profile_id).api_key_ref)}


@router.get("/providers/{profile_id}/models", response_model=list[str])
async def list_models(profile_id: str, request: Request,
                      refresh: bool = Query(default=False)) -> list[str]:
    service = _service(request)
    try:
        service.router.profile(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    catalog_loader = getattr(service.router, "list_model_catalog", None)
    if callable(catalog_loader):
        result = await catalog_loader(profile_id, refresh=refresh)
        return list(result.get("models") or [])
    return await service.router.get(profile_id).list_models()
