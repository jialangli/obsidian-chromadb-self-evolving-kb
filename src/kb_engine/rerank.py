"""
两阶段重排（cross-encoder reranker）
====================================
BGE + BM25 的 RRF 融合是「无监督」的：它只看各通道各自的排序，
看不到 query 与 doc 之间的细粒度语义交互。本模块在 RRF 之后插入一个
cross-encoder（bge-reranker-base），构成两阶段召回：

    RRF Top-N(rerank_top_k)  →  cross-encoder 打分  →  Top-K(用户请求的 top_k)

bge-reranker 把 (query, doc) 拼接做联合编码，对中文语义匹配比 RRF 单用
通常能再提 8~15 个点的 Hit@5。GPU 不可用时走 CPU（Top-50 重排单条几十毫秒，
整体可接受）。

优雅降级：sentence_transformers 未安装，或模型权重未下载到本地缓存时，
`available` 置为 False，HybridRetriever 自动跳过 rerank、沿用 RRF 结果，
行为与不开启 rerank 完全一致。这与 kb_embed.BgeEmbedder 的降级约定保持一致。
"""

import os

os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

DEFAULT_RERANK_MODEL = "BAAI/bge-reranker-base"
RERANK_BATCH = 32


class BgeReranker:
    """惰性加载 bge-reranker-base cross-encoder；供 HybridRetriever 在 RRF 之后重排。"""

    def __init__(self, model_name: str = DEFAULT_RERANK_MODEL, local_files_only: bool = True):
        self.model_name = model_name
        self._local_files_only = local_files_only
        self.model = None
        self.available = False
        self._try_load()

    def _try_load(self):
        try:
            from sentence_transformers import CrossEncoder
        except ImportError:
            # 加载器不在：静默降级，不打印，避免污染正常输出
            self.available = False
            return
        try:
            # 与 BgeEmbedder 同样必须显式 local_files_only，避免离线环境仍去 HEAD 检查
            os.environ["HF_HUB_OFFLINE"] = "1"
            os.environ["TRANSFORMERS_OFFLINE"] = "1"
            self.model = CrossEncoder(
                self.model_name,
                device="cpu",
                local_files_only=self._local_files_only,
            )
            self.available = True
        except Exception:
            # 权重缺失或其他异常：降级为不可用，由调用方回退 RRF
            self.model = None
            self.available = False

    def rerank(self, query: str, docs: list, top_k: int = None) -> list:
        """对 docs 按与 query 的相关性重排。

        Args:
            query: 查询文本
            docs: 候选文档文本列表
            top_k: 返回前 N 个；None 则返回全部（按分数降序）
        Returns:
            [(idx_in_docs, score), ...]，score 越大越相关
        """
        if not self.model or not docs:
            return []
        import numpy as np

        pairs = [(query, d) for d in docs]
        scores = np.asarray(self.model.predict(pairs, batch_size=RERANK_BATCH), dtype=float)
        order = np.argsort(-scores)
        if top_k is not None:
            order = order[:top_k]
        return [(int(i), float(scores[i])) for i in order]


if __name__ == "__main__":
    rk = BgeReranker()
    print("reranker available locally:", rk.available)
    if rk.available:
        out = rk.rerank(
            "火星救援的得分怎么算",
            [
                "火星救援得分体系包括任务完成度、操作规范性、用时效率。",
                "今天天气不错，适合出门散步。",
                "未来之城赛事强调两队对抗同场，选手代表需签字确认。",
            ],
            top_k=2,
        )
        print(out)
