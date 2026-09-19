"""通用能力执行层：把声明式请求变成实际结果（DESIGNv0.4 §4.4 / §7.1）。

Runtime 在这里只提供**与教学无关的通用原语**；教学语义（用哪个 Skill、用哪段
模板、写什么提示词）全部来自调用方注入的数据（见 graph/education/bindings.py）。
因此本模块里不会出现任何教学动作名（hint / teach / ask …），
`tests/test_architecture_boundaries.py` 有对应的词汇守卫。

六个内置原语：

    render_template     确定性渲染，**绝不调用模型**——提示强度必须是可控自变量，
                        不能因为换模型而漂移（§3.4 研究问题 1）
    invoke_skill        按名调用一个 Skill；可选取正文（content_field），
                        或只问状态（不声明 content_field）
    retrieve_evidence   经检索 Skill 取可定位证据；取不到就报证据不足，绝不编造来源（§7.3）
    generate_grounded   只用给定证据生成正文，并回传可定位引用（不含原文，§6.4）
    read_memory         读学情记忆（身份取调用上下文，检索口径来自绑定）
    write_memory        写学情增量（记录由策略层构造，§5.5）

这些原语拿到的是 **RuntimeHost（宽面）**——图节点拿不到它，
所以"图只声明 WHAT"不靠自觉，靠类型与守卫（见 runtime/core/ports.py）。

绑定数据里的字符串支持 ``${字段}`` 占位，取值来自请求 dict（支持 ``a.b`` 路径）。
整串占位（``"${level}"``）保留原类型，内嵌占位（``"第 ${level} 级"``）按文本插值。
引用到不存在的字段会记入 ``missing``，由分发器显式失败——绑定写错必须响，
不能悄悄给模型喂一句字面量 ``${params.query}``。

分发器（``ActionDispatcher``）是这两者的黏合层，它同样不认识教学动作，
只按注入的绑定表把请求翻成通用能力调用。
"""
from __future__ import annotations

import json
import re
from collections import OrderedDict
from typing import Any, Protocol, runtime_checkable

from .core.ports import RuntimeHost

#: 同一轮内证据检索的备忘上限：按 trace 保留最近若干轮，避免长期运行下无界增长。
#: 16 足够覆盖并发中同时在跑的会话数级（每轮结束即不再被命中）。
EVIDENCE_MEMO_TRACES = 16

# ======================================================================
# 一、结果构造（字段与 graph/education/contracts.py 的 CapabilityResult 对齐）
# ======================================================================
def capability_result(
    *,
    status: str,
    content: str = "",
    evidence: list[dict[str, Any]] | None = None,
    records: list[dict[str, Any]] | None = None,
    action: str = "",
    capability: str = "",
    error: dict[str, Any] | None = None,
    metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """构造 CapabilityResult 的 dict 形态（端口只传 dict，§7.1）。"""
    return {
        "status": status,
        "content": content,
        "evidence": list(evidence or []),
        "records": list(records or []),
        "action": action,
        "capability": capability,
        "error": error,
        "metadata": dict(metadata or {}),
    }


def _failed(
    status: str, code: str, message: str, **metadata: Any
) -> dict[str, Any]:
    return capability_result(
        status=status,
        error={"code": code, "message": message},
        metadata=metadata,
    )


# ======================================================================
# 二、Capability 协议与注册表
# ======================================================================
@runtime_checkable
class Capability(Protocol):
    """一个可被通用分发的执行单元：参数与结果都是 dict，不认识教学词汇。"""

    name: str

    async def execute(self, params: dict, ctx: dict, port: RuntimeHost) -> dict:
        """执行并返回 ``{"status", "content", "evidence", "records", "metadata", "error"}``。"""


class CapabilityRegistry:
    """进程内能力注册表（名字是纯字符串键，注册表不理解其语义）。"""

    def __init__(self) -> None:
        self._capabilities: dict[str, Capability] = {}

    def register(self, capability: Capability) -> Capability:
        name = str(getattr(capability, "name", "") or "")
        if not name:
            raise ValueError("Capability 必须有 name")
        if name in self._capabilities:
            raise ValueError(f"Capability 名称重复: {name}")
        self._capabilities[name] = capability
        return capability

    def get(self, name: str) -> Capability | None:
        return self._capabilities.get(str(name or ""))

    def has(self, name: str) -> bool:
        return str(name or "") in self._capabilities

    def names(self) -> list[str]:
        return sorted(self._capabilities)


# ======================================================================
# 三、占位解析（绑定数据 → 能力参数）
# ======================================================================
_PLACEHOLDER = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_.]*)\}")


