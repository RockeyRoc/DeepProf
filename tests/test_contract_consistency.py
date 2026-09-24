"""契约一致性：packages/contracts 与代码必须同步。"""

from __future__ import annotations

import json
from pathlib import Path

from api.event_types import GATEWAY_EVENT_TYPES
from runtime.core.events import EventType, RuntimeEvent

CONTRACTS = Path(__file__).resolve().parents[1] / "packages" / "contracts"


def _load(name: str) -> dict:
    return json.loads((CONTRACTS / name).read_text(encoding="utf-8"))


def test_contract_files_exist():
    for name in (
        "events.json",
        "client_command.json",
        "pedagogical_decision.json",
        "capability_result.json",
        "provider_profile.json",
    ):
        assert (CONTRACTS / name).exists(), f"缺少契约文件 {name}"


def test_events_section_matches_event_type_enum():
    contract = _load("events.json")
    declared = {key for key in contract["events"] if not key.startswith("_")}
    actual = {member.value for member in EventType} | set(GATEWAY_EVENT_TYPES)
    assert declared == actual, (
        f"events.json 与 EventType 不一致；仅在契约: {sorted(declared - actual)}；"
        f"仅在代码: {sorted(actual - declared)}"
    )


def test_envelope_matches_runtime_event_dict():
    contract = _load("events.json")
    envelope = set(contract["envelope"])
    actual = set(RuntimeEvent(type="session.started").to_dict())
    assert envelope == actual, (
        f"envelope 与 RuntimeEvent.to_dict 不一致；仅在契约: {sorted(envelope - actual)}；"
        f"仅在代码: {sorted(actual - envelope)}"
    )


def test_sse_frames_reference_real_event_types():
    contract = _load("events.json")
    frames = contract["_sse_frames"]
    real = {member.value for member in EventType} | set(GATEWAY_EVENT_TYPES)
    for name in frames["passthrough"]:
        assert name in real, f"SSE 透传帧 {name} 不是真实事件类型"
    # 合成帧不是 EventType，必须显式声明在 synthetic 里
    for name in frames["synthetic"]:
        assert name not in real, f"合成帧 {name} 与 EventType 冲突"


def test_capability_result_statuses_match_dispatcher():
    from runtime import capabilities

    contract = _load("capability_result.json")
    declared = set(contract["statuses"])
    actual = {
        capabilities.STATUS_SUCCESS,
        capabilities.STATUS_INSUFFICIENT_EVIDENCE,
        capabilities.STATUS_NO_BINDING,
        capabilities.STATUS_CAPABILITY_NOT_FOUND,
        capabilities.STATUS_INVALID_REQUEST,
        capabilities.STATUS_ERROR,
    }
    assert declared == actual


def test_capability_result_fields_match_dispatcher_output():
    contract = _load("capability_result.json")
    declared = {key for key in contract["CapabilityResult"] if not key.startswith("_")}
    produced = set(
        _dispatcher_result_keys()
    )
    assert declared == produced


def _dispatcher_result_keys() -> list[str]:
    from runtime.capabilities import ActionDispatcher

    return list(ActionDispatcher._result("a", "c", "success").keys())


def test_pedagogical_decision_fields_match_contract():
    from runtime.capabilities import PRIMITIVE_NAMES

    contract = _load("pedagogical_decision.json")
    declared = set(contract["PedagogicalDecision"])
    assert {"action", "params", "reason", "require_evidence"} <= declared
    # 绑定表可用的原语集合必须与契约术语一致
    assert set(PRIMITIVE_NAMES) == {
        "render_template",
        "invoke_skill",
        "retrieve_evidence",
        "generate_grounded",
    }


def test_command_types_match_api_schema():
    from api.schemas import COMMAND_TYPES

    contract = _load("client_command.json")
    assert set(contract["command_types"]) == set(COMMAND_TYPES)


def test_provider_profile_fields_match_dataclass():
    from runtime.providers.profiles import ProviderProfile

    contract = _load("provider_profile.json")
    declared = {key for key in contract["ProviderProfile"] if not key.startswith("_")}
    actual = set(ProviderProfile(profile_id="x").to_dict())
    assert declared == actual


def test_runtime_gateway_contract_matches_cli_scope():
    contract = _load("runtime_discovery.json")
    assert contract["RuntimeGatewayRecord"]["owner"] == "cli"
