"""Skill 注册表（DESIGNv0.4 §5.3）。

Skill = 面向模型或图节点的可复用能力说明与流程，负责"怎么完成一类任务"，
不拥有底层权限；需要模型、工具、记忆时一律通过 RuntimePort 申请。
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any

from .core.errors import SkillNotFound
from .core.ports import RuntimeContext, RuntimePort


@dataclass
class SkillDescriptor:
    """能力说明，也是给模型/评审看的元信息。"""

    name: str
    description: str
    when_to_use: str = ""
    version: str = "0.1.0"
    owner: str = ""  # 团队责任人，便于交接与评审
    tags: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "name": self.name,
            "description": self.description,
            "when_to_use": self.when_to_use,
            "version": self.version,
            "owner": self.owner,
            "tags": self.tags,
        }


class Skill(ABC):
    """Skill 基类。"""

    descriptor: SkillDescriptor

    @property
    def name(self) -> str:
        return self.descriptor.name

    @abstractmethod
    async def handle(
        self, payload: dict, ctx: RuntimeContext, port: RuntimePort
    ) -> dict:
        """执行一类任务，返回结构化结果。"""


class SkillRegistry:
    """进程内 Skill 注册表。"""

    def __init__(self) -> None:
        self._skills: dict[str, Skill] = {}

    def register(self, skill: Skill) -> Skill:
        name = skill.descriptor.name
        if not name:
            raise ValueError("Skill 必须有 name")
        if name in self._skills:
            raise ValueError(f"Skill 名称重复: {name}")
        self._skills[name] = skill
        return skill

    def get(self, name: str) -> Skill:
        skill = self._skills.get(name)
        if skill is None:
            raise SkillNotFound(f"Skill 不存在: {name}", skill=name)
        return skill

    def has(self, name: str) -> bool:
        return name in self._skills

    def names(self) -> list[str]:
        return sorted(self._skills)

    def describe(self) -> list[dict[str, Any]]:
        """供文档、门户与评审查看的能力清单。"""
        return [skill.descriptor.to_dict() for skill in self._skills.values()]

    async def invoke(
        self, name: str, payload: dict, ctx: RuntimeContext, port: RuntimePort
    ) -> dict:
        """调用 Skill；未注册时返回结构化失败，而不是抛异常打断图。"""
        skill = self._skills.get(name)
        if skill is None:
            return {
                "status": "skill_not_found",
                "skill": name,
                "error": {"code": "skill_not_found", "message": f"Skill 不存在: {name}"},
            }
        return await skill.handle(payload or {}, ctx, port)