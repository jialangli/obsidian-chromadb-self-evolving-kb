"""
Knowledge 知识库 混合检索模块 (Hybrid Retriever)
==============================================
将原本「单一 LSA 向量排序」升级为「向量 + 关键词」混合召回，RRF 融合。

  - 向量通道：优先用 bge-small-zh-v1.5 神经语义向量（kb-engine_bge 集合），模型/库缺失时回退 LSA
  - 关键词通道：纯 Python 实现的 Okapi BM25，对 chunk 全文现建内存索引
  - 融合：Reciprocal Rank Fusion (RRF)，k=60，兼顾语义泛化与专有名词精确匹配

设计目标：可回退（bge 不可用时自动降级为 LSA 混合）。
被 kb_mcp_server.py / kb_api_server.py / 评测脚本共用，保证线上与评测同一套逻辑。

用法：
    from kb_engine.hybrid_retrieve import HybridRetriever
    r = HybridRetriever()                       # 自动加载向量模型 + collection
    hits = r.search("火星救援得分体系", top_k=5)  # 混合检索
    hits = r.search("...", top_k=5, bm25_only=True)  # 仅关键词（做对照）

返回的每个 hit：{id, rank, fused_score, source_file, header_path, memory_type,
                 similarity, bm25, excerpt}
"""

import math
from pathlib import Path

import chromadb
import numpy as np

from kb_engine.sync_obsidian_to_chroma import (
    CHROMA_PATH,
    COLLECTION_NAME,
    COLLECTION_NAME_BGE,
    VECTORIZER_PATH,
    LsaEmbedder,
    tokenize_zh,
)

# ── 可调参数 ──────────────────────────────────────────
BM25_K1 = 1.5
BM25_B = 0.75
RRF_K = 60
CANDIDATE_POOL = 50  # 每个通道取前 N 个候选进入融合
QUERY_EXCERPT_LEN = 200


def _norm_query(query: str) -> str:
    """查询预处理：复用中文 n-gram 分词，保证与建库侧 token 空间一致"""
    return tokenize_zh(query).split()


class BM25Index:
    """纯 Python Okapi BM25，内存索引，从 chunk 全文构建。"""

    def __init__(self, corpus_tokens: list):
        self.doc_tokens = corpus_tokens
        self.doc_len = [len(t) for t in corpus_tokens]
        self.n_docs = len(corpus_tokens)
        self.avgdl = (sum(self.doc_len) / self.n_docs) if self.n_docs else 0.0
        self.tf = []  # 每篇 {term: freq}
        self.df = {}  # term -> 含该 term 的文档数
        for tokens in corpus_tokens:
            tf = {}
            for tok in tokens:
                tf[tok] = tf.get(tok, 0) + 1
            self.tf.append(tf)
            for tok in tf:
                self.df[tok] = self.df.get(tok, 0) + 1
        self.idf = {}
        for tok, df in self.df.items():
            self.idf[tok] = math.log(1 + (self.n_docs - df + 0.5) / (df + 0.5))

    def score(self, query_tokens: list) -> list:
        """返回 [(doc_idx, score), ...]，只含命中至少一个查询词的文档。"""
        scores = []
        qset = set(query_tokens)
        for i in range(self.n_docs):
            s = 0.0
            tf_i = self.tf[i]
            dl = self.doc_len[i] or 1
            denom_base = BM25_K1 * (1 - BM25_B + BM25_B * dl / (self.avgdl or 1))
            for tok in qset:
                f = tf_i.get(tok)
                if not f:
                    continue
                idf = self.idf.get(tok, 0.0)
                s += idf * (f * (BM25_K1 + 1)) / (f + denom_base)
            if s > 0:
                scores.append((i, s))
        scores.sort(key=lambda x: x[1], reverse=True)
        return scores


