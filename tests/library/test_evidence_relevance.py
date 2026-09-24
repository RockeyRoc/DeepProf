from library.evidence_relevance import supports_claim_specific_evidence
from library.service import ResourceLibrary


class FixedStore:
    def __init__(self, text: str):
        self.text = text

    def search(self, *_args, **_kwargs):
        return [{
            "document_id": "doc-test",
            "chunk_id": "chunk-test",
            "page": 4,
            "source": "local-test-book",
            "text": self.text,
            "score": 0.5,
            "section": "test",
        }]


def test_amortized_proof_requires_matching_source_terms():
    query = "动态数组扩容的均摊复杂度如何严格证明？"

    assert not supports_claim_specific_evidence(query, [{"text": "顺序表可采用动态分配方式存储元素。"}])
    assert supports_claim_specific_evidence(query, [{"text": "采用均摊分析推导扩容策略。"}])


def test_expansion_factor_requires_matching_source_terms():
    query = "给出动态数组的扩容因子并证明其选择依据。"

    assert not supports_claim_specific_evidence(query, [{"text": "顺序表存储结构。"}])
    assert supports_claim_specific_evidence(query, [{"text": "扩容因子采用二倍，并给出证明。"}])


def test_ordinary_retrieval_does_not_require_specialized_markers():
    assert supports_claim_specific_evidence("邻接表如何存储图的边？", [{"text": "邻接表存储图中各顶点的边。"}])


def test_library_search_drops_locatable_but_off_claim_hits(tmp_path):
    library = ResourceLibrary(FixedStore("顺序表可采用动态分配方式存储元素。"), library_root=tmp_path)

    result = library.search("动态数组扩容的均摊复杂度如何严格证明？")

    assert result["status"] == "insufficient_evidence"
    assert result["evidence"] == []
    assert result["missing"] == ["claim_specific_source_support"]
