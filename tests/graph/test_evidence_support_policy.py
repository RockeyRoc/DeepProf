from graph.education.policies import request_explicitly_claims_source_gap


def test_explicit_syllabus_gap_is_detected_without_relying_on_retriever_text():
    assert request_explicitly_claims_source_gap("教材中未出现的新图算法接口是什么？")
    assert request_explicitly_claims_source_gap("What is not covered by the textbook?")
    assert not request_explicitly_claims_source_gap("邻接表如何存储图的边？")
