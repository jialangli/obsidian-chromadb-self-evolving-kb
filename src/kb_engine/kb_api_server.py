#!/usr/bin/env python3
"""
Knowledge 知识库 - 语义检索 HTTP API 服务

端点：
  GET  /                     服务信息
  GET  /stats                知识库统计（文件数/块数/类型分布）
  POST /search               语义检索（自然语言查询）
  POST /filter               元数据过滤检索（memory_type/status/文件路径）
  POST /sync                 手动触发增量同步

启动：
  python kb_api_server.py              # 默认 127.0.0.1:8300
  python kb_api_server.py --port 9000
"""

import argparse
import os
import sys
from datetime import datetime

# 强制离线（须在 chromadb / huggingface 相关 import 之前）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from typing import Optional

import chromadb
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# 复用同目录下的同步脚本模块
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# 自进化闭环：A/B 灰度路由（与 MCP 服务端共用同一套 decide_variant）
import kb_engine.closed_loop_runtime as closed_loop_runtime
from kb_engine.sync_obsidian_to_chroma import (
    CHROMA_PATH,
    COLLECTION_NAME,
    VAULT_PATH,
    VECTORIZER_PATH,
    LsaEmbedder,
)

# ── 全局单例 ──────────────────────────────────────────
LSA_PATH = VECTORIZER_PATH  # lsa_model.pkl

app = FastAPI(
    title="Knowledge 知识库语义检索 API",
    description="基于 LSA（TF-IDF + SVD）+ ChromaDB 的本地知识库检索服务",
    version="2.0.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_embedder = None
_collection = None


def get_embedder() -> LsaEmbedder:
    global _embedder
    if _embedder is None:
        if not os.path.exists(LSA_PATH):
            raise HTTPException(
                status_code=503,
                detail=f"LSA 模型未找到（{LSA_PATH}），请先运行 sync_obsidian_to_chroma.py",
            )
        _embedder = LsaEmbedder.load(LSA_PATH)
    return _embedder


def get_collection():
    global _collection
    if _collection is None:
        client = chromadb.PersistentClient(path=CHROMA_PATH)
        try:
            _collection = client.get_collection(COLLECTION_NAME)
        except Exception:
            raise HTTPException(
                status_code=503,
                detail=(
                    f"知识库集合 '{COLLECTION_NAME}' 不存在，"
                    "请先运行 `kb sync --full` 构建索引。"
                ),
            )
    return _collection


_hybrid = None


def get_hybrid():
    """懒加载混合检索器（LSA 向量 + BM25 含文件名 + RRF 融合）"""
    global _hybrid
    if _hybrid is None:
        from kb_engine.hybrid_retrieve import HybridRetriever

        _hybrid = HybridRetriever()
    return _hybrid


# ── 请求/响应模型 ─────────────────────────────────────
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="自然语言查询")
    top_k: int = Field(5, ge=1, le=50, description="返回结果数")
    memory_type: Optional[str] = Field(
        None, description="过滤：fact/preference/experience/safety/task_state/navigation"
    )
    source_file: Optional[str] = Field(None, description="过滤：来源文件相对路径（支持部分匹配）")


class FilterRequest(BaseModel):
    memory_type: Optional[str] = Field(None, description="按治理类型过滤")
    status: Optional[str] = Field(None, description="按状态过滤")
    source_file: Optional[str] = Field(None, description="按来源文件过滤（部分匹配）")
    contains: Optional[str] = Field(None, description="正文关键词包含")
    limit: int = Field(50, ge=1, le=500)


class SyncRequest(BaseModel):
    full: bool = Field(False, description="是否全量重建")


# ── 端点 ──────────────────────────────────────────────
@app.get("/health")
def health_check():
    """健康检查端点（用于 Docker healthcheck 和负载均衡）"""
    return {"status": "ok", "service": "kb-engine"}


@app.get("/")
def root():
    return {
        "service": "Knowledge 知识库语义检索 API",
        "version": "2.0.0",
        "embedding": "bge-small-zh-v1.5(512d) 优先，LSA(384d) 回退",
        "vector_store": f"ChromaDB @ {CHROMA_PATH}",
        "vault": VAULT_PATH,
        "endpoints": {
            "GET /stats": "知识库统计",
            "POST /search": "语义检索",
            "POST /filter": "元数据过滤",
            "POST /sync": "触发同步",
        },
    }


@app.get("/stats")
def stats():
    coll = get_collection()
    total = coll.count()

    # 按类型统计
    all_meta = coll.get(include=["metadatas"])
    type_dist = {}
    file_set = set()
    for m in all_meta["metadatas"]:
        t = m.get("memory_type", "unknown")
        type_dist[t] = type_dist.get(t, 0) + 1
        file_set.add(m.get("source_file", ""))

    return {
        "total_chunks": total,
        "total_files": len(file_set),
        "memory_type_distribution": type_dist,
        "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "embedding": "bge-small-zh-v1.5(512d) 优先，LSA(384d) 回退",
        "embedding_dim": 512,
    }


