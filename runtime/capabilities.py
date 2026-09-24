"""通用能力分发器（D-7 的 HOW 侧）。

Runtime **不认识** hint/teach/ask 等教学词汇：动作名只出现在注入的绑定表里。
本模块只做四件事：查绑定、解析参数、执行通用原语、归一化结果。

失败必须显式（§7.3）：no_binding / capability_not_found / invalid_request / error(template_not_found)，
不用兜底文案掩盖装配缺陷。
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable

from runtime.core.errors import ProviderError
from runtime.core.ports import RuntimeHost

# ---- CapabilityResult.status ----
STATUS_SUCCESS = "success"
STATUS_INSUFFICIENT_EVIDENCE = "insufficient_evidence"
STATUS_NO_BINDING = "no_binding"
STATUS_CAPABILITY_NOT_FOUND = "capability_not_found"
STATUS_INVALID_REQUEST = "invalid_request"
STATUS_ERROR = "error"

# 只有只读且同轮内稳定的能力才允许备忘；有副作用的能力绝不能缓存
MEMOIZABLE = frozenset({"retrieve_evidence"})

PRIMITIVE_NAMES = (
    "render_template",
    "invoke_skill",
    "retrieve_evidence",
    "generate_grounded",
)

_PLACEHOLDER = re.compile(r"\$\{([^}]+)\}")
_LOCATOR_FIELDS = ("document_id", "chunk_id", "page", "printed_page", "chapter", "section", "source")

#: 送入模型前每条证据原文的截断长度（绑定可用 ``max_chars`` 覆盖）
DEFAULT_EVIDENCE_MAX_CHARS = 600


class SkillUnavailable(Exception):
    """Skill 没有返回可用正文：交给绑定声明的兜底文案，并带回 Skill 状态元信息。"""

    def __init__(self, message: str, *, metadata: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.metadata = dict(metadata or {})


class _TemplateNotFound(Exception):
    def __init__(self, key: str) -> None:
        super().__init__(key)
        self.key = key


class _FieldResolver:
    """从决策里解析 ``${a.b}`` 占位符；缺失字段统一收集后再报错。"""

    def __init__(self, decision: dict[str, Any]) -> None:
        self._decision = decision
        self.missing: list[str] = []

    def resolve(self, value: Any) -> Any:
        if isinstance(value, str):
            return self._resolve_str(value)
        if isinstance(value, dict):
            return {key: self.resolve(item) for key, item in value.items()}
        if isinstance(value, list):
            return [self.resolve(item) for item in value]
        return value

    def _resolve_str(self, value: str) -> Any:
        full = _PLACEHOLDER.fullmatch(value)
        if full:
            return self._lookup(full.group(1).strip())
        return _PLACEHOLDER.sub(lambda m: _stringify(self._lookup(m.group(1).strip())), value)

    def _lookup(self, path: str) -> Any:
        current: Any = self._decision
        for part in path.split("."):
            if isinstance(current, dict) and part in current:
                current = current[part]
            else:
                self.missing.append(path)
                return None
        return current


def _stringify(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)
    return str(value)


def render_block(block: Any, decision: dict[str, Any], missing: list[str] | None = None) -> str:
    """渲染绑定模板块：固定模板 或 按取值选模板。

    两种记法可以混用：``${字段}`` 从决策取值（分发器统一口径），
    ``{字段}`` 从模板块自己的 ``values`` 取值（策略文案的口径）。
    ``values`` 里的值本身也可以写成 ``${字段}``，先解析再用于填充——
    这样“绑定的数据来源”只有一个口径，不会出现两套写法。

    传入 ``missing`` 时，解析不到的字段会被收集起来，供调用方显式报错（§7.3）。
    """
    if block is None:
        return ""
    if isinstance(block, str):
        resolver = _FieldResolver(decision)
        text = str(resolver.resolve(block) or "")
        _note_missing(missing, resolver.missing)
        return text
    if not isinstance(block, dict):
        raise _TemplateNotFound(str(block))

    resolver = _FieldResolver(decision)
    values = {key: resolver.resolve(item) for key, item in (block.get("values") or {}).items()}
    scope = {**decision, **values}

    if "templates" in block:
        select = block.get("select")
        if select is None:
            raise _TemplateNotFound("")
        key = _FieldResolver(scope).resolve(str(select))
        templates = block.get("templates") or {}
        if key is None or str(key) not in templates:
            raise _TemplateNotFound(str(key))
        chosen: Any = templates[str(key)]
    else:
        chosen = block.get("template")

    if isinstance(chosen, dict):
        # 允许嵌套块（如 {"template": {...}}）
        return render_block(chosen, scope, missing)
    if chosen is None:
        raise _TemplateNotFound("template")
    resolved = str(_FieldResolver(scope).resolve(str(chosen)) or "")
    _note_missing(missing, resolver.missing)
    return format_text(resolved, values)


def _note_missing(target: list[str] | None, paths: list[str]) -> None:
    """把解析不到的字段名收集到调用方给的列表里（未提供则忽略）。"""
    if target is None:
        return
    for path in paths:
        if path not in target:
            target.append(path)


def locator_of(item: dict[str, Any]) -> dict[str, Any]:
    """把证据归一化为可定位引用，丢弃原文。"""
    locator = {key: item.get(key) for key in _LOCATOR_FIELDS if item.get(key) is not None}
    if "hash" in item:
        locator["hash"] = item["hash"]
    return locator


def is_locatable(item: dict[str, Any]) -> bool:
    """命中是否带齐可定位标识（缺一条就不算引用，§7.3 不编造引用）。"""
    return bool(
        str(item.get("document_id") or item.get("doc_id") or "")
        and str(item.get("chunk_id") or item.get("chunk") or "")
        and item.get("page") is not None
    )


class _SafeValues(dict):
    """缺键时保留 ``{key}`` 字面量：策略模板多写一个占位符不该崩掉整轮教学。"""

    def __missing__(self, key: str) -> str:
        return "{" + str(key) + "}"


def format_text(template: str, values: dict[str, Any] | None = None) -> str:
    """按 ``{字段}`` 渲染模板（策略文案用的单花括号记法）。"""
    return str(template or "").format_map(_SafeValues(dict(values or {})))


def build_evidence_block(evidence: list[dict[str, Any]], *, max_chars: int) -> str:
    """把证据**原文**拼成给模型的片段块（每段带定位，按上限截断）。

    原文只在这一次生成里使用，用完即弃；回传策略层的结果只保留可定位引用（§6.4）。
    """
    lines: list[str] = []
    for index, item in enumerate(evidence, start=1):
        text = str(item.get("text") or "")[:max_chars]
        page = item.get("page")
        locator = (
            f"document_id={item.get('document_id') or '-'} "
            f"chunk_id={item.get('chunk_id') or '-'} "
            f"page={page if page is not None else '-'}"
        )
        lines.append(f"[片段{index}] {locator}\n{text}")
    return "\n".join(lines)


def lookup_path(source: Any, path: str) -> tuple[bool, Any]:
    """按 ``a.b`` 点路径取值。"""
    current = source
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def passthrough_fields(info: dict[str, Any], names: Any) -> dict[str, Any]:
    """按白名单把 Skill 结果里的字段带回策略层（值缺失就不带，保持“没有”语义）。"""
    carried: dict[str, Any] = {}
    for name in names or []:
        key = str(name)
        if key in info:
            carried[key] = info[key]
    return carried


def pick_content(info: dict[str, Any], field: Any) -> str:
    """从 Skill 结果里取出回复正文（字段路径，或按取值选字段名的模板块）。"""
    name = render_block(field, {}) if isinstance(field, dict) else str(field or "")
    if not name:
        return ""
    found, value = lookup_path(info, name)
    return str(value or "").strip() if found else ""


@dataclass(slots=True)
class _PrimitiveResult:
    content: str = ""
    evidence: list[dict[str, Any]] = field(default_factory=list)
    records: list[dict[str, Any]] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    #: 原语自报的状态（如「检索不到可定位证据」）；留空表示 success
    status: str = ""


Primitive = Callable[
    ["ActionDispatcher", dict[str, Any], dict[str, Any], dict[str, Any], list[dict[str, Any]]],
    Awaitable[_PrimitiveResult],
]


class ActionDispatcher:
    """按注入的绑定表把声明式决策落地为一次能力执行。"""

    def __init__(self, host: RuntimeHost, bindings: dict[str, dict[str, Any]] | None = None) -> None:
        self._host = host
        self._bindings: dict[str, dict[str, Any]] = dict(bindings or {})
        self._memo: dict[str, _PrimitiveResult] = {}
        self._memo_trace: str | None = None

    # ---- 装配状态 ----

    @property
    def bindings(self) -> dict[str, dict[str, Any]]:
        return dict(self._bindings)

    def binding_summary(self) -> dict[str, str]:
        """供 /health 暴露的非敏感装配状态。"""
        return {action: str(binding.get("capability", "")) for action, binding in self._bindings.items()}

    def set_bindings(self, bindings: dict[str, dict[str, Any]]) -> None:
        self._bindings = dict(bindings)

    # ---- 主入口 ----

    async def execute(self, decision: dict[str, Any], ctx: dict[str, Any]) -> dict[str, Any]:
        action = str(decision.get("action") or "")
        binding = self._bindings.get(action)
        if binding is None:
            return self._result(action, "", STATUS_NO_BINDING, metadata={"registered_actions": sorted(self._bindings)})

        capability = str(binding.get("capability") or "")
        primitive = _PRIMITIVES.get(capability)
        if primitive is None:
            return self._result(
                action,
                capability,
                STATUS_CAPABILITY_NOT_FOUND,
                metadata={"registered_capabilities": list(PRIMITIVE_NAMES)},
            )

        self._ensure_trace(ctx)

        resolver = _FieldResolver(decision)
        params = resolver.resolve(dict(binding.get("params") or {}))
        missing = resolver.missing
        if missing:
            return self._result(
                action,
                capability,
                STATUS_INVALID_REQUEST,
                error={"code": "missing_fields", "message": f"绑定引用了不存在的字段: {missing}"},
                metadata={"missing_fields": missing},
            )

        # 证据前置：取不到可定位证据就不执行主能力、不调模型
        raw_evidence: list[dict[str, Any]] = []
        m3_evidence_options = ctx.get("m3_evidence_options") if isinstance(ctx.get("m3_evidence_options"), dict) else {}
        enforce_evidence = bool(m3_evidence_options.get("evidence_constraint", True))
        evidence_spec = binding.get("evidence")
        if evidence_spec or (decision.get("require_evidence") and enforce_evidence):
            spec = evidence_spec or {"capability": "retrieve_evidence"}
            raw_evidence = await self._collect_evidence(spec, decision, ctx)
            if not raw_evidence and enforce_evidence:
                try:
                    text = render_block(binding.get("insufficient_text"), decision)
                except _TemplateNotFound as exc:
                    return self._result(
                        action,
                        capability,
                        STATUS_ERROR,
                        error={"code": "template_not_found", "message": f"选不到模板: {exc.key}"},
                    )
                return self._result(
                    action,
                    capability,
                    STATUS_INSUFFICIENT_EVIDENCE,
                    content=text,
                    metadata={"evidence_count": 0},
                )

        block_missing: list[str] = []
        degraded_to_fallback = False
        try:
            prefix = render_block(binding.get("prefix"), decision, block_missing)
            suffix = render_block(binding.get("suffix"), decision, block_missing)
        except _TemplateNotFound as exc:
            return self._result(
                action,
                capability,
                STATUS_ERROR,
                error={"code": "template_not_found", "message": f"选不到模板: {exc.key}"},
            )
        if block_missing:
            return self._result(
                action,
                capability,
                STATUS_INVALID_REQUEST,
                error={"code": "missing_fields", "message": f"绑定引用了不存在的字段: {block_missing}"},
                metadata={"missing_fields": block_missing},
            )

        try:
            outcome = await self._run_primitive(
                capability,
                primitive,
                decision,
                params,
                ctx,
                raw_evidence,
                memo=bool(binding.get("memo", capability in MEMOIZABLE)),
            )
            status = outcome.status or STATUS_SUCCESS
        except Exception as exc:  # 主能力失败 → 确定性兜底
            fallback = binding.get("fallback")
            if fallback is None:
                return self._result(
                    action,
                    capability,
                    STATUS_ERROR,
                    error={"code": "capability_failed", "message": str(exc)},
                    metadata={"error_type": type(exc).__name__},
                )
            try:
                text = render_block(fallback, decision, block_missing)
            except _TemplateNotFound as exc2:
                return self._result(
                    action,
                    capability,
                    STATUS_ERROR,
                    error={"code": "template_not_found", "message": f"选不到兜底模板: {exc2.key}"},
                )
            if block_missing:
                return self._result(
                    action,
                    capability,
                    STATUS_INVALID_REQUEST,
                    error={"code": "missing_fields", "message": f"兜底模板引用了不存在的字段: {block_missing}"},
                    metadata={"missing_fields": block_missing},
                )
            outcome = _PrimitiveResult(content=text, metadata={"degraded_from": capability, "error": str(exc)})
            outcome.metadata.update(getattr(exc, "metadata", None) or {})
            status = STATUS_SUCCESS
            degraded_to_fallback = True

        content = "".join(part for part in (prefix, outcome.content, suffix) if part)
        merged_metadata = dict(outcome.metadata)
        # 失败已降级成兜底文案时**不附引用**（§7.3）：既然这一轮没有依据可以生成，
        # 把检索到的片段挂在这句“生成失败”旁边，只会让学生以为讲解是有出处的。
        evidence_items = [] if degraded_to_fallback else (outcome.evidence or raw_evidence)
        merged_metadata.setdefault("evidence_count", len(raw_evidence))
        merged_metadata.setdefault("evidence_attached", bool(evidence_items))
        # 跨回策略层的证据一律是**可定位引用**：原文只在本次执行内部使用（§6.4）
        return self._result(
            action,
            capability,
            status,
            content=content,
            evidence=[locator_of(item) for item in evidence_items if isinstance(item, dict)],
            records=outcome.records,
            metadata=merged_metadata,
        )

    # ---- 证据 ----

    async def _collect_evidence(
        self, spec: dict[str, Any], decision: dict[str, Any], ctx: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """执行证据能力，产出**原始**命中（含原文，供本次生成使用）。

        只保留带齐定位标识的命中：无法定位的“来源”等于不可验证的来源（§7.3）。
        """
        capability = str(spec.get("capability") or "retrieve_evidence")
        primitive = _PRIMITIVES.get(capability)
        if primitive is None:
            return []
        resolver = _FieldResolver(decision)
        params = resolver.resolve(dict(spec.get("params") or {}))
        if resolver.missing:
            return []
        try:
            outcome = await self._run_primitive(
                capability,
                primitive,
                decision,
                params,
                ctx,
                [],
                memo=bool(spec.get("memo", capability in MEMOIZABLE)),
            )
        except Exception:
            return []
        return [dict(item) for item in outcome.evidence if is_locatable(item)]

    async def _run_primitive(
        self,
        capability: str,
        primitive: Primitive,
        decision: dict[str, Any],
        params: dict[str, Any],
        ctx: dict[str, Any],
        evidence: list[dict[str, Any]],
        *,
        memo: bool,
    ) -> _PrimitiveResult:
        """执行一个原语，按需在**同一轮内**复用只读结果。

        备忘录按 ``(trace_id, 能力, 参数)`` 索引：Assess 的探路与 Teach 的取原文
        参数相同即得同一结果，既省一次向量查询，也避免“Assess 判证据充分、
        Teach 却说证据不足”的自相矛盾。有副作用的能力绝不缓存。
        """
        self._ensure_trace(ctx)
        usable = memo and capability in MEMOIZABLE and self._memo_trace is not None
        key = self._memo_key(capability, params) if usable else ""
        if usable and key in self._memo:
            cached = self._memo[key]
            return _PrimitiveResult(
                content=cached.content,
                evidence=[dict(item) for item in cached.evidence],
                records=[dict(item) for item in cached.records],
                metadata=dict(cached.metadata),
                status=cached.status,
            )
        outcome = await primitive(self, decision, params, ctx, evidence)
        if usable:
            self._memo[key] = _PrimitiveResult(
                content=outcome.content,
                evidence=[dict(item) for item in outcome.evidence],
                records=[dict(item) for item in outcome.records],
                metadata=dict(outcome.metadata),
                status=outcome.status,
            )
        return outcome

    def _memo_key(self, capability: str, params: dict[str, Any]) -> str:
        payload = json.dumps(params, ensure_ascii=False, sort_keys=True, default=str)
        return f"{self._memo_trace}|{capability}|{payload}"

    def _ensure_trace(self, ctx: dict[str, Any]) -> None:
        """同一轮内证据只检索一次；跨轮（trace_id 不同）天然隔离；无 trace_id 不备忘。"""
        trace = ctx.get("trace_id")
        if trace != self._memo_trace:
            self._memo.clear()
            self._memo_trace = trace

    # ---- 结果 ----

    @staticmethod
    def _result(
        action: str,
        capability: str,
        status: str,
        *,
        content: str = "",
        evidence: list[dict[str, Any]] | None = None,
        records: list[dict[str, Any]] | None = None,
        error: dict[str, Any] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return {
            "content": content,
            "evidence": list(evidence or []),
            "records": list(records or []),
            "status": status,
            "capability": capability,
            "action": action,
            "error": error,
            "metadata": dict(metadata or {}),
        }


# ---- 六个通用原语 ----

async def _render_template(
    dispatcher: ActionDispatcher,
    decision: dict[str, Any],
    params: dict[str, Any],
    ctx: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> _PrimitiveResult:
    """确定性渲染：绝不调用模型，保证提示强度不因换模型而漂移。

    ``params`` 本身就是一个绑定模板块（固定模板 / 按取值选模板），
    因此“哪一级提示说哪句话”永远是绑定数据，不是代码。
    """
    block: Any = params
    if not (isinstance(params, dict) and ("template" in params or "templates" in params)):
        block = {"template": params.get("text", "")}
    text = render_block(block, decision)
    return _PrimitiveResult(content=text, evidence=data_evidence(params))


async def _invoke_skill(
    dispatcher: ActionDispatcher,
    decision: dict[str, Any],
    params: dict[str, Any],
    ctx: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> _PrimitiveResult:
    """按名调用 Skill；名字由绑定数据给出，Runtime 不硬编码任何 Skill 名。

    两个可选参数让绑定足以描述“从 Skill 结果里取哪一段”：

    ``content_field``
        取哪一段作正文（支持 ``a.b`` 点路径，或按取值选字段名的模板块）；
        **不声明**时是“只问状态”的调用——Skill 被如实调用并回传结果就算成功，
        结论看 ``metadata.skill_status``（如未接入的学情模型），不当失败处理。
    ``passthrough``
        Skill 结果里要原样带回策略层的字段白名单（如题库是否接入）。
    """
    skill = str(params.get("skill") or "")
    if not skill:
        raise ValueError("invoke_skill 缺少 skill 名")
    payload = dict(params.get("input") or {})
    if evidence:
        payload.setdefault("evidence", evidence)
    info = dict(await dispatcher._host.invoke_skill(skill, payload, ctx) or {})
    status = str(info.get("status") or "")
    metadata: dict[str, Any] = {
        "skill": skill,
        "skill_status": status or "unknown",
        "source": str(info.get("source") or "skill"),
        **passthrough_fields(info, params.get("passthrough")),
    }
    records = [dict(item) for item in (info.get("records") or []) if isinstance(item, dict)]
    if "content_field" not in params:
        return _PrimitiveResult(content="", records=records, metadata=metadata)

    content = pick_content(info, params.get("content_field"))
    if status == "ok" and content:
        return _PrimitiveResult(content=content, records=records, metadata=metadata)
    raise SkillUnavailable(
        f"Skill {skill} 未返回可用内容（status={status or 'unknown'}）", metadata=metadata
    )


async def _retrieve_evidence(
    dispatcher: ActionDispatcher,
    decision: dict[str, Any],
    params: dict[str, Any],
    ctx: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> _PrimitiveResult:
    """取可定位证据：优先经检索 Skill（可做规范化/过滤），否则直接调检索 Tool。

    取不到就返回空证据——由分发器的前置门决定“不调用主能力”，
    **绝不编造来源**（§7.3）。回传的原文只在本轮能力内部使用。
    """
    skill = str(params.get("skill") or "")
    if skill:
        info = dict(await dispatcher._host.invoke_skill(skill, dict(params.get("input") or {}), ctx) or {})
        items = [
            dict(item)
            for item in (info.get("evidence") or [])
            if isinstance(item, dict) and is_locatable(item)
        ]
        status = str(info.get("status") or "")
        metadata = {
            "skill": skill,
            "retrieval_status": status or "unknown",
            "note": str(info.get("note") or ""),
        }
        if status != "ok" or not items:
            return _PrimitiveResult(evidence=[], metadata=metadata, status=STATUS_INSUFFICIENT_EVIDENCE)
        return _PrimitiveResult(evidence=items, metadata=metadata)

    tool = str(params.get("tool") or "retrieve_evidence")
    arguments = dict(params.get("arguments") or {})
    raw = await dispatcher._host.call_tool(tool, arguments, ctx)
    items = raw.get("evidence") if isinstance(raw, dict) else None
    return _PrimitiveResult(
        evidence=[dict(item) for item in (items or []) if isinstance(item, dict) and is_locatable(item)]
    )


async def _generate_grounded(
    dispatcher: ActionDispatcher,
    decision: dict[str, Any],
    params: dict[str, Any],
    ctx: dict[str, Any],
    evidence: list[dict[str, Any]],
) -> _PrimitiveResult:
    """只用给定证据生成正文：原文进提示词，回传结果只有可定位引用（§6.4）。

    提示词由绑定声明（``system_prompt`` + ``prompt_template`` + ``values``），
    其中 ``{evidence_block}`` 由本次执行填入证据原文——策略层因此既不需要
    也没有能力自己拼教材原文（§7.3 不编造引用）。
    """
    prompt_template = str(params.get("prompt_template") or "")
    if prompt_template:
        values = dict(params.get("values") or {})
        values["evidence_block"] = build_evidence_block(
            evidence, max_chars=int(params.get("max_chars") or DEFAULT_EVIDENCE_MAX_CHARS)
        )
        messages: list[dict[str, Any]] = []
        options = ctx.get("m3_evidence_options") if isinstance(ctx.get("m3_evidence_options"), dict) else {}
        unconstrained = options.get("evidence_constraint") is False
        system_prompt = str(params.get("system_prompt") or "")
        if unconstrained:
            system_prompt = "你是 DeepProf，一名面向高校学生的伴学老师。用中文回答，语气亲切严谨。不得编造出处。"
            prompt_template = prompt_template.replace("教材片段（唯一允许的依据）：", "可用教材片段：")
            prompt_template = prompt_template.replace(
                "2) 每个结论都必须能对应到上面的片段，不得引入片段之外的文献或来源；",
                "2) 说明相关条件与推理过程；",
            ).replace("不得引入片段之外的文献或来源", "不得编造来源")
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})
        messages.append({"role": "user", "content": format_text(prompt_template, values)})
    else:
        messages = list(params.get("messages") or [])

    request = {
        "role": params.get("role") or "tutor.default",
        "messages": messages,
        "model": params.get("model"),
        "max_tokens": params.get("max_tokens"),
        "temperature": params.get("temperature"),
        "tools": list(params.get("tools") or []),
    }
    parts: list[str] = []
    metadata: dict[str, Any] = {"evidence_count": len(evidence)}
    async for frame in dispatcher._host.generate(request, {**ctx, "evidence": evidence}):
        kind = frame.get("type")
        if kind == "delta":
            parts.append(str(frame.get("text", "")))
        elif kind == "error":
            error = dict(frame.get("error") or {})
            raise ProviderError(
                str(error.get("message") or "模型生成失败"),
                kind=str((error.get("details") or {}).get("kind", "unknown")),
                details=error.get("details") or {},
            )
        elif kind == "usage":
            metadata["usage"] = dict(frame.get("usage") or {})
    return _PrimitiveResult(content="".join(parts), evidence=evidence, metadata=metadata)


def data_evidence(params: dict[str, Any]) -> list[dict[str, Any]]:
    items = params.get("evidence")
    if isinstance(items, list):
        return [locator_of(item) for item in items if isinstance(item, dict)]
    return []


_PRIMITIVES: dict[str, Primitive] = {
    "render_template": _render_template,
    "invoke_skill": _invoke_skill,
    "retrieve_evidence": _retrieve_evidence,
    "generate_grounded": _generate_grounded,
}
