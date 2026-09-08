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
import json
import os
import subprocess
import sys
import threading
import time
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

# 强制离线（须在 chromadb / huggingface 相关 import 之前）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")


import chromadb
import uvicorn
from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

# 自进化闭环：A/B 灰度路由（与 MCP 服务端共用同一套 decide_variant）
import kb_engine.closed_loop_runtime as closed_loop_runtime
from kb_engine.config import settings
from kb_engine.sync_obsidian_to_chroma import (
    CHROMA_PATH,
    COLLECTION_NAME,
    LOG_PATH,
    VAULT_PATH,
    VECTORIZER_PATH,
    LsaEmbedder,
    load_sync_summary,
)

# ── 全局单例 ──────────────────────────────────────────
LSA_PATH = VECTORIZER_PATH  # lsa_model.pkl


@asynccontextmanager
async def lifespan(app: FastAPI):
    """启动预热：后台线程做轻量预载（LSA + collection，秒级），不阻塞启动；
    重型 hub 由首次 /search 惰性构建（与预热无并发，见 _warmup docstring）。"""
    threading.Thread(target=_warmup, daemon=True).start()
    yield


app = FastAPI(
    title="Knowledge 知识库语义检索 API",
    description="基于 LSA（TF-IDF + SVD）+ ChromaDB 的本地知识库检索服务",
    version="2.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

_embedder = None
_embedder_lock = threading.Lock()
_collection = None
_collection_lock = threading.Lock()
# 后台同步状态：POST /sync 立即返回，GET /sync/status 轮询
_sync_state = {"running": False, "last": None, "started_at": None}


def get_embedder() -> LsaEmbedder:
    global _embedder
    if _embedder is None:
        with _embedder_lock:
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
        # 锁串行化首次创建：chromadb 的 SharedSystemClient 非线程安全，
        # 「预热线程 + 首个请求」并发建客户端会 KeyError（已实测复现）
        with _collection_lock:
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


def _warmup():
    """启动后台预热，两层：
    1) 轻量（秒级）：预载 LSA 模型 + Chroma collection → /health /stats /filter 即时可用；
    2) 重型（约 30~120s）：预建 A/B 检索器 hub（BGE/reranker）→ 用户首查不再现场干等。

    hub 构建与 /search 共用 closed_loop_runtime 的 _HUBS_LOCK（双检锁，已验证并发安全：
    预热与首查并发只构建一次，另一方阻塞后复用缓存）。任一步失败都不影响服务。
    """
    try:
        get_embedder()
        get_collection()
    except Exception:
        return
    print(
        "[warm] 预建 A/B 检索器（BGE/reranker 首次加载约 30~120s，期间 /search 会等待就绪）...",
        flush=True,
    )
    t0 = time.time()
    try:
        closed_loop_runtime.warm_up_hubs()
        print(
            f"[warm] 检索器就绪，耗时 {time.time() - t0:.0f}s（/health 的 hub_ready=true）",
            flush=True,
        )
    except Exception as e:  # noqa: BLE001
        print(
            f"[warm] 预建失败（将退化为首次 /search 惰性构建）：{type(e).__name__}: {e}", flush=True
        )


# ── 请求/响应模型 ─────────────────────────────────────
class SearchRequest(BaseModel):
    query: str = Field(..., min_length=1, description="自然语言查询")
    top_k: int = Field(5, ge=1, le=50, description="返回结果数")
    memory_type: str | None = Field(
        None, description="过滤：fact/preference/experience/safety/task_state/navigation"
    )
    source_file: str | None = Field(None, description="过滤：来源文件相对路径（支持部分匹配）")


class FilterRequest(BaseModel):
    memory_type: str | None = Field(None, description="按治理类型过滤")
    status: str | None = Field(None, description="按状态过滤")
    source_file: str | None = Field(None, description="按来源文件过滤（部分匹配）")
    contains: str | None = Field(None, description="正文关键词包含")
    limit: int = Field(50, ge=1, le=500)


class SyncRequest(BaseModel):
    full: bool = Field(False, description="是否全量重建")


# ── 端点 ──────────────────────────────────────────────
@app.get("/health")
def health_check():
    """健康检查端点：真实探活 —— collection 缺失抛 503（Docker healthcheck 会判失败），
    并顺带返回库规模与检索器就绪状态；不做模型加载，保持毫秒级。"""
    coll = get_collection()  # 集合不存在时抛 503
    return {
        "status": "ok",
        "service": "kb-engine",
        "chunks": coll.count(),
        "lsa_model": os.path.exists(LSA_PATH),
        "hub_ready": closed_loop_runtime.hubs_ready(),  # 首查就绪后可轮询该字段
    }


@app.get("/")
def root():
    return {
        "service": "Knowledge 知识库语义检索 API",
        "version": "2.1.0",
        "embedding": "bge-small-zh-v1.5(512d) 优先，LSA(384d) 回退",
        "vector_store": f"ChromaDB @ {CHROMA_PATH}",
        "vault": VAULT_PATH,
        "endpoints": {
            "GET /health": "健康检查（探活 ChromaDB）",
            "GET /stats": "知识库统计",
            "POST /search": "语义检索",
            "POST /filter": "元数据过滤",
            "POST /sync": "触发同步（后台执行）",
            "GET /sync/status": "查询同步状态",
        },
    }


@app.get("/stats")
def stats():
    coll = get_collection()
    total = coll.count()

    # 优先读 sync 时落盘的统计摘要（O(1)）；摘要缺失或与库规模不一致时兜底全量扫描一次并回写
    summary = load_sync_summary()
    if summary and summary.get("total_chunks") == total:
        type_dist = summary.get("type_distribution") or {}
        total_files = summary.get("total_files", 0)
    else:
        all_meta = coll.get(include=["metadatas"])
        type_dist = {}
        file_set = set()
        for m in all_meta["metadatas"]:
            t = m.get("memory_type", "unknown")
            type_dist[t] = type_dist.get(t, 0) + 1
            file_set.add(m.get("source_file", ""))
        total_files = len(file_set)
        try:  # 回写摘要，下次 O(1)
            Path(LOG_PATH).parent.mkdir(parents=True, exist_ok=True)
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                json.dump(
                    {
                        "total_chunks": total,
                        "total_files": total_files,
                        "type_distribution": type_dist,
                        "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    },
                    f,
                    ensure_ascii=False,
                    indent=2,
                )
        except OSError:
            pass

    return {
        "total_chunks": total,
        "total_files": total_files,
        "memory_type_distribution": type_dist,
        "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "embedding": (
            f"bge-small-zh-v1.5({settings.bge_dimensions}d) 优先，"
            f"LSA({settings.lsa_dimensions}d) 回退"
        ),
        "embedding_dim": settings.bge_dimensions,
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
            # status/tags 已由检索器一并返回，无需再按 id 回查（原先是每条一次 coll.get）
            hits.append(
                {
                    "similarity": h["similarity"],
                    "bm25": h["bm25"],
                    "fused_score": h["fused_score"],
                    "source_file": h["source_file"],
                    "header_path": h["header_path"],
                    "memory_type": h["memory_type"],
                    "status": h.get("status", ""),
                    "tags": h.get("tags", ""),
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


def _run_sync_bg(full: bool):
    """在后台线程执行同步子进程；结束后失效全部缓存（LSA 重训过，向量/BM25 全过期）。"""
    global _sync_state, _embedder, _collection
    try:
        cmd = [sys.executable, "-m", "kb_engine.sync_obsidian_to_chroma"]
        if full:
            cmd.append("--full")
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=900,  # 大 vault + bge 编码可能较久
            cwd=str(Path(__file__).resolve().parents[2]),
        )
        ok = result.returncode == 0
        if ok:
            _embedder = None
            _collection = None
            closed_loop_runtime.reset_hubs()
        _sync_state["last"] = {
            "finished_at": datetime.now().isoformat(timespec="seconds"),
            "success": ok,
            "command": " ".join(cmd),
            "stdout_tail": (result.stdout or "")[-2000:],
            "stderr_tail": (result.stderr or "")[-500:],
        }
    except Exception as e:  # noqa: BLE001 - 后台任务需兜住一切异常写状态
        _sync_state["last"] = {"success": False, "error": str(e)}
    finally:
        _sync_state["running"] = False
        _sync_state["started_at"] = None


@app.post("/sync")
def trigger_sync(req: SyncRequest, background_tasks: BackgroundTasks):
    """触发同步：立即返回（后台执行，不阻塞请求线程；结果可经 GET /sync/status 轮询）"""
    if _sync_state["running"]:
        return {"success": False, "error": "已有同步任务进行中", "running": True}
    _sync_state["running"] = True
    _sync_state["started_at"] = datetime.now().isoformat(timespec="seconds")
    background_tasks.add_task(_run_sync_bg, req.full)
    return {"success": True, "started": True, "full": req.full, "status_endpoint": "/sync/status"}


@app.get("/sync/status")
def sync_status():
    """查询后台同步任务状态（running / last 结果）"""
    return _sync_state


def main(argv: list = None):
    """CLI 入口（pyproject: kb-api = kb_engine.kb_api_server:main）"""
    parser = argparse.ArgumentParser(description="Knowledge 知识库语义检索 API")
    parser.add_argument("--host", default=os.environ.get("KB_API_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("KB_API_PORT", 8300)))
    args = parser.parse_args(argv)

    settings.ensure_dirs()
    print(f"{'='*60}")
    print("Knowledge 知识库语义检索 API")
    print(f"  地址:     http://{args.host}:{args.port}")
    print(f"  文档:     http://{args.host}:{args.port}/docs")
    print(f"  Vault:    {VAULT_PATH}")
    print(f"  ChromaDB: {CHROMA_PATH}")
    print(f"{'='*60}")

    uvicorn.run(app, host=args.host, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
