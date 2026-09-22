"""Aggregate persisted RuntimeEvent dictionaries without contacting a provider."""

from __future__ import annotations

import csv
import json
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable


def _time(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def load_pricing(path: str | Path | None) -> dict[str, Any]:
    if not path:
        return {}
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _cost(usage: dict[str, Any], pricing: dict[str, Any], profile: str, model: str) -> float | None:
    profiles = pricing.get("profiles", {}) if isinstance(pricing, dict) else {}
    entry = profiles.get(profile, {}) if isinstance(profiles, dict) else {}
    models = entry.get("models", {}) if isinstance(entry, dict) else {}
    rates = models.get(model) if isinstance(models, dict) else None
    if not isinstance(rates, dict):
        return None
    prompt = usage.get("prompt_tokens")
    completion = usage.get("completion_tokens")
    if not isinstance(prompt, (int, float)) or not isinstance(completion, (int, float)):
        return None
    input_rate = rates.get("input_per_million")
    output_rate = rates.get("output_per_million")
    if not isinstance(input_rate, (int, float)) or not isinstance(output_rate, (int, float)):
        return None
    return (prompt * float(input_rate) + completion * float(output_rate)) / 1_000_000


def aggregate_events(events: Iterable[dict[str, Any]], pricing: dict[str, Any] | None = None) -> dict[str, Any]:
    ordered = sorted((dict(item) for item in events), key=lambda item: int(item.get("sequence", 0)))
    pricing = pricing or {}
    traces: dict[str, dict[str, Any]] = {}
    surfaces: set[str] = set()
    providers: list[dict[str, Any]] = []
    failures: list[dict[str, Any]] = []
    session_id = ""
    for event in ordered:
        trace = str(event.get("trace_id") or "")
        session_id = session_id or str(event.get("session_id") or "")
        if event.get("surface"):
            surfaces.add(str(event["surface"]))
        state = traces.setdefault(trace, {"trace_id": trace, "agent_started": None, "agent_terminal": None, "calls": []})
        timestamp = str(event.get("timestamp") or "")
        if event.get("type") == "agent.started":
            state["agent_started"] = timestamp
        elif event.get("type") == "agent.turn.completed" and str(event.get("payload", {}).get("status") or "") == "ok":
            state["agent_terminal"] = timestamp
        elif event.get("type") == "model.requested":
            payload = event.get("payload") or {}
            call = {
                "call_index": len(state["calls"]),
                "requested": timestamp,
                "first_delta": None,
                "terminal": None,
                "usage": {},
                "provider_profile": payload.get("provider_profile"),
                "model": payload.get("model"),
                "failed": False,
                "surface": event.get("surface"),
            }
            state["calls"].append(call)
            providers.append({"trace_id": trace, "call_index": call["call_index"], "provider_profile": call["provider_profile"], "model": call["model"], "surface": call["surface"]})
        elif event.get("type") == "model.stream.delta":
            calls = state["calls"]
            if calls and calls[-1]["first_delta"] is None:
                calls[-1]["first_delta"] = timestamp
        elif event.get("type") in {"model.completed", "model.failed", "agent.failed"}:
            calls = state["calls"]
            if calls:
                call = calls[-1]
                if call["terminal"] is None:
                    call["terminal"] = timestamp
                if event.get("type") == "model.completed":
                    call["usage"] = dict(event.get("payload", {}).get("usage") or {})
                else:
                    call["failed"] = True
        if event.get("type") in {"model.failed", "agent.failed"}:
            error = event.get("payload", {}).get("error") or {}
            failures.append({"trace_id": trace, "type": event.get("type"), "code": error.get("code"), "kind": error.get("details", {}).get("kind")})

    rows: list[dict[str, Any]] = []
    for state in traces.values():
        e2e = (_time(state["agent_terminal"]) - _time(state["agent_started"])).total_seconds() * 1000 if state["agent_started"] and state["agent_terminal"] else None
        calls = state["calls"] or [{"call_index": 0, "requested": None, "first_delta": None, "terminal": state["agent_terminal"], "usage": {}, "provider_profile": None, "model": None, "failed": bool(failures)}]
        for call in calls:
            requested = _time(call["requested"]) if call["requested"] else None
            first_delta = _time(call["first_delta"]) if call["first_delta"] else None
            terminal = _time(call["terminal"]) if call["terminal"] else None
            rows.append({
                "trace_id": state["trace_id"],
                "call_index": call["call_index"],
                "provider_profile": call["provider_profile"],
                "model": call["model"],
                "ttft_ms": (first_delta - requested).total_seconds() * 1000 if requested and first_delta else None,
                "provider_latency_ms": (terminal - requested).total_seconds() * 1000 if requested and terminal else None,
                "e2e_latency_ms": e2e,
                "prompt_tokens": call["usage"].get("prompt_tokens"),
                "completion_tokens": call["usage"].get("completion_tokens"),
                "total_tokens": call["usage"].get("total_tokens"),
                "estimated_cost": _cost(call["usage"], pricing, str(call["provider_profile"] or ""), str(call["model"] or "")),
                "status": "failed" if call["failed"] or any(item["trace_id"] == state["trace_id"] for item in failures) else "ok",
            })
    return {"session_id": session_id, "surfaces": sorted(surfaces), "providers": providers, "failures": failures, "traces": rows}


def write_report(result: dict[str, Any], output_dir: str | Path) -> None:
    target = Path(output_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "mvp5_metrics.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    rows = result.get("traces") or []
    if rows:
        with (target / "mvp5_metrics.csv").open("w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)
    lines = ["# MVP-5 Runtime Evaluation", "", f"Session: `{result.get('session_id', '')}`", f"Surfaces: {', '.join(result.get('surfaces') or []) or 'N/A'}", "", "| Trace | Call | Provider | Model | TTFT (ms) | Provider (ms) | E2E (ms) | Tokens | Cost | Status |", "|---|---:|---|---|---:|---:|---:|---:|---:|---|"]
    for row in rows:
        lines.append(f"| {row['trace_id']} | {row.get('call_index', 0)} | {row['provider_profile'] or ''} | {row['model'] or ''} | {row['ttft_ms'] if row['ttft_ms'] is not None else 'N/A'} | {row['provider_latency_ms'] if row['provider_latency_ms'] is not None else 'N/A'} | {row['e2e_latency_ms'] if row['e2e_latency_ms'] is not None else 'N/A'} | {row['total_tokens'] if row['total_tokens'] is not None else 'N/A'} | {row['estimated_cost'] if row['estimated_cost'] is not None else 'N/A'} | {row['status']} |")
    (target / "mvp5_report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
