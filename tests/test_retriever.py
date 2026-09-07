"""
混合检索模块单元测试
"""


def test_hybrid_retriever_class_exists():
    """测试 HybridRetriever 类可以被导入"""
    from kb_engine.hybrid_retrieve import HybridRetriever

    assert HybridRetriever is not None
    assert callable(HybridRetriever)


def test_tokenize_zh():
    """测试中文分词函数"""
    from kb_engine.sync_obsidian_to_chroma import tokenize_zh

    # 基本分词测试
    result = tokenize_zh("你好世界")
    assert isinstance(result, str)
    # 应该包含 2-gram 和 3-gram
    assert "你好" in result
    assert "好世" in result
    assert "世界" in result


def test_rrf_constants():
    """测试 RRF 相关常量"""
    from kb_engine.hybrid_retrieve import BM25_B, BM25_K1, CANDIDATE_POOL, RRF_K

    assert RRF_K > 0
    assert BM25_K1 > 0
    assert 0 < BM25_B < 1
    assert CANDIDATE_POOL > 0


def test_retriever_methods():
    """测试 HybridRetriever 的方法存在"""
    from kb_engine.hybrid_retrieve import HybridRetriever

    methods = ["search", "_search_vector", "_search_bm25", "_rrf_fuse"]
    for m in methods:
        assert hasattr(HybridRetriever, m), f"HybridRetriever missing method: {m}"
