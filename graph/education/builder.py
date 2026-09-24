"""教学策略图组装与对外入口（DESIGNv0.6 §6.1 / §7.3 / §16.3）。

对外两个入口：

1. `build_education_graph(port)` —— 用 LangGraph StateGraph 组装八个节点与条件边。
   节点函数用 functools.partial 绑定 port，使 LangGraph 只看到 `(state) -> dict`，
   从而保证“节点只依赖 RuntimePort”这条边界在编译期就被固化（§4.4）。
2. `run_teaching_turn(port, state)` —— 跑一轮图并返回最终状态。
   API 与测试统一使用它；每次调用重建图（编译成本低），避免在模块里缓存
   带 port 的闭包导致测试之间互相污染。

图结构（§6.2 + §7.3）：

    START → assess ─(条件: 六类动作 / reflect / END)→ teach|ask|hint|correct|test|reflect
    teach|ask|hint|correct|test|reflect → END

防无限追问由三层保证（§7.3、§16.3 验收“图不能形成无限追问”）：
    ① assess 递增 turn_count，达到 max_turns 直接出 reflect 并结束；
    ② 唯一的回退边（test → assess）同时受 hint_level < HINT_MAX_LEVEL 与
       turn_count < max_turns 限制；
    ③ 编译/调用时设置 recursion_limit 作为最后兜底。
"""

from __future__ import annotations

from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.graph.state import CompiledStateGraph

from runtime.core.ports import RuntimePort

from .nodes.ask import ask
from .nodes.assess import assess
from .nodes.correct import correct
from .nodes.hint import hint
from .nodes.reflect import reflect
from .nodes.teach import teach
from .nodes.test import test as test_node
from .policies import MAX_TURNS_DEFAULT, recursion_limit
from .router import (
    route_from_assess,
    route_from_reflect,
    route_from_teaching,
    route_from_test,
)
from .state import PedagogyState, new_state

#: 条件边的返回值 → 目标节点（LangGraph 的 path_map）
_ASSESS_PATHS = {
    "teach": "teach",
    "ask": "ask",
    "hint": "hint",
    "correct": "correct",
    "test": "test",
    "reflect": "reflect",
    END: END,
}
_TEACHING_PATHS = {END: END}
_TEST_PATHS = {END: END}
_REFLECT_PATHS = {END: END}


def build_education_graph(port: RuntimePort) -> CompiledStateGraph:
    """组装并编译教学策略图；节点只通过 port 与外界交互。"""
    graph = StateGraph(PedagogyState)

    graph.add_node("assess", partial(assess, port=port))
    graph.add_node("teach", partial(teach, port=port))
    graph.add_node("ask", partial(ask, port=port))
    graph.add_node("hint", partial(hint, port=port))
    graph.add_node("correct", partial(correct, port=port))
    graph.add_node("test", partial(test_node, port=port))
    graph.add_node("reflect", partial(reflect, port=port))

    graph.add_edge(START, "assess")

    # Assess → 六类教学动作 / Reflect / END
    graph.add_conditional_edges("assess", route_from_assess, _ASSESS_PATHS)
    # 每条用户命令只运行一个有界回合，不写入跨题学情历史。
    for node_name in ("teach", "ask", "hint", "correct"):
        graph.add_conditional_edges(node_name, route_from_teaching, _TEACHING_PATHS)
    # Test 明确报告题库未配置，不生成题目或判分。
    graph.add_conditional_edges("test", route_from_test, _TEST_PATHS)
    # Reflect 纯模板收束。
    graph.add_conditional_edges("reflect", route_from_reflect, _REFLECT_PATHS)

    return graph.compile()


async def run_teaching_turn(port: RuntimePort, state: dict) -> dict:
    """跑一轮教学图，返回最终状态（API 与测试的统一入口）。

    入参 state 允许是部分状态：缺失字段用 state.new_state 的默认值补齐，
    未知字段被忽略（保证不会把非法键喂给 LangGraph 的 state schema）。
    """
    merged = new_state(**dict(state or {}))
    graph = build_education_graph(port)
    max_turns = int(merged.get("max_turns") or MAX_TURNS_DEFAULT)
    result = await graph.ainvoke(merged, config={"recursion_limit": recursion_limit(max_turns)})
    return dict(result)


__all__ = ["build_education_graph", "run_teaching_turn"]