@app.post("/search")
def search(req: SearchRequest):
    embedder = get_embedder()
    coll = get_collection()

    # 构造过滤条件
    where = None
    conditions = []
    if req.memory_type:
        conditions.append({"memory_type": req.memory_type})
    if req.source_file:
        conditions.append({"source_file": {"$contains": req.source_file}})
    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}

    # ── 优先：混合检索（bge 神经向量优先，LSA 回退；BM25 关键词 + RRF 融合）──
    try:
        hyb_where = {}
        if req.memory_type:
            hyb_where["memory_type"] = req.memory_type
        if req.source_file:
            hyb_where["source_file"] = {"$contains": req.source_file}
        # ── A/B 灰度路由：按 active_model.json 的 traffic_ratio 概率分流到候选检索器 ──
        variant, bge_coll, hr = closed_loop_runtime.decide_variant()
        model_version = bge_coll  # 集合名即版本标签，供 A/B 监控按臂区分采纳率
        hits = []
        for h in hr.search(req.query, top_k=req.top_k, where=hyb_where or None):
            m = coll.get(ids=[h["id"]], include=["metadatas"])
            meta = m["metadatas"][0] if m["metadatas"] else {}
            hits.append(
                {
                    "similarity": h["similarity"],
                    "bm25": h["bm25"],
                    "fused_score": h["fused_score"],
                    "source_file": h["source_file"],
                    "header_path": h["header_path"],
                    "memory_type": h["memory_type"],
                    "status": meta.get("status", ""),
                    "tags": meta.get("tags", ""),
                    "excerpt": h["excerpt"],
                }
            )
        retriever = f"hybrid({'bge' if hr.vector_mode == 'bge' else 'lsa'}+bm25+rrf) [{variant}@{model_version}]"
        return {
            "query": req.query,
            "top_k": req.top_k,
            "retriever": retriever,
            "ab_variant": variant,
            "model_version": model_version,
            "filters": {"memory_type": req.memory_type, "source_file": req.source_file},
            "results": hits,
        }
    except Exception as e:  # 任一步失败 → 回退原 LSA 逻辑
        fb_err = str(e)

    # ── 回退：原始 LSA 向量检索 ──
    q_emb = embedder.transform([req.query]).tolist()

    results = coll.query(
        query_embeddings=q_emb,
        n_results=req.top_k,
        where=where,
        include=["documents", "metadatas", "distances"],
    )

    hits = []
    for i in range(len(results["ids"][0])):
        hits.append(
            {
                "similarity": round(1 - results["distances"][0][i], 4),
                "source_file": results["metadatas"][0][i].get("source_file", ""),
                "header_path": results["metadatas"][0][i].get("header_path", ""),
                "memory_type": results["metadatas"][0][i].get("memory_type", ""),
                "status": results["metadatas"][0][i].get("status", ""),
                "tags": results["metadatas"][0][i].get("tags", ""),
                "excerpt": results["documents"][0][i][:500],
            }
        )

    return {
        "query": req.query,
        "top_k": req.top_k,
        "retriever": "lsa-fallback",
        "fallback_reason": fb_err,
        "filters": {"memory_type": req.memory_type, "source_file": req.source_file},
        "results": hits,
    }


@app.post("/filter")
def filter_docs(req: FilterRequest):
    coll = get_collection()

    where = None
    conditions = []
    if req.memory_type:
        conditions.append({"memory_type": req.memory_type})
    if req.status:
        conditions.append({"status": req.status})
    if req.source_file:
        conditions.append({"source_file": {"$contains": req.source_file}})

    if len(conditions) == 1:
        where = conditions[0]
    elif len(conditions) > 1:
        where = {"$and": conditions}

    results = coll.get(
        where=where,
        where_document=({"$contains": req.contains} if req.contains else None),
        include=["documents", "metadatas"],
        limit=req.limit,
    )

    items = []
    for i in range(len(results["ids"])):
        doc = results["documents"][i]
        items.append(
            {
                "source_file": results["metadatas"][i].get("source_file", ""),
                "header_path": results["metadatas"][i].get("header_path", ""),
                "memory_type": results["metadatas"][i].get("memory_type", ""),
                "status": results["metadatas"][i].get("status", ""),
                "date_updated": results["metadatas"][i].get("date_updated", ""),
                "excerpt": doc[:300],
            }
        )

    return {
        "filters": {
            "memory_type": req.memory_type,
            "status": req.status,
            "source_file": req.source_file,
            "contains": req.contains,
        },
        "count": len(items),
        "items": items,
    }


@app.post("/sync")
def trigger_sync(req: SyncRequest):
    """触发同步（在子进程中运行，避免阻塞 API）"""
    import subprocess

    script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sync_obsidian_to_chroma.py")
    cmd = [
        os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "venv",
            "Scripts",
            "python.exe",
        ),
        script,
    ]
    if req.full:
        cmd.append("--full")

    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,
            cwd=os.path.dirname(script),
        )
        # 同步后重置缓存（LSA 模型已重新训练，必须重新加载）
        global _embedder, _collection
        _embedder = None
        _collection = None
        return {
            "success": result.returncode == 0,
            "command": " ".join(cmd),
            "stdout_tail": result.stdout[-2000:] if result.stdout else "",
            "stderr_tail": result.stderr[-500:] if result.stderr else "",
        }
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"同步失败: {e}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Knowledge 知识库语义检索 API")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8300)
    args = parser.parse_args()

    print(f"{'='*60}")
    print("Knowledge 知识库语义检索 API")
    print(f"  地址:     http://{args.host}:{args.port}")
    print(f"  文档:     http://{args.host}:{args.port}/docs")
    print(f"  Vault:    {VAULT_PATH}")
    print(f"  ChromaDB: {CHROMA_PATH}")
    print(f"{'='*60}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
