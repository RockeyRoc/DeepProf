"""契约一致性守护测试（shared/contracts/events.json ↔ 代码事实标准）。

背景：``shared/contracts/events.json`` 是前后端事件契约，但它**不被任何代码
运行时加载**（纯 JSON 文档），改坏不会炸任何测试 —— 契约与代码只能靠人肉对账。
历史上已经漂移过两次：

1. ``pedagogy.attempt``：代码 ``EventType.PEDAGOGY_ATTEMPT`` 早已存在（Test / Correct
   节点产出的作答事实），契约却漏记 —— 照契约集成的人拿不到数据组的交接事件；
2. ``agent.cancelled``：Mock 与真后端各发一个事件名，契约只记了 Mock 的
   （该分歧已于 2026-09-21 解决，见 events.json changelog 1.2.0 -> 1.2.1）。

本测试的策略与 ``test_config_consistency.py`` 相同：以代码为事实标准
（``runtime/core/events.py`` 的 ``EventType`` 枚举 + ``api/routes/sessions.py``
的合成帧），契约的事件名集合必须与之**完全相等**；已知且有意为之的差异显式
登记在 ``KNOWN_DIVERGENCES``，其余任何差异都判失败；已修复却仍挂在例外表里的
过期条目也判失败，避免例外表失真后失去守护作用。
"""
from __future__ import annotations

import json
from pathlib import Path

from runtime.core.events import EventType, RuntimeEvent

CONTRACT = Path(__file__).resolve().parents[1] / "shared" / "contracts" / "events.json"

# api 层合成的收尾帧：不在 EventType 枚举里，由 sessions.py 定义
from api.routes.sessions import _RESULT_EVENT_TYPE  # noqa: E402

#: 代码侧的事件全集：EventType 枚举 + api 合成帧
CODE_EVENT_TYPES: set[str] = {e.value for e in EventType} | {_RESULT_EVENT_TYPE}

# 已知且有意为之的差异：事件名 -> 原因。
# 当前已无差异——契约 events 段应与代码事件全集完全一致。
# 若确有无法通过"改成一致"解决的差异，在此登记并写明原因；
# 一旦该差异被修复，必须同步删除条目，否则本测试会判失败。
KNOWN_DIVERGENCES: dict[str, str] = {}


def _contract_event_names() -> set[str]:
    """契约 events 段的事件名集合。

    键名以 ``_`` 开头的是元数据段（如 ``_插件事件公共payload``，登记 6 条
    plugin 事件共用的 payload 形状），不是事件，须排除 —— 与 proposed_events
    段的 ``_说明`` 同一套约定。
    """
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    return {name for name in contract["events"] if not name.startswith("_")}


def test_contract_event_names_match_code():
    """契约 events 段的事件名集合必须与代码事件全集完全相等（双向零缺口）。"""
    contract_names = _contract_event_names()

    missing_in_contract = sorted(CODE_EVENT_TYPES - contract_names - set(KNOWN_DIVERGENCES))
    stale_in_contract = sorted(contract_names - CODE_EVENT_TYPES - set(KNOWN_DIVERGENCES))
    assert not missing_in_contract, (
        f"代码有、契约漏记（请补进 events.json，或登记进 KNOWN_DIVERGENCES 并写明原因）: "
        f"{missing_in_contract}"
    )
    assert not stale_in_contract, (
        f"契约有、代码没有（契约里的事件没有任何生产方，请核实后删除或实现）: "
        f"{stale_in_contract}"
    )
    stale_exception = sorted(set(KNOWN_DIVERGENCES) - (CODE_EVENT_TYPES ^ contract_names))
    assert not stale_exception, f"例外表已过期（差异已修复，请从 KNOWN_DIVERGENCES 移除）: {stale_exception}"


def test_contract_envelope_matches_runtime_event_shape():
    """契约 envelope 的字段集合必须与 RuntimeEvent.to_dict() 的键一致（§5.4 / §18.2）。

    envelope 是每条事件的公共外壳：前端靠它去重（event_id + sequence），
    数据组靠它幂等写入与断线对账。文档两处（§5.4 与 §18.2）曾各说一半，
    代码取 8 字段并集 —— 本测试锁死「契约 envelope == 代码实际形状」。
    """
    contract = json.loads(CONTRACT.read_text(encoding="utf-8"))
    envelope_fields = set(contract["envelope"]["fields"])
    code_fields = set(RuntimeEvent(type="").to_dict())

    assert envelope_fields == code_fields, (
        f"envelope 漂移：契约多出 {sorted(envelope_fields - code_fields)}，"
        f"代码多出 {sorted(code_fields - envelope_fields)}"
    )


def test_contract_sse_event_names_all_mapped():
    """SSE 事件名映射表里的每个键都必须是真实事件类型（防止改错枚举名后静默失效）。

    ``_SSE_EVENT_NAMES`` 是「Runtime 事件类型 → SSE event 名」的映射，键写错
    （如枚举改名后忘了同步）不会报错，只会让该事件落到默认名 ``event``。
    """
    from api.routes.sessions import _SSE_EVENT_NAMES

    unknown = sorted(set(_SSE_EVENT_NAMES) - CODE_EVENT_TYPES)
    assert not unknown, f"_SSE_EVENT_NAMES 里有不是事件类型的键: {unknown}"
