"""Provider Settings API：只编辑 Profile，密钥只写不读。"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Query, Request

from api.schemas import ProbeRequest, ProviderProfileIn, ProviderProfileOut, ProviderSelection
from runtime.providers.factory import save_profiles, save_role_binding
from runtime.providers.profiles import ProviderProfile
from runtime.providers.registry import DEFAULT_ROLE
from runtime.providers.secrets import env_var_for
from runtime.service import RuntimeService

router = APIRouter(tags=["providers"])


def _service(request: Request) -> RuntimeService:
    return request.app.state.service


def _to_out(profile: ProviderProfile, has_secret: bool) -> ProviderProfileOut:
    data = profile.to_dict()
    data.pop("extra_headers", None)
    return ProviderProfileOut(**data, has_secret=has_secret)


@router.get("/providers", response_model=list[ProviderProfileOut])
async def list_providers(request: Request) -> list[ProviderProfileOut]:
    service = _service(request)
    return [
        _to_out(profile, service.router.has_secret(profile.profile_id))
        for profile in service.router.profiles()
    ]


@router.put("/providers/{profile_id}", response_model=ProviderProfileOut)
async def upsert_provider(
    profile_id: str, payload: ProviderProfileIn, request: Request
) -> ProviderProfileOut:
    service = _service(request)
    if payload.profile_id != profile_id:
        raise HTTPException(status_code=400, detail="profile_id mismatch")

    api_key_ref = payload.api_key_ref or f"provider:{profile_id}"
    profile = ProviderProfile.from_dict(
        {
            **payload.model_dump(exclude={"api_key"}),
            "api_key_ref": api_key_ref,
        }
    )
    secrets = service.router.secret_store
    if payload.api_key:
        secrets.set(api_key_ref, payload.api_key)
    service.router.add(profile)
    service.router.set_role(DEFAULT_ROLE, profile_id, profile.default_model)
    save_profiles(service.router.profiles())
    save_role_binding(DEFAULT_ROLE, profile_id, profile.default_model)
    has_secret = service.router.has_secret(profile_id)
    return _to_out(profile, has_secret)


@router.get("/providers/default", response_model=ProviderSelection)
async def get_default_provider(request: Request, role: str = Query(default=DEFAULT_ROLE)) -> ProviderSelection:
    service = _service(request)
    binding = service.router.roles.get(role)
    if binding is None:
        profiles = [item for item in service.router.profiles() if item.enabled]
        if not profiles:
            raise HTTPException(status_code=404, detail="no provider profile registered")
        profile = profiles[0]
        return ProviderSelection(role=role, profile_id=profile.profile_id, model=profile.default_model)
    return ProviderSelection(role=role, profile_id=binding[0], model=binding[1])


@router.put("/providers/default", response_model=ProviderSelection)
async def set_default_provider(payload: ProviderSelection, request: Request) -> ProviderSelection:
    service = _service(request)
    try:
        profile = service.router.profile(payload.profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    model = payload.model or profile.default_model
    service.router.set_role(payload.role, profile.profile_id, model)
    save_role_binding(payload.role, profile.profile_id, model)
    return ProviderSelection(role=payload.role, profile_id=profile.profile_id, model=model)


@router.post("/providers/{profile_id}/probe")
async def probe_provider(profile_id: str, payload: ProbeRequest, request: Request) -> dict[str, Any]:
    service = _service(request)
    try:
        result = await service.router.probe(profile_id, model=payload.model)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    # 探测结果只含能力与结构化原因，不含任何密钥或对话内容
    return {
        "profile_id": profile_id,
        "status": result.get("status"),
        "kind": result.get("kind"),
        "message": result.get("message", ""),
        "capabilities": result.get("capabilities", {}),
        "secret_env_var": env_var_for(service.router.profile(profile_id).api_key_ref),
    }


@router.get("/providers/{profile_id}/models", response_model=list[str])
async def list_models(profile_id: str, request: Request) -> list[str]:
    service = _service(request)
    try:
        provider = service.router.get(profile_id)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    return await provider.list_models()