def resolve_placeholders(
    value: Any, source: dict[str, Any], missing: list[str] | None = None
) -> Any:
    """把绑定数据里的 ``${字段}`` 换成请求里的真实取值（递归 dict / list）。"""
    if isinstance(value, str):
        exact = _PLACEHOLDER.fullmatch(value)
        if exact:
            found, resolved = _lookup(source, exact.group(1))
            if not found:
                _note_missing(missing, exact.group(1))
                return ""
            return resolved
        return _PLACEHOLDER.sub(
            lambda match: _interpolate(source, match.group(1), missing), value
        )
    if isinstance(value, dict):
        return {key: resolve_placeholders(item, source, missing) for key, item in value.items()}
    if isinstance(value, list):
        return [resolve_placeholders(item, source, missing) for item in value]
    return value


def _lookup(source: Any, path: str) -> tuple[bool, Any]:
    current = source
    for part in str(path).split("."):
        if isinstance(current, dict) and part in current:
            current = current[part]
        else:
            return False, None
    return True, current


def _interpolate(source: dict[str, Any], path: str, missing: list[str] | None) -> str:
    found, resolved = _lookup(source, path)
    if not found:
        _note_missing(missing, path)
        return "${" + path + "}"  # 保留原样，便于在最终文本里定位写错的绑定
    return "" if resolved is None else str(resolved)


def _note_missing(missing: list[str] | None, path: str) -> None:
    if missing is not None:
        missing.append(str(path))


# ======================================================================
# 四、通用小工具（渲染、证据、流式收集）
# ======================================================================
class _SafeValues(dict):
    """缺键时保留 ``{key}`` 字面量，避免策略模板多写一个占位符就崩掉整轮教学。"""

    def __missing__(self, key: str) -> str:
        return "{" + str(key) + "}"


def render_text(template: str, values: dict[str, Any] | None = None) -> str:
    """确定性模板渲染（支持 ``fallback`` / ``render_template`` 两处复用）。"""
    return str(template or "").format_map(_SafeValues(dict(values or {})))


def render_bound_block(bound: Any) -> str | None:
    """渲染一个**绑定模板块**；找不到模板时返回 None（由调用方决定是报错还是省略）。

    绑定模板块只有两种形态，``fallback`` / ``prefix`` / ``suffix`` /
    ``insufficient_text`` 以及 ``render_template`` 能力都复用同一套语义，
    因此"加一段可评审文案"永远是加数据，不是加代码：

        {"template": "...", "values": {...}}                        固定模板
        {"templates": {...}, "select": "<字段>", "values": {...}}   按取值选模板

    选模板用的键来自 ``values`` 里 ``select`` 指名的那个字段——它通常由
    ``${...}`` 占位从决策请求解析而来，所以"选哪段文案"这条规则写在绑定里，
    而 Runtime 依然不知道那些取值（"correct" / "2"）是什么意思。
    """
    if not isinstance(bound, dict):
        return None
    values = dict(bound.get("values") or {})
    template = bound.get("template")
    if template is None:
        select = str(bound.get("select") or "")
        template = _pick_template(
            dict(bound.get("templates") or {}), values.get(select) if select else ""
        )
    if template is None:
        return None
    return render_text(str(template), values)


def _uses_selection(bound: Any) -> bool:
    """该模板块是否声明了"按取值选模板"（用于区分"没声明"与"选了但没选到"）。"""
    return isinstance(bound, dict) and "templates" in bound


def _wrap(content: str, resolved: dict) -> str:
    """按绑定给正文加上 prefix / suffix（可选；到这一步已校验过 select 能选到）。"""
    prefix = render_bound_block(resolved.get("prefix")) or ""
    suffix = render_bound_block(resolved.get("suffix")) or ""
    return f"{prefix}{content}{suffix}"


def is_locatable(evidence: dict[str, Any]) -> bool:
    """命中是否带齐可定位标识（document_id / chunk_id / page，§18.2 Evidence 契约）。

    该不变量原先只写在图节点的证据获取里（§7.3 不编造引用）；
    证据获取下沉到能力层后，判定也随之下沉，图与能力层共用同一条规则。
    """
    return bool(
        str(evidence.get("document_id") or evidence.get("doc_id") or "")
        and str(evidence.get("chunk_id") or evidence.get("chunk") or "")
        and evidence.get("page") is not None
    )


