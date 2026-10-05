"""Maintain retrieval-only concept names and aliases without hard filtering."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any


_COURSE_ROOT = Path(__file__).resolve().parents[1] / "data" / "courses" / "data_structures_c"
_ALIASES: dict[str, tuple[str, ...]] = {
    "DS-LIN-01": ("线性表", "linear list", "linear list ADT"),
    "DS-LIN-02": ("顺序表", "array list", "sequential storage"),
    "DS-LIN-03": ("动态数组", "dynamic array", "amortized complexity"),
    "DS-LIN-04": ("链表", "linked list", "pointer list"),
    "DS-LIN-05": ("双向链表", "循环链表", "doubly linked list", "circular linked list"),
    "DS-LIN-06": ("栈", "stack", "LIFO"),
    "DS-LIN-07": ("队列", "循环队列", "queue", "FIFO"),
    "DS-TREE-01": ("二叉树", "binary tree", "tree storage"),
    "DS-TREE-03": ("二叉树遍历", "tree traversal", "preorder", "inorder", "postorder"),
    "DS-TREE-06": ("优先队列", "heap", "priority queue"),
    "DS-GRAPH-01": ("图", "adjacency matrix", "adjacency list"),
    "DS-GRAPH-02": ("广度优先搜索", "BFS", "breadth first search"),
    "DS-GRAPH-03": ("深度优先搜索", "DFS", "depth first search"),
    "DS-GRAPH-06": ("Dijkstra", "最短路径", "shortest path"),
    "DS-GRAPH-07": ("最小生成树", "Prim", "Kruskal", "minimum spanning tree"),
    "DS-SORT-01": ("排序复杂度", "稳定排序", "sorting complexity", "stable sort"),
    "DS-SORT-06": ("归并排序", "merge sort"),
    "DS-SORT-07": ("快速排序", "quick sort", "partition"),
    "DS-SORT-08": ("堆排序", "heap sort"),
}


@lru_cache(maxsize=8)
def _concept_catalog(course_id: str) -> dict[str, tuple[str, ...]]:
    if course_id != "ds.c_language.v1":
        return {}
    manifest_path = _COURSE_ROOT / "manifest.json"
    try:
        manifest: dict[str, Any] = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    catalog: dict[str, tuple[str, ...]] = {}
    for row in manifest.get("concepts", []):
        if not isinstance(row, dict):
            continue
        concept_id = str(row.get("concept_id") or "").strip()
        if not concept_id:
            continue
        terms = [str(row.get(key) or "").strip() for key in ("module", "name", "learning_objective")]
        terms.extend(_ALIASES.get(concept_id, ()))
        catalog[concept_id] = tuple(dict.fromkeys(term for term in terms if term))
    return catalog


def concept_query_terms(course_id: str, concept_ids: list[str] | tuple[str, ...] | None) -> list[str]:
    """Expand a query with curated names; unknown IDs remain searchable as IDs."""
    catalog = _concept_catalog(str(course_id or ""))
    terms: list[str] = []
    for raw in concept_ids or ():
        concept_id = str(raw or "").strip()
        if not concept_id:
            continue
        terms.append(concept_id)
        terms.extend(catalog.get(concept_id, ()))
    return list(dict.fromkeys(terms))


def expand_concept_query(query: str, course_id: str,
                         concept_ids: list[str] | tuple[str, ...] | None) -> tuple[str, list[str]]:
    terms = concept_query_terms(course_id, concept_ids)
    normalized_query = str(query or "").strip()
    return " ".join([normalized_query, *terms]).strip(), terms


__all__ = ["concept_query_terms", "expand_concept_query"]
