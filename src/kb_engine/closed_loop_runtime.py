"""
检索运行时 + A/B 灰度路由（供 MCP / API 在线调用）
==================================================
按 active_model.json 决定每条检索请求走「基线」还是「灰度候选」，
并返回 model_version 标签（= 使用的 bge 集合名），供线上 A/B 监控按臂区分采纳率。

设计要点：
  - 默认（active_model.json 未开启 A/B）完全等同于现状：始终走 base bge 集合
  - 灰度开启时，按 traffic_ratio 概率把一部分流量路由到候选检索器
  - 检索器实例按 bge 集合名缓存，避免重复加载全库向量
"""

import random
import threading

import kb_engine.closed_loop_config as cfg  # noqa: N812
from kb_engine.hybrid_retrieve import HybridRetriever
from kb_engine.kb_embed import BgeEmbedder

# bge 集合名 → 检索器实例 的缓存
_HUBS = {}
# 构建锁：检索器加载 BGE/reranker 较重，「预热线程 + 首个请求」并发建会双重加载甚至崩溃
_HUBS_LOCK = threading.Lock()


def _make_retriever(bge_collection_name: str, bge_model_path: str = None) -> HybridRetriever:
    """构造检索器：bge_model_path 给定则用微调模型，否则用 base BgeEmbedder。"""
    override = None
    if bge_model_path:
        override = BgeEmbedder(model_name=bge_model_path, local_files_only=True)
    return HybridRetriever(
        bge_collection_name=bge_collection_name,
        bge_embedder=override,
    )


def get_baseline_retriever() -> HybridRetriever:
    st = cfg.load_active()
    key = st["active_bge_collection"]
    if key not in _HUBS:
        with _HUBS_LOCK:
            if key not in _HUBS:
                _HUBS[key] = _make_retriever(
                    key,
                    st["active_bge_model"] if _is_custom(st["active_bge_model"]) else None,
                )
    return _HUBS[key]


def get_candidate_retriever() -> HybridRetriever:
    """返回当前灰度候选检索器；未开启灰度或候选不可用则返回 None。"""
    st = cfg.load_active()
    ab = st["ab"]
    if not ab.get("enabled") or not ab.get("candidate_collection"):
        return None
    key = ab["candidate_collection"]
    if key not in _HUBS:
        with _HUBS_LOCK:
            if key not in _HUBS:
                _HUBS[key] = _make_retriever(key, ab.get("candidate_model"))
    return _HUBS[key]


def _is_custom(model_name: str) -> bool:
    """判断模型是否为本地微调目录（非官方 HF 名）。"""
    return bool(model_name) and ("/" not in model_name)


def decide_variant() -> tuple:
    """
    决定本次请求走哪个臂。
    Returns: (variant: "baseline"|"candidate", collection_name, retriever)
    """
    st = cfg.load_active()
    ab = st["ab"]
    if ab.get("enabled") and ab.get("candidate_collection"):
        ratio = float(ab.get("traffic_ratio", 0.0) or 0.0)
        if ratio > 0 and random.random() < ratio:
            cand = get_candidate_retriever()
            if cand is not None:
                return "candidate", ab["candidate_collection"], cand
    return "baseline", st["active_bge_collection"], get_baseline_retriever()


def warm_up_hubs() -> None:
    """启动预热：预构建 A/B 检索器（baseline，灰度开启时含候选）。

    与请求线程共用 _HUBS_LOCK（双检锁）：预热与首个请求并发时只会构建一次，
    另一方阻塞等待后直接复用缓存——不会出现两份 BGE/reranker 同时加载。
    """
    get_baseline_retriever()
    st = cfg.load_active()
    ab = st.get("ab") or {}
    if ab.get("enabled") and ab.get("candidate_collection"):
        get_candidate_retriever()


def hubs_ready() -> bool:
    """A/B 检索器 hub 是否已构建（供 /health 探活 / 客户端轮询首查就绪）"""
    return bool(_HUBS)


def reset_hubs():
    """清空缓存（active_model.json 变更后调用，使下次请求重建检索器）。"""
    _HUBS.clear()


if __name__ == "__main__":
    variant, coll, rt = decide_variant()
    print(f"variant={variant} collection={coll} vector_mode={rt.vector_mode}")
