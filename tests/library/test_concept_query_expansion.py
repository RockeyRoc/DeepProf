from __future__ import annotations

import asyncio

import pytest

from library.embeddings import BgeM3Embedder, retrieval_index_profile, versioned_index_path
from library.hybrid_retrieval import hybrid_rank
from library.chunking import chunk_pages_structured
from library.concept_queries import expand_concept_query
from library.models import DocumentPage
from library.service import ResourceLibrary
from tools.retrieval.search_textbook import build_search_textbook_tool


def test_concept_name_and_curated_alias_expand_query_without_filtering():
    query, terms = expand_concept_query("平均复杂度", "ds.c_language.v1", ["DS-SORT-07"])
    assert "平均复杂度" in query
    assert "DS-SORT-07" in terms
    assert any("快速排序" in term for term in terms)
    assert "quick sort" in terms


def test_search_tool_forwards_concept_ids_to_capable_searcher():
    observed = {}

    def searcher(query, **kwargs):
        observed.update({"query": query, **kwargs})
        return {"status": "insufficient_evidence", "evidence": []}

    tool = build_search_textbook_tool(searcher)
    result = asyncio.run(tool.handler({"query": "遍历", "course_id": "ds.c_language.v1",
        "concept_ids": ["DS-TREE-03"]}, {"learner_id": "local"}))
    assert result["status"] == "insufficient_evidence"
    assert observed["concept_ids"] == ["DS-TREE-03"]


def test_search_tool_keeps_legacy_searcher_signature_compatible():
    observed = {}

    def old_searcher(query, *, top_k, course_id, resource_type, tags, owner_id):
        observed.update(locals())
        return {"status": "ok", "evidence": []}

    tool = build_search_textbook_tool(old_searcher)
    result = asyncio.run(tool.handler({"query": "遍历", "concept_ids": ["DS-TREE-03"]}, {}))
    assert result["status"] == "ok"
    assert observed["query"] == "遍历"


def test_resource_library_embeds_concept_context_but_does_not_hard_filter(tmp_path):
    class Embedder:
        name = "capture-embedder"
        dimension = 4

        def __init__(self):
            self.texts = []

        def embed(self, texts):
            self.texts.extend(texts)
            return [[1.0, 0.0, 0.0, 0.0] for _ in texts]

    class Store:
        def __init__(self):
            self.kwargs = None

        def search(self, query_vector, **kwargs):
            self.kwargs = kwargs
            return []

    embedder = Embedder()
    store = Store()
    library = ResourceLibrary(store, library_root=tmp_path, embedder=embedder)
    result = library.search("遍历", course_id="ds.c_language.v1", concept_ids=["DS-GRAPH-03"])
    assert "DS-GRAPH-03" in embedder.texts[0]
    assert any("depth first search" in term for term in embedder.texts)
    assert result["status"] == "insufficient_evidence"
    assert "concept_ids" not in store.kwargs


def test_bge_m3_adapter_uses_local_cuda_model_and_validates_dimensions(tmp_path):
    snapshot = tmp_path / "bge-m3"
    snapshot.mkdir()

    class Model:
        def encode(self, texts, **kwargs):
            assert kwargs["max_length"] == 8192
            return {"dense_vecs": [[3.0] + [4.0] + [0.0] * 1022 for _ in texts]}

    embedder = BgeM3Embedder(snapshot, model_version="a" * 40,
                             model_loader=lambda: Model())
    vector = embedder.embed(["树的遍历"])[0]
    assert embedder.dimension == len(vector) == 1024
    assert sum(value * value for value in vector) == pytest.approx(1.0)
    profile = retrieval_index_profile(embedder, chunking_version="page-paragraph-v1")
    assert profile["embedding_model_version"] == "a" * 40
    assert versioned_index_path(tmp_path, profile).name == f"library-{profile['config_id']}.sqlite"


def test_hybrid_retrieval_keeps_stage_scores_and_reranks_top_five():
    dense = [{"document_id": "d", "chunk_id": f"c{i}", "page": i + 1,
              "score": 1.0 - i / 10, "text": str(i)} for i in range(10)]
    bm25 = [{"document_id": "d", "chunk_id": "c9", "page": 10,
             "score": 10.0, "text": "9"}]

    class Reranker:
        name = "test-reranker"
        model_version = "test-v1"

        def score(self, query, passages):
            assert query == "q"
            return [float(text) for text in passages]

    result = hybrid_rank("q", dense, bm25, reranker=Reranker(), candidate_k=10, final_k=5)
    assert len(result) == 5
    assert result[0]["chunk_id"] == "c9"
    assert set(result[0]["retrieval_stage_scores"]) == {"dense", "bm25", "reranker"}
    assert result[0]["rrf_score"] > 0


def test_structured_chunker_keeps_pages_and_code_blocks_together():
    page = DocumentPage(page=4, text="第一段说明线性表。\n\n```c\nint size = 3;\n```\n\n第二段说明顺序表。",
                        printed_page=12, chapter="线性表")
    chunks = chunk_pages_structured([page], resource_id="r", document_id="d",
                                    chunk_size=80, overlap=10)
    assert all(chunk.page == 4 and chunk.printed_page == 12 for chunk in chunks)
    assert any("```c\nint size = 3;\n```" in chunk.text for chunk in chunks)