def locator_ref(evidence: dict[str, Any]) -> dict[str, Any]:
    """把一条证据压成可进状态的引用：只保留定位字段，丢弃原文（§6.4）。"""
    document_id = str(evidence.get("document_id") or evidence.get("doc_id") or "")
    chunk_id = str(evidence.get("chunk_id") or evidence.get("chunk") or "")
    page = evidence.get("page")
    return {
        "evidence_id": str(
            evidence.get("evidence_id") or f"{document_id}#{chunk_id}@{page}"
        ),
        "document_id": document_id,
        "chunk_id": chunk_id,
        "page": page,
        "source": str(evidence.get("source") or ""),
    }


def build_evidence_block(evidence: list[dict[str, Any]], *, max_chars: int) -> str:
    """把证据原文拼成给模型的片段块，并按上限截断（避免超长与隐私复制）。"""
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


async def collect_stream(port: RuntimeHost, request: dict, ctx: dict) -> str:
    """消费 port.generate 的流式增量，返回完整文本（任何失败返回空串，§7.3）。"""
    parts: list[str] = []
    final = ""
    async for chunk in port.generate(request, ctx):
        kind = str(chunk.get("type") or "")
        if kind == "delta":
            parts.append(str(chunk.get("text") or ""))
        elif kind == "done":
            final = str(chunk.get("content") or "")
        elif kind == "error":
            return ""
    return final or "".join(parts)


def _pick_template(templates: dict, key: Any) -> Any:
    """按选中键取模板。

    键在 JSON 往返或 ``str(bool)`` 之后可能是 ``"2"`` / ``"True"``，
    此处做两次归一化（原样 → 小写 → 数字），避免绑定只因为大小写就失效。
    """
    if not isinstance(templates, dict) or not templates:
        return None
    if key in templates:
        return templates[key]
    text = str(key)
    for candidate in (text, text.lower()):
        if candidate in templates:
            return templates[candidate]
    if text.isdigit() and int(text) in templates:
        return templates[int(text)]
    return None


# ======================================================================
# 五、四个内置原语
# ======================================================================
class TemplateCapability:
    """确定性渲染：不调用模型，保证提示强度可复现、可评审。

    params 本身就是一个绑定模板块（见 render_bound_block）；选不到模板时必须
    **显式失败**，不能给一段空文案——空提示和"没给提示"对学生是同一件事，
    对审计却完全不同。
    """

    name = "render_template"

    async def execute(self, params: dict, ctx: dict, port: RuntimeHost) -> dict:
        text = render_bound_block(params)
        if text is None:
            select = str(params.get("select") or "")
            key = dict(params.get("values") or {}).get(select)
            return _failed(
                "error",
                "template_not_found",
                f"绑定数据里没有可用的模板（select={select or '-'} key={key!r}）",
            )
        return {
            "status": "success",
            "content": text,
            "metadata": {
                "select": str(params.get("select") or ""),
                "key": str(dict(params.get("values") or {}).get(params.get("select"))),
            },
        }


