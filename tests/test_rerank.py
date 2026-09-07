"""
两阶段 Rerank 测试
==================
重点：reranker 在无 sentence_transformers / 无模型权重时必须优雅降级，
不抛异常、不污染 RRF 结果；RRF 通道加权逻辑可单测。

注：本环境（纯 LSA 降级模式）未装 sentence_transformers，reranker 走降级分支。
装好依赖后重跑，会额外覆盖「rerank 实际生效」路径（见 test_p0_regression 不覆盖）。
"""
import pytest

from kb_engine.hybrid_retrieve import HybridRetriever
from kb_engine.rerank import BgeReranker


def test_reranker_graceful_degradation_without_deps():
    """无依赖/无模型时：available=False，rerank 返回空，绝不抛异常。"""
    rk = BgeReranker()
    assert rk.available is False
    assert rk.rerank("查询文本", ["文档一", "文档二"]) == []


def test_hybrid_retriever_rerank_disabled_search():
    """显式关闭 rerank：search 正常，hit 不带 rerank_score 字段。"""
    r = HybridRetriever(enable_rerank=False)
    hits = r.search("测试查询语句", top_k=3)
    assert len(hits) <= 3
    assert all("rerank_score" not in h for h in hits)


def test_hybrid_retriever_default_no_crash_on_missing_model():
    """默认开启 rerank（config.enable_rerank=True）：模型缺失时应自动降级，
    reranker 为 None 或 available=False，search 不崩。"""
    r = HybridRetriever()
    assert r.reranker is None or r.reranker.available is False
    hits = r.search("测试查询语句", top_k=3)
    assert len(hits) <= 3


def test_rrf_fuse_weighting():
    """RRF 通道加权：纯函数，不依赖外部资源。"""
    vec_rank = {"a": (0, 0.1), "b": (1, 0.2)}  # (rank, dist)
    kw_rank = {"a": (0, 5.0)}  # 仅 a 在关键词通道 rank0

    fused_balanced = HybridRetriever._rrf_fuse(vec_rank, kw_rank, vec_w=1.0, kw_w=1.0)
    fused_vec_heavy = HybridRetriever._rrf_fuse(vec_rank, kw_rank, vec_w=10.0, kw_w=0.0)

    # BM25 权重为 0 时，仅向量通道有分的 b 仍应进入融合
    assert "b" in fused_vec_heavy
    # 向量权重拉满时，向量强相关项 a 的融合分应高于平衡权重
    assert fused_vec_heavy["a"][0] > fused_balanced["a"][0]


def test_rerank_preserved_when_available(monkeypatch):
    """reranker 可用时，命中结果的 rerank_score 字段应被写入（mock 掉模型层）。"""
    r = HybridRetriever(enable_rerank=False)

    class FakeReranker:
        available = True

        def rerank(self, query, docs, top_k=None):
            # 返回与 docs 顺序相反，模拟重排把末位提到首位
            order = list(range(len(docs)))[::-1]
            if top_k:
                order = order[:top_k]
            return [(i, float(len(docs) - i)) for i in order]

    r._enable_rerank = True
    r.reranker = FakeReranker()
    r._rerank_top_k = 50
    # 准备足够候选：直接调 search 看 rerank 是否介入
    hits = r.search("需要重排的查询", top_k=3)
    # 若候选数 > top_k 且 reranker 可用，结果应带 rerank_score
    assert len(hits) <= 3
    if r.reranker.available and len(r.docs) > 3:
        assert all("rerank_score" in h for h in hits)
