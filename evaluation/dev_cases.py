"""Fixed, constructed prompts for local M1 A/B acceptance runs.

These are software test fixtures, never learner observations or education data.
"""

from __future__ import annotations

import json
from pathlib import Path

CASE_VERSION = "ds-dev-cases-v1"

SCENARIOS = {
    "cold_start": (
        ("DS-LIN-01", "什么是线性表？请用自己的话说明它与普通数组有什么关系。"),
        ("DS-TREE-02", "一棵二叉树的每个节点最多有几个子节点？"),
        ("DS-GRAPH-01", "用邻接表存储一张图时，一个顶点对应的表保存什么信息？"),
        ("DS-SORT-01", "比较排序算法时，稳定性指什么？"),
        ("DS-LIN-06", "栈的后进先出规则可以怎么理解？"),
    ),
    "prior_insufficient": (
        ("DS-LIN-01", "我以前学过线性表，但这个会话没有我的作答记录。请先问我一个基础问题，再判断从哪里开始。"),
        ("DS-TREE-02", "我还没答过树结构题。能只根据这句话说我掌握多少吗？"),
        ("DS-GRAPH-01", "当前会话没有图的学习记录；请用一个基础问题检查我是否理解邻接表。"),
        ("DS-SORT-01", "我之前可能学过排序，但没有答题证据。请先用一个小问题摸清我的起点。"),
        ("DS-LIN-04", "新会话没有我的先验作答。不要猜我的水平，先问我一个单链表小问题。"),
    ),
    "insufficient_evidence": (
        ("DS-LIN-03", "动态数组扩容的均摊复杂度如何严格证明？"),
        ("DS-LIN-03", "仅依据当前教材，请给出动态数组的扩容因子并证明其选择依据。"),
        ("DS-TREE-06", "教材没有明确覆盖的优先队列 API 应有哪些具体函数签名？"),
        ("DS-GRAPH-06", "请引用教材中未出现的一个新版 C 图算法接口并说明出处。"),
        ("DS-SORT-07", "这一份教材无法支持的库实现细节是什么？请标出对应教材页码。"),
    ),
    "consecutive_errors": (
        ("DS-TREE-03", "后序遍历一定先访问根节点，所以答案是先序吗？"),
        ("DS-GRAPH-02", "BFS 使用递归调用深度优先访问，理解对吗？"),
        ("DS-LIN-04", "在单链表里可以直接向后移动，因此删除头节点只改前驱指针。"),
        ("DS-SORT-06", "归并排序每次都只对一个元素做比较，所以平均复杂度是 O(n)。"),
        ("DS-GRAPH-05", "无权图最短路径只看顶点序号，路径长度就是序号差。"),
    ),
    "hint_then_success": (
        ("DS-LIN-07", "循环队列为什么需要区分队空和队满？"),
        ("DS-TREE-03", "怎样从二叉树的递归定义推导出先序、中序和后序？"),
        ("DS-GRAPH-02", "BFS 访问一个新顶点后应把它放到哪里？为什么？"),
        ("DS-SORT-06", "归并排序中的合并步骤怎样保证不漏掉元素？"),
        ("DS-LIN-02", "顺序表中间插入一个元素时，已有元素通常如何移动？"),
    ),
    "misconception": (
        ("DS-GRAPH-03", "DFS 按层访问所有邻居，所以它和 BFS 只是名称不同吗？"),
        ("DS-TREE-05", "二叉搜索树的中序遍历可能按降序访问节点吗？"),
        ("DS-SORT-02", "插入排序每一步都把整个数组彻底随机打乱吗？"),
        ("DS-LIN-06", "栈和队列的出入顺序完全相同，对吗？"),
        ("DS-GRAPH-08", "拓扑排序可以直接用于包含有向环的任意图吗？"),
    ),
    "active_explanation_request": (
        ("DS-LIN-04", "请从头解释单链表删除一个已知节点需要哪些条件。"),
        ("DS-TREE-04", "请讲一讲二叉树层序遍历怎样使用队列。"),
        ("DS-GRAPH-06", "请从适用条件开始解释 Dijkstra 的核心思路。"),
        ("DS-SORT-07", "请逐步说明快速排序中的一次划分。"),
        ("DS-LIN-06", "请解释栈为什么适合处理括号匹配。"),
    ),
    "low_progress": (
        ("DS-LIN-02", "我还是不理解顺序表如何插入元素。"),
        ("DS-TREE-03", "我还是不知道先序遍历的第一步。"),
        ("DS-GRAPH-01", "我仍分不清邻接矩阵和邻接表。"),
        ("DS-SORT-01", "我还是不理解什么叫稳定排序。"),
        ("DS-GRAPH-02", "BFS 的队列步骤我还是跟不上。"),
    ),
}


def build_cases() -> dict:
    categories = []
    for category, examples in SCENARIOS.items():
        for repeat, (concept_id, prompt) in enumerate(examples, start=1):
            user_turns = [prompt]
            if category == "consecutive_errors":
                user_turns = [prompt, "我还是认为刚才的说法正确，请指出关键依据。", "那我再解释一次，请检查我的推理。"]
            elif category == "hint_then_success":
                user_turns = [prompt, "请先给我一级提示。", "我按提示重新推理并尝试回答。"]
            elif category == "low_progress":
                user_turns = [prompt, "换一个例子后，我还是不理解。", "能否再问我一个更小的步骤？"]
            categories.append({
                "case_id": f"DSDEV-{len(categories) + 1:03d}",
                "case_version": CASE_VERSION,
                "category": category,
                "sample_type": "constructed_developer_fixture",
                "concept_id": concept_id,
                "repeat": repeat,
                "user_turns": user_turns,
                "expected_action_family": {
                    "cold_start": ["ask", "teach"],
                    "prior_insufficient": ["ask", "teach"],
                    "insufficient_evidence": ["evidence_gap"],
                    "consecutive_errors": ["correct", "ask"],
                    "hint_then_success": ["hint", "ask"],
                    "misconception": ["correct", "ask"],
                    "active_explanation_request": ["teach"],
                    "low_progress": ["reflect", "ask"],
                }[category],
                "expected_guards": [
                    "locatable_evidence_or_explicit_gap",
                    "no_cross_question_learner_estimate_in_A_or_B",
                ],
                "annotation_status": "pending_human_review",
            })
    return {
        "version": CASE_VERSION,
        "sample_type": "constructed_developer_fixture",
        "case_count": len(categories),
        "category_count": len(SCENARIOS),
        "human_subjects": False,
        "cases": categories,
    }


def write_cases(path: str | Path | None = None) -> Path:
    target = Path(path) if path else Path(__file__).with_name("dev_cases.json")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(build_cases(), ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return target


if __name__ == "__main__":
    print(write_cases())