class SkillCapability:
    """按名调用 Skill；名字由绑定数据给出，Runtime 不硬编码任何 Skill 名。

    两个可选参数让绑定足以描述"从 Skill 结果里取哪一段"：

    content_field
        取哪一段作为回复正文，支持 ``a.b`` 点路径（如 ``item.stem``）。
        也可以是一个绑定模板块——按决策取值选字段名（Test 在出题/评价两种模式下
        读的是 Skill 结果的不同位置），于是"哪种模式读哪里"由绑定声明。

        **声明了它 = "我需要正文"**：取不到就判为失败，好让绑定的 fallback 接手。
        **不声明 = "我只问状态"**（如 Diagnosis 只回 not_implemented）：调用本身
        完成即算成功，结论看 metadata.skill_status 与透传字段。
    passthrough
        Skill 结果里**要原样带回决策层**的字段白名单（如
        ``item_bank_connected``）。白名单之外一律不带回——能力层不该把自己 casing
        的全部内部结构倒给策略层。
    """

    name = "invoke_skill"

    async def execute(self, params: dict, ctx: dict, port: RuntimeHost) -> dict:
        skill = str(params.get("skill") or "")
        if not skill:
            return _failed("invalid_request", "missing_skill", "invoke_skill 缺少 skill 名")
        payload = dict(params.get("input") or {})
        info = dict(await port.invoke_skill(skill, payload, ctx) or {})
        status = str(info.get("status") or "")
        metadata: dict[str, Any] = {
            "skill": skill,
            "skill_status": status or "unknown",
            "source": str(info.get("source") or "skill"),
            **_passthrough(info, params.get("passthrough")),
        }
        if "content_field" not in params:
            # 只问状态：Skill 被如实调用并回传了结果，就算这次调用成功；
            # "它自己回的是 not_implemented 还是 ok"由 skill_status 表达，
            # 决策事件里能看到，不需要在这一层替策略层下判断。
            return {"status": "success", "content": "", "metadata": metadata}

        content = _pick_content(info, params.get("content_field"))
        if status == "ok" and content:
            return {"status": "success", "content": content, "metadata": metadata}
        return {
            "status": "error",
            "content": "",
            "error": {
                "code": "skill_unavailable",
                "message": f"Skill {skill} 未返回可用内容（status={status or 'unknown'}）",
            },
            "metadata": metadata,
        }


def _pick_content(info: dict[str, Any], field: Any) -> str:
    """从 Skill 结果里取出回复正文（字段路径，或按取值选字段名的模板块）。"""
    name = render_bound_block(field) if isinstance(field, dict) else str(field or "")
    if not name:
        return ""
    found, value = _lookup(info, name)
    return str(value or "").strip() if found else ""


def _passthrough(info: dict[str, Any], names: Any) -> dict[str, Any]:
    """按白名单把 Skill 结果里的字段带回策略层（值缺失就不带，保持"没有"语义）。"""
    carried: dict[str, Any] = {}
    for name in names or []:
        key = str(name)
        if key in info:
            carried[key] = info[key]
    return carried


class MemoryReadCapability:
    """读学情记忆：身份取自调用上下文，检索口径来自绑定（§5.5 / §7.1）。

    身份（learner_id）是每次调用都有的上下文事实，因此从 ctx 取，不要求决策
    再抄一遍；"查哪一类记忆、查多少条、key 用什么"才是策略，由绑定声明。
    """

    name = "read_memory"

    async def execute(self, params: dict, ctx: dict, port: RuntimeHost) -> dict:
        query = dict(params.get("query") or {})
        query.setdefault("learner_id", str((ctx or {}).get("learner_id") or ""))
        records = [
            dict(item)
            for item in (await port.read_memory(query, ctx) or [])
            if isinstance(item, dict)
        ]
        return {
            "status": "success",
            "content": "",
            "records": records,
            "metadata": {"count": len(records)},
        }


class MemoryWriteCapability:
    """写学情增量：记录由策略层构造（来源、置信度、过期策略、可撤回都在里面）。

    空列表直接算成功且不写盘——"本轮没有可写的增量"与"写入 0 条"是同一件事，
    不必为此多走一次存储（§5.5 缺 learner_id 时宁可不写）。
    """

    name = "write_memory"

    async def execute(self, params: dict, ctx: dict, port: RuntimeHost) -> dict:
        records = [
            dict(item) for item in (params.get("records") or []) if isinstance(item, dict)
        ]
        if records:
            await port.write_memory(records, ctx)
        return {
            "status": "success",
            "content": "",
            "metadata": {"written": len(records)},
        }


class EvidenceRetrievalCapability:
    """经检索 Skill 取可定位证据；取不到就报证据不足，绝不编造来源（§7.3）。"""

    name = "retrieve_evidence"

    async def execute(self, params: dict, ctx: dict, port: RuntimeHost) -> dict:
        skill = str(params.get("skill") or "")
        if not skill:
            return _failed(
                "invalid_request", "missing_skill", "retrieve_evidence 缺少检索 Skill 名"
            )
        info = dict(await port.invoke_skill(skill, dict(params.get("input") or {}), ctx) or {})
        status = str(info.get("status") or "")
        evidence = [
            item
            for item in (info.get("evidence") or [])
            if isinstance(item, dict) and is_locatable(item)
        ]
        metadata = {"skill": skill, "retrieval_status": status, "note": str(info.get("note") or "")}
        if status != "ok" or not evidence:
            return {
                "status": "insufficient_evidence",
                "content": "",
                "evidence": [],
                "metadata": metadata,
            }
        return {
            "status": "success",
            "content": "",
            "evidence": evidence,
            "metadata": metadata,
        }


