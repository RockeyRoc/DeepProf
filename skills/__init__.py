"""教育能力（Skill）层（DESIGNv0.6 §5.3 / §6.3 / §9）。

Skill = “怎么完成一类教学任务”的方法与流程，不拥有底层权限：
需要模型、工具、记忆时通过 ``RuntimeHost`` 宽面申请（声明 ``uses_host = True``），
由组合根在装配时把它交给 Runtime；图节点拿不到宽面，只能出决策（§4.4）。

本包提供五个教育能力，与 §6.3 教育能力节点一一对应：

    socratic     苏格拉底递进提问（孙一新）
    rag          教材检索与可追踪来源（许阳毅）
    quiz         出题与评价（孙一新）
    diagnosis    学情诊断（欧阳文凯）
    paper_reader 论文结构化理解与讨论（许阳毅）

**诚实原则**（§12 / §16.3 验收）：题库、向量库、学情模型这些尚未接入的能力
不得假装实现——对应 Skill 返回 ``{"status": "not_implemented", ...}``
并说明缺什么、由谁补（§18.1、§20.5）。

装配是组合根的职责（api/app.py 调用 ``register_default_skills``），
**不要**写 ``register_default_skills(RuntimeService().skills)``：
那会把 Skill 注册进一个随即被丢弃的实例。
"""

from __future__ import annotations

from runtime.skills import FunctionSkill, Skill, SkillRegistry

from .diagnosis import DiagnosisSkill
from .paper_reader import PaperReaderSkill
from .quiz import QuizSkill
from .rag import RAGSkill
from .socratic import SocraticSkill

#: 默认教育能力集合（顺序即健康检查里的注册顺序）
DEFAULT_SKILLS: tuple[type, ...] = (
    SocraticSkill,
    RAGSkill,
    QuizSkill,
    DiagnosisSkill,
    PaperReaderSkill,
)


def register_default_skills(registry: SkillRegistry) -> SkillRegistry:
    """注册默认教育 Skill，返回同一个 registry 便于链式调用。"""
    for skill_type in DEFAULT_SKILLS:
        registry.register(skill_type())
    return registry


__all__ = [
    "DEFAULT_SKILLS",
    "DiagnosisSkill",
    "FunctionSkill",
    "PaperReaderSkill",
    "QuizSkill",
    "RAGSkill",
    "Skill",
    "SkillRegistry",
    "SocraticSkill",
    "register_default_skills",
]