class HybridRetriever:
    """加载 LSA 模型与 collection，构建 BM25，提供混合检索。

    新增（自进化闭环）：
      - bge_collection_name：指定使用哪个 bge 神经向量集合（默认 base 集合，
        候选集合 kb-engine_bge_ft_vN 通过此参数指向）
      - bge_embedder：外部传入的 BgeEmbedder（如微调后的模型）；为 None 时按现状
        加载 base BgeEmbedder。两参数均向后兼容，不传则与改造前行为完全一致。
    """

    def __init__(
        self,
        chroma_path=CHROMA_PATH,
        collection_name=COLLECTION_NAME,
        vectorizer_path=VECTORIZER_PATH,
        bge_collection_name=COLLECTION_NAME_BGE,
        bge_embedder=None,
    ):
        self._chroma_path = chroma_path
        self._collection_name = collection_name
        self._vectorizer_path = vectorizer_path
        self._bge_collection_name = bge_collection_name
        self._bge_embedder_override = bge_embedder
        self.embedder = None
        self.collection = None
        self.bge = None
        self.vector_mode = "lsa"
        self._model_mtime = 0
        self._load()

    # 加载 / 自愈
    def _load(self):
        # LSA 始终加载，作为保底向量与 legacy 基线
        self.embedder = LsaEmbedder.load(self._vectorizer_path)
        self._model_mtime = Path(self._vectorizer_path).stat().st_mtime
        client = chromadb.PersistentClient(path=self._chroma_path)
        self.collection = client.get_collection(self._collection_name)  # LSA 集合

        bge_ok = False
        try:
            from kb_engine.kb_embed import BgeEmbedder
            from kb_engine.kb_embed import available as bge_available

            # 候选/微调模型：外部显式传入，跳过 base 模型探测
            if self._bge_embedder_override is not None:
                try:
                    bge_col = client.get_collection(self._bge_collection_name)
                    data = bge_col.get(include=["embeddings", "documents", "metadatas"])
                    if data["ids"] and data["embeddings"] is not None:
                        self.ids = data["ids"]
                        self.docs = data["documents"]
                        self.metas = data["metadatas"]
                        self.vecs = np.asarray(data["embeddings"], dtype="float32")
                        self.bge = self._bge_embedder_override
                        self.vector_mode = "bge"
                        bge_ok = True
                except Exception:
                    bge_ok = False
            elif bge_available():
                try:
                    bge_col = client.get_collection(self._bge_collection_name)
                    data = bge_col.get(include=["embeddings", "documents", "metadatas"])
                    if data["ids"] and data["embeddings"] is not None:
                        self.ids = data["ids"]
                        self.docs = data["documents"]
                        self.metas = data["metadatas"]
                        self.vecs = np.asarray(data["embeddings"], dtype="float32")
                        self.bge = BgeEmbedder()
                        self.vector_mode = "bge"
                        bge_ok = True
                except Exception:
                    bge_ok = False
        except Exception:
            bge_ok = False

        if not bge_ok:
            # 回退：LSA 集合 + 内存现算 LSA 向量（吃文件名增强）
            data = self.collection.get(include=["documents", "metadatas"])
            self.ids = data["ids"]
            self.docs = data["documents"]
            self.metas = data["metadatas"]
            self.bge = None
            self.vector_mode = "lsa"
            raw_texts = [
                f"{(m or {}).get('source_file','')} {(m or {}).get('filename','')} {d or ''}"
                for d, m in zip(self.docs, self.metas)
            ]
            self.vecs = self.embedder.transform(raw_texts)

        self._id_pos = {cid: i for i, cid in enumerate(self.ids)}
        # BM25 语料：把 source_file / filename 前缀进文本，使「仅出现在文件名里的实体」可被关键词召回
        corpus = []
        for d, meta in zip(self.docs, self.metas):
            src = (meta or {}).get("source_file", "")
            fn = (meta or {}).get("filename", "")
            corpus.append(_norm_query(f"{src} {fn} {d or ''}"))
        self.bm25 = BM25Index(corpus)

    def _refresh_if_stale(self):
        try:
            if Path(self._vectorizer_path).stat().st_mtime != self._model_mtime:
                self._load()
        except OSError:
            pass

    # 元数据过滤判定
    @staticmethod
    def _match(meta: dict, where: dict) -> bool:
        if not where:
            return True
        for k, v in where.items():
            if isinstance(v, dict) and "$contains" in v:
                if v["$contains"] not in str(meta.get(k, "")):
                    return False
            else:
                if str(meta.get(k, "")) != str(v):
                    return False
        return True

    def _make_hit(self, cid, fused, sim, bm25):
        i = self._id_pos.get(cid)
        meta = self.metas[i] if i is not None else {}
        doc = self.docs[i] if i is not None else ""
        return {
            "id": cid,
            "fused_score": round(fused, 5),
            "similarity": round(sim, 3) if sim is not None else None,
            "bm25": round(bm25, 3) if bm25 is not None else None,
            "source_file": meta.get("source_file", ""),
            "header_path": meta.get("header_path", ""),
            "memory_type": meta.get("memory_type", ""),
            "excerpt": (doc or "")[:QUERY_EXCERPT_LEN],
        }

    def search(
        self,
        query: str,
        top_k: int = 5,
        where: dict = None,
        vector_only: bool = False,
        bm25_only: bool = False,
    ):
        self._refresh_if_stale()
        top_k = max(1, int(top_k))
        where = where or {}
        pool = CANDIDATE_POOL

        # 向量通道（bge 神经语义 优先，回退 LSA；内存余弦，均吃文件名增强）
        vec_rank = {}
        if not bm25_only:
            if self.vector_mode == "bge" and self.bge is not None:
                qv = self.bge.encode_query(query)
            else:
                qv = self.embedder.transform([query])[0]
            sims = self.vecs @ qv
            cand = []
            for di in range(len(self.ids)):
                if self._match(self.metas[di], where):
                    cand.append((di, float(sims[di])))
            cand.sort(key=lambda x: x[1], reverse=True)
            for rank, (di, s) in enumerate(cand[:pool]):
                vec_rank[self.ids[di]] = (rank, 1.0 - s)  # dist=1-cos，下游 sim=1-dist 保持正确

        # 关键词通道（BM25）
        kw_rank = {}
        if not vector_only:
            qt = _norm_query(query)
            flat = []  # (doc_idx, score) 已按分数降序
            for di, sc in self.bm25.score(qt):
                if self._match(self.metas[di], where):
                    flat.append((di, sc))
            for rank, (di, sc) in enumerate(flat[:pool]):
                kw_rank[self.ids[di]] = (rank, sc)

        # 融合（RRF）
        fused = {}
        for cid, (rank, dist) in vec_rank.items():
            rrf = 1.0 / (RRF_K + rank + 1)
            sim = 1 - dist
            fused[cid] = [rrf, sim, None]
        for cid, (rank, sc) in kw_rank.items():
            rrf = 1.0 / (RRF_K + rank + 1)
            if cid in fused:
                fused[cid][0] += rrf
                fused[cid][2] = sc
            else:
                fused[cid] = [rrf, None, sc]

        ranked = sorted(fused.items(), key=lambda kv: kv[1][0], reverse=True)[:top_k]
        return [self._make_hit(cid, v[0], v[1], v[2]) for cid, v in ranked]

    def search_legacy(self, query: str, top_k: int = 5, where: dict = None):
        """原始行为（改造前基线）：直接用向量库里旧存向量 collection.query。"""
        self._refresh_if_stale()
        top_k = max(1, int(top_k))
        q_emb = self.embedder.transform([query]).tolist()
        res = self.collection.query(
            query_embeddings=q_emb,
            n_results=min(top_k, self.collection.count()),
            where=where or None,
        )
        hits = []
        for cid, dist, meta, doc in zip(
            res["ids"][0], res["distances"][0], res["metadatas"][0], res["documents"][0]
        ):
            hits.append(
                {
                    "id": cid,
                    "similarity": round(1 - dist, 3),
                    "source_file": meta.get("source_file", ""),
                    "header_path": meta.get("header_path", ""),
                    "memory_type": meta.get("memory_type", ""),
                    "excerpt": (doc or "")[:QUERY_EXCERPT_LEN],
                }
            )
        return hits


if __name__ == "__main__":
    r = HybridRetriever()
    for h in r.search("火星救援的得分体系", top_k=5):
        print(
            f"{h['fused_score']:.4f}  sim={h['similarity']} bm25={h['bm25']}  {h['source_file']} :: {h['header_path']}"
        )