class GroundedGenerationCapability:
    """只用给定证据生成正文，并回传可定位引用（原文不进结果，§6.4）。"""

    name = "generate_grounded"

    async def execute(self, params: dict, ctx: dict, port: RuntimeHost) -> dict:
        evidence = [item for item in (params.get("evidence") or []) if isinstance(item, dict)]
        if not evidence:
            return _failed(
                "insufficient_evidence",
                "evidence_required",
                "generate_grounded 只在有证据时生成，禁止无证据自由发挥（§7.3）",
            )
        values = dict(params.get("values") or {})
        values["evidence_block"] = build_evidence_block(
            evidence, max_chars=int(params.get("max_chars") or 600)
        )
        request = {
            "messages": [
                {"role": "system", "content": str(params.get("system_prompt") or "")},
                {"role": "user", "content": render_text(str(params.get("prompt_template") or ""), values)},
            ],
            "temperature": params.get("temperature"),
            "stream": True,
            "metadata": dict(params.get("metadata") or {}),
        }
        content = await collect_stream(port, request, ctx)
        if not content:
            return _failed(
                "error",
                "generation_failed",
                "模型未返回内容或流式生成失败；调用方应改用确定性失败表述",
            )
        return {
            "status": "success",
            "content": content,
            "evidence": [locator_ref(item) for item in evidence],
            "metadata": {"evidence_count": len(evidence)},
        }


def default_capabilities() -> CapabilityRegistry:
    """装配六个通用原语；它们都不含教学语义，因此可以默认注册。

    (render_template / invoke_skill / retrieve_evidence / generate_grounded
     / read_memory / write_memory)
    """
    registry = CapabilityRegistry()
    for capability in (
        TemplateCapability(),
        SkillCapability(),
        EvidenceRetrievalCapability(),
        GroundedGenerationCapability(),
        MemoryReadCapability(),
        MemoryWriteCapability(),
    ):
        registry.register(capability)
    return registry


