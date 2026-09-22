"""Pedagogical Graph：LangGraph 教学策略图（DESIGNv0.6 §6）。

LangGraph 的唯一职责（§6.1）：把教育策略表达为有状态图，
通过 DeepProf Runtime 提供的稳定端口调用模型、工具、记忆与技能。

模块划分：
    state.py     图状态（§6.4，可序列化、只放 Storage 引用）
    router.py    条件路由与教学动作决策（§6.2 触发条件）
    nodes/       八个教学节点（§6.2）
    policies/    策略阈值与文本模板（可评审、可单测的常量集中于此）
    contracts.py 决策与结果两份契约（WHAT / HOW 的交接形态）
    bindings.py  教学动作 → 通用能力的绑定表（纯数据，由组合根注入 Runtime）
    builder.py   用 StateGraph 组装并对外暴露入口

对外入口（API 与测试统一使用）：
    from graph.education.builder import build_education_graph, run_teaching_turn

本包内的节点只依赖 runtime.core.ports 的 RuntimePort 契约，
不 import runtime.providers / runtime.storage / runtime.tools.registry /
数据库或任何模型 SDK（§4.4）。
"""