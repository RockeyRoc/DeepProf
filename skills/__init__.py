"""教育能力（Skill）层（DESIGNv0.4 §5.3 / §6.3 / §9）。

Skill = "怎么完成一类教学任务"的方法与流程，不拥有底层权限：
需要模型、工具、记忆时一律通过 RuntimePort 申请，需要上下文时读 RuntimeContext。

本包提供五个教育能力，与 §6.3 教育能力节点一一对应：

    socratic     苏格拉底递进提问（孙一新）
    rag          教材检索与可追踪来源（许阳毅）
    quiz         出题与评价（孙一新）
    diagnosis    学情诊断（欧阳文凯）
    paper_reader 论文结构化理解与讨论（许阳毅）

**诚实原则**（§12 / §16.3 验收）：题库、向量库、学情模型这些尚未接入的能力
不得假装实现——对应 Skill 返回 {"status": "not_implemented", ...} 并说明缺什么、由谁补。

对外入口：
    from api.deps import configure_runtime
    configure_runtime(service)      # 一次装配工具、Skill 与教学绑定表（幂等）

**不要**写 ``register_default_skills(RuntimeService().skills)``：那会把 Skill 注册进
一个随即被丢弃的实例。装配是组合根的职责，见 DESIGNv0.4 §9.1。
"""
from __future__ import annotations

from typing import Any

from runtime.core.ports import RuntimeContext, RuntimePort
from runtime.skills import Skill, SkillDescriptor, SkillRegistry


def to_ctx_dict(ctx: RuntimeContext | dict | None) -> dict:
    """把 Skill 收到的 RuntimeContext 转成端口需要的 ctx dict（§7.1）。

    端口层的参数是 dict（便于跨进程/跨语言边界），Skill 层拿到的却是
    RuntimeContext 对象，这里做一次显式转换，避免各处手写字段名。
    """
    if isinstance(ctx, RuntimeContext):
        return ctx.to_dict()
    return dict(ctx or {})


async def collect_stream(port: RuntimePort, request: dict, ctx: dict) -> str:
    """消费 port.generate 的流式增量并拼成完整文本（失败返回空串）。

    delta 是增量、done 带完整内容、error 表示 Provider 失败；
    调用方据空串判断"未取得内容"，不解析字符串（§7.3）。
    """
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


def register_default_skills(registry: SkillRegistry) -> SkillRegistry:
    """注册五个默认教育 Skill，返回同一个 registry 便于链式调用。

    重复注册会由 SkillRegistry 抛 ValueError（不静默覆盖），
    因此本函数只应在装配阶段调用一次。
    """
    # 延迟导入：避免 skills/__init__ 与子模块互相导入形成环
    from .diagnosis import DiagnosisSkill
    from .paper_reader import PaperReaderSkill
    from .quiz import QuizSkill
    from .rag import RAGSkill
    from .socratic import SocraticSkill

    for skill in (
        SocraticSkill(),
        RAGSkill(),
        QuizSkill(),
        DiagnosisSkill(),
        PaperReaderSkill(),
    ):
        registry.register(skill)
    return registry


__all__ = [
    "Skill",
    "SkillDescriptor",
    "SkillRegistry",
    "collect_stream",
    "register_default_skills",
    "to_ctx_dict",
]