# ======================================================================
# 六、通用分发器
# ======================================================================
class ActionDispatcher:
    """查绑定表 → 按名解析 capability → 执行 → 归一化结果（§4.4）。

    它住在 runtime 侧，却不认识任何教学动作：``hint`` / ``teach`` / ``ask``
    这些词只出现在**注入的绑定数据**里（graph/education/bindings.py）。
    若把映射写成这里的 if/elif，只是把耦合从结构搬到了词汇，边界并未立起来。

    一个绑定可以做四件事：执行一个通用能力、按需先取证据（require_evidence）、
    给正文加 prefix / suffix 段落、在失败时改用确定性 fallback。这四件事都不看
    动作名，只看绑定里写了什么。

    装配失败一律显式失败，不静默兜底（不要用"自由发挥的文本"掩盖漏装配）：
        no_binding / capability_not_found / invalid_request / template_not_found
    """

    def __init__(
        self,
        capabilities: CapabilityRegistry,
        bindings: dict[str, dict] | None = None,
        *,
        port: RuntimeHost | None = None,
    ) -> None:
        self.capabilities = capabilities
        self.bindings: dict[str, dict] = {}
        self._port = port
        self._evidence_memo: OrderedDict[str, dict[str, dict]] = OrderedDict()
        self.set_bindings(bindings or {})

    def set_bindings(self, bindings: dict[str, dict]) -> None:
        """替换绑定表；绑定是数据，随时可由组合根重装（换人才、换课程都能改表）。"""
        self.bindings = {
            str(key): dict(value) for key, value in dict(bindings or {}).items()
        }

    async def execute(self, request: dict, ctx: dict) -> dict:
        payload = dict(request or {})
        action = str(payload.get("action") or "")
        binding = self.bindings.get(action)
        if binding is None:
            return capability_result(
                status="no_binding",
                action=action,
                error={
                    "code": "no_binding",
                    "message": f"没有为动作 {action!r} 注入绑定（组合根未装配）",
                },
                metadata={"known_actions": sorted(self.bindings)},
            )

        missing: list[str] = []
        resolved = resolve_placeholders(binding, payload, missing)
        if missing:
            return capability_result(
                status="invalid_request",
                action=action,
                error={
                    "code": "missing_fields",
                    "message": f"绑定引用了请求里不存在的字段: {sorted(set(missing))}",
                },
                metadata={"missing_fields": sorted(set(missing))},
            )

        name = str(resolved.get("capability") or "")
        capability = self.capabilities.get(name)
        if capability is None:
            return capability_result(
                status="capability_not_found",
                action=action,
                capability=name,
                error={"code": "capability_not_found", "message": f"能力未注册: {name}"},
                metadata={"known_capabilities": self.capabilities.names()},
            )

        params = dict(resolved.get("params") or {})
        metadata: dict[str, Any] = {}

        # require_evidence 是分发器的通用前置条件（不看动作名，只看这个布尔）：
        # 缺证据时不执行主能力、不调用模型，改用绑定声明的确定性表述（§7.3）。
        if payload.get("require_evidence"):
            evidence, retrieval_meta = await self._fulfil_evidence(payload, resolved, ctx)
            metadata.update(retrieval_meta)
            if not evidence:
                return capability_result(
                    status="insufficient_evidence",
                    content=render_bound_block(resolved.get("insufficient_text")) or "",
                    action=action,
                    capability=name,
                    metadata=metadata,
                )
            params["evidence"] = evidence
            metadata["evidence_count"] = len(evidence)

        # prefix / suffix：围绕正文的固定或按取值选择的段落（开头框架、结尾说明）。
        # 它们和 fallback 共用"绑定模板块"语义；声明了 select 却选不到模板时
        # 必须显式失败——悄悄少一段会让学生看到半截回复，且没人会发现。
        for key in ("prefix", "suffix"):
            block = resolved.get(key)
            if _uses_selection(block) and render_bound_block(block) is None:
                select = str(block.get("select") or "")
                return capability_result(
                    status="error",
                    action=action,
                    capability=name,
                    error={
                        "code": "template_not_found",
                        "message": (
                            f"{key} 没选到模板（select={select or '-'} "
                            f"key={dict(block.get('values') or {}).get(select)!r}）"
                        ),
                    },
                )

        outcome = await self._execute_capability(
            name, params, ctx, memo=bool(resolved.get("memo"))
        )
        status = str(outcome.get("status") or "error")
        content = str(outcome.get("content") or "")
        # 出站边界（§6.4 / §7.3）：能力内部可以带证据原文（当次生成用完即弃），
        # 但跨回策略层的结果只保留可定位引用，绝不携带原文——
        # 否则 retrieved_evidence_refs 这类状态字段会把教材正文复制进来。
        evidence = [
            locator_ref(item)
            for item in (outcome.get("evidence") or [])
            if isinstance(item, dict)
        ]
        # records 是结构化记录（学情记忆），不是教材原文，因此原样带回；
        # 由策略层决定怎么解释（如 record_id 与误解标签）。
        records = [item for item in (outcome.get("records") or []) if isinstance(item, dict)]
        metadata = {**metadata, **dict(outcome.get("metadata") or {})}

        # fallback 是策略侧声明的确定性兜底：宁可话术朴素，也不让模型自由发挥。
        # 声明了"按取值选模板"却选不到时同样显式失败——静默跳过兜底会让
        # 学生收到一句空话，而绑定作者以为自己已经配了兜底。
        fallback = resolved.get("fallback")
        if status != "success" and isinstance(fallback, dict):
            text = render_bound_block(fallback)
            if text is None:
                return capability_result(
                    status="error",
                    action=action,
                    capability=name,
                    error={
                        "code": "template_not_found",
                        "message": (
                            "fallback 没选到模板（select="
                            f"{fallback.get('select') or '-'} "
                            f"key={dict(fallback.get('values') or {}).get(fallback.get('select'))!r}）"
                        ),
                    },
                    metadata=metadata,
                )
            content = text
            evidence = []
            metadata["degraded_from"] = status
            status = "success"

        if status == "success":
            content = _wrap(content, resolved)

        return capability_result(
            status=status,
            content=content,
            evidence=evidence,
            records=records,
            action=action,
            capability=name,
            error=outcome.get("error"),
            metadata=metadata,
        )

    async def _fulfil_evidence(
        self, payload: dict, resolved: dict, ctx: dict
    ) -> tuple[list[dict], dict]:
        """满足 require_evidence：返回（可定位证据, 元信息），空列表表示未满足。

        两条判断顺序有意如此：
        1. 决策已声明本轮无证据（策略层判过 Assess 的结论）→ **不再硬找**，
           省掉一次检索，也避免"证据不足还要试一下"的含糊语义；
        2. 声明有证据时，必须真的取到**带可定位标识**的命中才算数——
           策略层的判断不能替代分发器对证据的实际校验（§7.3 不编造引用）。
        """
        if not payload.get("evidence_sufficient"):
            return [], {"evidence_skipped": "declared_insufficient"}
        bound = resolved.get("evidence")
        if not isinstance(bound, dict):
            return [], {"evidence_skipped": "no_evidence_source"}
        retrieval = await self._execute_bound(bound, ctx)
        meta = {"retrieval": dict(retrieval.get("metadata") or {})}
        if str(retrieval.get("status") or "") != "success":
            return [], meta
        return [
            item for item in (retrieval.get("evidence") or []) if isinstance(item, dict)
        ], meta

    async def _execute_capability(
        self, name: str, params: dict, ctx: dict, *, memo: bool = False
    ) -> dict:
        """执行一个能力；``memo=True`` 时在同一轮内按 (能力名, 参数) 复用结果。

        为什么要备忘：Assess 先探一次证据来定 `evidence_sufficient`，Teach / Correct
        随后还要取原文来生成——参数完全相同，结果理应相同。重复检索的代价不只是
        多一次查询：两次结果若不一致，就会出现"Assess 判证据充分、Teach 却说证据不足"。

        `memo` 由**绑定声明**（`"memo": true`），只应用于只读且同轮内结果稳定的能力
        （目前只有证据检索）。不声明的能力一律真调用——写学情、调模型这类有副作用的
        能力绝不能缓存，所以默认值是"不备忘"。

        作用域刻意是**单轮**：键含 trace_id，跨轮不复用，不会拿到上一轮的陈旧证据；
        `ctx` 没有 trace_id 时不备忘（无法判断是否同一轮，宁可多查一次）。
        """
        if not memo:
            return await self._run_capability(name, params, ctx)
        bucket = self._memo_for(str((ctx or {}).get("trace_id") or ""))
        spec = json.dumps(
            {"capability": name, "params": params},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )
        if bucket is not None and spec in bucket:
            return dict(bucket[spec])
        outcome = await self._run_capability(name, params, ctx)
        if bucket is not None:
            bucket[spec] = dict(outcome)
        return outcome

    def _memo_for(self, trace_id: str) -> dict[str, dict] | None:
        """取该 trace 的检索备忘桶（LRU，保留最近 EVIDENCE_MEMO_TRACES 轮）。"""
        if not trace_id:
            return None
        bucket = self._evidence_memo.get(trace_id)
        if bucket is not None:
            self._evidence_memo.move_to_end(trace_id)
            return bucket
        bucket = {}
        self._evidence_memo[trace_id] = bucket
        while len(self._evidence_memo) > EVIDENCE_MEMO_TRACES:
            self._evidence_memo.popitem(last=False)
        return bucket

    async def _run_capability(self, name: str, params: dict, ctx: dict) -> dict:
        """按名解析并执行能力；未注册返回结构化失败（不抛异常打断图）。"""
        capability = self.capabilities.get(name)
        if capability is None:
            return {
                "status": "capability_not_found",
                "content": "",
                "evidence": [],
                "metadata": {"capability": name},
            }
        return dict(await capability.execute(dict(params or {}), ctx, self._port) or {})

    async def _execute_bound(self, bound: dict, ctx: dict) -> dict:
        """执行绑定里的子能力（如证据获取），参数已由分发器解析完毕。"""
        return await self._execute_capability(
            str(bound.get("capability") or ""),
            dict(bound.get("params") or {}),
            ctx,
            memo=bool(bound.get("memo")),
        )


__all__ = [
    "ActionDispatcher",
    "Capability",
    "CapabilityRegistry",
    "EvidenceRetrievalCapability",
    "GroundedGenerationCapability",
    "MemoryReadCapability",
    "MemoryWriteCapability",
    "SkillCapability",
    "TemplateCapability",
    "build_evidence_block",
    "capability_result",
    "collect_stream",
    "default_capabilities",
    "is_locatable",
    "locator_ref",
    "render_bound_block",
    "render_text",
    "resolve_placeholders",
]