"""
Knowledge 知识库 MCP Server (Phase 3)
====================================
将 Phase 2 构建的 ChromaDB 语义检索能力封装为 MCP 工具，供 Agent 原生调用。

- 传输方式：stdio（由 MCP 客户端作为子进程拉起）
- 数据来源：直接读取本地 ChromaDB 数据目录（不依赖 HTTP API 服务是否运行）
- 检索方案：LSA（TF-IDF + SVD 降维），与建库侧同一套模型
- Trace：每次工具调用落盘日志目录下的 mcp_trace.jsonl

工具清单：
  1. search_knowledge_base       语义检索（按自然语言查询知识库）
  2. filter_knowledge_base       元数据过滤（按类型/状态/来源/关键词列出条目）
  3. knowledge_base_stats        知识库统计概览
  4. record_retrieval_feedback   记录检索结果是否被采纳（反馈埋点，驱动自进化飞轮）
  5. feedback_stats              查看反馈统计（采纳率、高质量/低质量文档分布）

反馈飞轮：
  检索结果带 result_id → 调用方回传「是否采纳」→ 落盘 feedback.jsonl
  → 积累 query-文档 正负例 → 可用于对比学习微调 embedding 模型
"""

import hashlib
import json
import os
from collections import OrderedDict

# 强制离线（须在 chromadb / huggingface 相关 import 之前）
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

from datetime import datetime
from pathlib import Path

import chromadb

# MCP Python SDK 在 v2 把 FastMCP 更名为 MCPServer，模块从 mcp.server.fastmcp
# 迁到 mcp.server.mcpserver。依赖声明是 mcp>=1.0.0，实际可能装到 v1 或 v2，
# 这里双向兼容，避免「换个环境就 ImportError」。
try:
    from mcp.server.mcpserver import MCPServer
except ImportError:  # MCP SDK v1.x
    from mcp.server.fastmcp import FastMCP as MCPServer

import kb_engine.closed_loop_config as cfg  # noqa: N812

# 自进化闭环：A/B 灰度路由 + 按臂打 model_version 标签
import kb_engine.closed_loop_runtime as closed_loop_runtime
from kb_engine.config import settings
from kb_engine.sync_obsidian_to_chroma import (
    CHROMA_PATH,
    COLLECTION_NAME,
    VECTORIZER_PATH,
    LsaEmbedder,
    load_sync_summary,
)

# 路径统一取自闭环配置（可由 KB_ROOT 环境变量覆盖），不再硬编码本机绝对路径。
# 注意：MCP 写入的反馈必须与闭环读取的是同一个文件，否则飞轮断链。
TRACE_PATH = cfg.TRACE_PATH
FEEDBACK_PATH = cfg.FEEDBACK_PATH


def _call_with_fallback(fn, kwargs: dict, order: tuple):
    """
    逐级降级调用：MCP SDK 各版本对 title/description 等参数的支持不一致，
    老版本遇到不认识的关键字会抛 TypeError。这里按 order 依次裁剪参数重试。
    """
    last = None
    for keys in order:
        try:
            return fn(**{k: kwargs[k] for k in keys if k in kwargs})
        except TypeError as e:
            last = e
    raise last


_SERVER_KWARGS = {
    "name": "kb-engine",
    "title": "Knowledge 知识库",
    "description": "Knowledge 教育产品知识库的语义检索服务（领域文档/产品/PRD/方法论等）",
    "instructions": (
        "这是 Knowledge 教育产品知识库。使用 search_knowledge_base 做自然语言语义检索，"
        "使用 filter_knowledge_base 按元数据（memory_type/status）过滤条目，"
        "使用 knowledge_base_stats 查看库的规模。"
        "检索结果的每条都带有 result_id；当你判断某条结果确实回答了问题时，"
        "请调用 record_retrieval_feedback(result_id=..., adopted=true) 记录正反馈，"
        "无用结果记录 adopted=false。这有助于持续优化检索质量。"
    ),
}
server = _call_with_fallback(
    MCPServer,
    _SERVER_KWARGS,
    order=(
        ("name", "title", "description", "instructions"),
        ("name", "description", "instructions"),
        ("name", "instructions"),
        ("name",),
    ),
)


def _tool(**kwargs):
    """注册工具，同样按 SDK 版本能力降级 title/description"""

    def deco(fn):
        return _call_with_fallback(
            server.tool,
            kwargs,
            order=(
                ("name", "title", "description"),
                ("name", "description"),
                ("name",),
                (),
            ),
        )(fn)

    return deco


# ── 懒加载全局状态 ────────────────────────────────────
# 注意：同步脚本用 --full 会删除并重建 collection，磁盘上的模型文件也会被覆盖。
# 因此这里缓存的 embedder / collection 句柄随时可能失效，需具备自愈能力。
_state = {"embedder": None, "collection": None, "client": None, "model_mtime": 0}


def _load_state():
    """（重新）加载 LSA 模型与 ChromaDB collection"""
    model_path = Path(VECTORIZER_PATH)
    _state["embedder"] = LsaEmbedder.load(VECTORIZER_PATH)
    _state["model_mtime"] = model_path.stat().st_mtime
    _state["client"] = chromadb.PersistentClient(path=CHROMA_PATH)
    _state["collection"] = _state["client"].get_collection(COLLECTION_NAME)


def _get_state():
    """首次调用时加载（约 2-5 秒）；后续调用检查模型文件是否已被更新"""
    if _state["collection"] is None:
        _load_state()
        return _state
    # 模型文件被同步脚本覆盖过 → 重新加载，避免用旧模型查新向量
    try:
        if Path(VECTORIZER_PATH).stat().st_mtime != _state["model_mtime"]:
            _load_state()
    except OSError:
        pass
    return _state


def reset_state():
    """清空缓存，下次调用会重新加载（用于 collection 被重建后的自愈）"""
    _state.update({"embedder": None, "collection": None, "client": None, "model_mtime": 0})


_hybrid = None


def get_hybrid():
    """懒加载混合检索器；collection/模型更新后由内部 mtime 检测自愈"""
    global _hybrid
    if _hybrid is None:
        from kb_engine.hybrid_retrieve import HybridRetriever

        _hybrid = HybridRetriever()
    return _hybrid


def with_retry(fn):
    """执行一次数据库操作；若因句柄失效抛错，重建连接后重试一次。

    只对「句柄失效」这一可自愈场景重试，第二次仍失败则异常照常上抛（不吞）。
    设 KB_DEBUG=1 可打印首次失败原因，便于排查非句柄类问题。
    """
    try:
        return fn()
    except Exception as e:  # noqa: BLE001 - 需先感知一切句柄失效
        if os.environ.get("KB_DEBUG"):
            print(f"[mcp] 操作失败，重置句柄后重试: {type(e).__name__}: {e}", flush=True)
        reset_state()
        _get_state()
        return fn()


def _trace(tool: str, args: dict, result_summary: str):
    """记录一次工具调用到 JSONL trace 文件"""
    try:
        record = {
            "timestamp": datetime.now().isoformat(timespec="seconds"),
            "tool": tool,
            "args": args,
            "result": result_summary,
        }
        with open(TRACE_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        # trace 失败不影响主流程
        pass


# ── 反馈飞轮：result_id 生成 / 结果缓存 / 反馈落盘 ─────
# 检索时把 result_id -> 详情 存在内存，反馈时用于补全上下文（无需调用方重复传）。
# OrderedDict：淘汰用 popitem(last=False)，O(1)；不再 list(keys())[:n] 整表切片。
RESULT_CACHE: OrderedDict = OrderedDict()
RESULT_CACHE_MAX = 800


def _cache_put(key: str, value: dict) -> None:
    """写入并移到队尾（LRU 化：刚被覆盖的 key 不先淘汰）"""
    if key in RESULT_CACHE:
        del RESULT_CACHE[key]
    RESULT_CACHE[key] = value
    while len(RESULT_CACHE) > RESULT_CACHE_MAX:
        RESULT_CACHE.popitem(last=False)


def make_result_id(query: str, chunk_id: str) -> str:
    """由「查询 + 命中的 chunk id」生成稳定短 ID，同一对结果 ID 恒定可复现"""
    raw = f"{query}|{chunk_id}"
    return hashlib.md5(raw.encode("utf-8")).hexdigest()[:12]


def cache_results(query: str, hits: list, model_version: str = ""):
    """缓存本轮检索结果的详情，供反馈时反查（含 model_version 用于 A/B 分臂）"""
    for h in hits:
        _cache_put(
            h["result_id"],
            {
                "query": query,
                "source_file": h.get("source_file", ""),
                "header_path": h.get("header_path", ""),
                "memory_type": h.get("memory_type", ""),
                "similarity": h.get("similarity"),
                "model_version": model_version,
                "ts": datetime.now().isoformat(timespec="seconds"),
            },
        )


def log_feedback(
    result_id: str, adopted: bool, note: str = "", query: str = "", source_file: str = ""
) -> dict:
    """把一条反馈写入 feedback.jsonl，返回写入的记录"""
    detail = RESULT_CACHE.get(result_id, {})
    record = {
        "timestamp": datetime.now().isoformat(timespec="seconds"),
        "result_id": result_id,
        "adopted": bool(adopted),
        "note": note or "",
        # 优先用缓存里的详情（更准），取不到再用调用方传的
        "query": detail.get("query") or query,
        "source_file": detail.get("source_file") or source_file,
        "header_path": detail.get("header_path", ""),
        "memory_type": detail.get("memory_type", ""),
        "similarity": detail.get("similarity"),
        "model_version": detail.get("model_version", ""),
    }
    try:
        FEEDBACK_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(FEEDBACK_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")
    except OSError:
        pass
    return record


def _fmt_results(results: dict, top_k: int) -> str:
    """把 ChromaDB query 结果格式化为紧凑的 JSON 字符串"""
    hits = []
    for i in range(len(results["ids"][0])):
        meta = results["metadatas"][0][i]
        doc = results["documents"][0][i]
        hits.append(
            {
                "similarity": round(1 - results["distances"][0][i], 3),
                "source_file": meta.get("source_file", ""),
                "header_path": meta.get("header_path", ""),
                "memory_type": meta.get("memory_type", ""),
                "excerpt": doc[:200],
            }
        )
    return json.dumps(
        {"query": results.get("_query", ""), "count": len(hits), "results": hits},
        ensure_ascii=False,
        indent=2,
    )


# ── 工具 1：语义检索 ──────────────────────────────────
@_tool(
    name="search_knowledge_base",
    title="知识库语义检索",
    description=(
        "用自然语言查询 Knowledge 知识库，返回最相关的内容块。"
        "适用于查询领域文档、产品信息、PRD 需求、方法论等。"
    ),
)
def search_knowledge_base(query: str, top_k: int = 5, memory_type: str = None) -> str:
    """
    Args:
        query: 自然语言查询，如"火星救援的得分体系"
        top_k: 返回前 K 条结果（默认 5，最大 20）
        memory_type: 可选过滤：fact(事实)/preference(偏好)/experience(经验)/task_state(任务状态)/navigation(导航)
    """
    state = _get_state()
    top_k = max(1, min(int(top_k), 20))
    hyb_where = {"memory_type": memory_type} if memory_type else None

    # ── 优先：混合检索（按 A/B 路由选择基线/候选检索器）──
    variant, bge_coll, hr = closed_loop_runtime.decide_variant()
    model_version = bge_coll  # 集合名即版本标签，供线上 A/B 监控按臂区分采纳率
    try:
        retriever = f"hybrid({'bge' if hr.vector_mode == 'bge' else 'lsa'}+bm25+rrf)"
        hyb_hits = hr.search(query, top_k=top_k, where=hyb_where)
        hits = []
        for h in hyb_hits:
            # result_id 由「查询 + chunk id」生成，供后续反馈回传定位
            rid = make_result_id(query, h["id"])
            hits.append(
                {
                    "result_id": rid,
                    "similarity": h["similarity"],
                    "source_file": h["source_file"],
                    "header_path": h["header_path"],
                    "memory_type": h["memory_type"],
                    "bm25": h["bm25"],
                    "fused_score": h["fused_score"],
                    "excerpt": h["excerpt"],
                }
            )
        cache_results(query, hits, model_version=model_version)
        output = json.dumps(
            {
                "query": query,
                "retriever": retriever,
                "model_version": model_version,
                "count": len(hits),
                "results": hits,
            },
            ensure_ascii=False,
            indent=2,
        )
        top1 = hits[0]["source_file"] if hits else ""
        _trace(
            "search_knowledge_base",
            {"query": query, "top_k": top_k, "memory_type": memory_type},
            f"hits={len(hits)}, top1={top1}, r={retriever}, v={model_version}, ids={[h['result_id'] for h in hits]}",
        )
        return output
    except Exception:
        retriever = "lsa-fallback"

    # ── 回退：原始 LSA 向量检索 ──
    def _do():
        q_emb = state["embedder"].transform([query]).tolist()
        where = {"memory_type": memory_type} if memory_type else None
        return state["collection"].query(query_embeddings=q_emb, n_results=top_k, where=where)

    results = with_retry(_do)
    results["_query"] = query
    output = _fmt_results(results, top_k)
    top1 = results["metadatas"][0][0] if results["ids"][0] else {}
    _trace(
        "search_knowledge_base",
        {"query": query, "top_k": top_k, "memory_type": memory_type},
        f"hits={len(results['ids'][0])}, top1={top1.get('source_file', '')}, r={retriever}",
    )
    return output


# ── 工具 2：元数据过滤 ────────────────────────────────
@_tool(
    name="filter_knowledge_base",
    title="知识库元数据过滤",
    description=(
        "按元数据条件列出知识库条目（不做语义排序）。"
        "用于按记忆类型、状态、来源文件或正文关键词浏览内容。"
    ),
)
def filter_knowledge_base(
    memory_type: str = None,
    status: str = None,
    source_file: str = None,
    contains: str = None,
    limit: int = 10,
) -> str:
    """
    Args:
        memory_type: fact/preference/experience/task_state/navigation
        status: 如 in_progress/archived/active
        source_file: 来源文件名（部分匹配）
        contains: 正文包含的关键词
        limit: 最多返回条数（默认 10，最大 50）
    """
    state = _get_state()
    limit = max(1, min(int(limit), 50))

    where = {}
    if memory_type:
        where["memory_type"] = memory_type
    if status:
        where["status"] = status
    if source_file:
        where["source_file"] = {"$contains": source_file}

    kwargs = {"include": ["documents", "metadatas"], "limit": limit}
    if where:
        kwargs["where"] = where
    # Chroma 的 get() 不接受 where 与 where_document 同时出现。
    # 两者都给了就只按 where 查库，contains 交给下面的 Python 侧兜底过滤。
    if contains and not where:
        kwargs["where_document"] = {"$contains": contains}

    results = with_retry(lambda: state["collection"].get(**kwargs))
    items = []
    for i in range(len(results["ids"])):
        meta = results["metadatas"][i]
        doc = results["documents"][i] or ""
        if contains and contains not in doc:
            continue
        items.append(
            {
                "source_file": meta.get("source_file", ""),
                "header_path": meta.get("header_path", ""),
                "memory_type": meta.get("memory_type", ""),
                "status": meta.get("status", ""),
                "excerpt": doc[:200],
            }
        )
    _trace(
        "filter_knowledge_base",
        {"memory_type": memory_type, "status": status, "contains": contains},
        f"returned={len(items)}",
    )
    return json.dumps({"count": len(items), "items": items}, ensure_ascii=False, indent=2)


# ── 工具 3：统计概览 ──────────────────────────────────
@_tool(
    name="knowledge_base_stats",
    title="知识库统计",
    description="返回知识库规模统计：内容块总数、文件数、记忆类型分布。",
)
def knowledge_base_stats() -> str:
    state = _get_state()
    total = with_retry(lambda: state["collection"].count())

    # 优先读 sync 落盘的统计摘要（O(1)）；摘要缺失/与库规模不符时兜底全量扫描一次
    summary = load_sync_summary()
    if summary and summary.get("total_chunks") == total:
        dist = summary.get("type_distribution") or {}
        total_files = summary.get("total_files", 0)
    else:
        all_meta = with_retry(lambda: state["collection"].get(include=["metadatas"]))
        dist = {}
        for m in all_meta["metadatas"]:
            t = m.get("memory_type", "unknown")
            dist[t] = dist.get(t, 0) + 1
        total_files = len({m.get("source_file", "") for m in all_meta["metadatas"]})

    st = cfg.load_active()
    ab = st["ab"]
    ab_status = (
        f"灰度中(候选={ab['candidate_collection']}, 流量={int(float(ab.get('traffic_ratio',0) or 0)*100)}%)"
        if ab.get("enabled")
        else "未开启灰度"
    )
    _trace("knowledge_base_stats", {}, f"chunks={total}")
    return json.dumps(
        {
            "total_chunks": total,
            "total_files": total_files,
            "memory_type_distribution": dist,
            "embedding": (
                f"bge 激活集合={st['active_bge_collection']}"
                f"（base={settings.collection_bge}），LSA({settings.lsa_dimensions}d) 回退"
            ),
            "self_evolving_loop": ab_status,
            "last_sync": datetime.fromtimestamp(Path(VECTORIZER_PATH).stat().st_mtime).isoformat(
                timespec="minutes"
            ),
        },
        ensure_ascii=False,
        indent=2,
    )


# ── 工具 4：反馈埋点（自进化飞轮第一步）────────────────
@_tool(
    name="record_retrieval_feedback",
    title="记录检索结果反馈",
    description=(
        "记录某条检索结果是否被采纳（有用/没用），用于持续优化检索质量。"
        "传入 search_knowledge_base 返回结果中的 result_id 即可，无需重复传查询内容。"
    ),
)
def record_retrieval_feedback(result_id: str, adopted: bool, note: str = "") -> str:
    """
    Args:
        result_id: 检索结果中的 result_id 字段
        adopted: 是否被采纳（true=这条结果确实回答了问题，false=无用/不相关）
        note: 可选备注，如"答非所问""正是要找的计分表"
    """
    if not result_id:
        return json.dumps({"success": False, "error": "result_id 不能为空"}, ensure_ascii=False)
    rec = log_feedback(result_id, adopted, note)
    known = result_id in RESULT_CACHE
    _trace(
        "record_retrieval_feedback",
        {"result_id": result_id, "adopted": bool(adopted)},
        f"adopted={bool(adopted)}, known={known}",
    )
    return json.dumps(
        {
            "success": True,
            "recorded": {
                "result_id": rec["result_id"],
                "adopted": rec["adopted"],
                "query": rec["query"],
                "source_file": rec["source_file"],
            },
            "note": "已计入反馈飞轮，可用于后续 embedding 微调",
        },
        ensure_ascii=False,
        indent=2,
    )


# ── 工具 5：反馈统计 ──────────────────────────────────
@_tool(
    name="feedback_stats",
    title="检索反馈统计",
    description=(
        "查看反馈飞轮的积累情况：总反馈数、采纳率、"
        "以及被采纳最多/被忽略最多的文档分布，用于定位知识库内容质量问题。"
    ),
)
def feedback_stats(top_n: int = 5) -> str:
    """
    Args:
        top_n: 返回的 Top 文档数量（默认 5）
    """
    if not FEEDBACK_PATH.exists():
        return json.dumps(
            {
                "total_feedback": 0,
                "message": "暂无反馈数据。调用 record_retrieval_feedback 后这里会有统计。",
            },
            ensure_ascii=False,
            indent=2,
        )

    rows = []
    with open(FEEDBACK_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue

    if not rows:
        return json.dumps(
            {"total_feedback": 0, "message": "反馈文件为空"}, ensure_ascii=False, indent=2
        )

    # 同一 result_id 多次反馈时，取最新一条
    latest = {}
    for r in rows:
        latest[r["result_id"]] = r

    adopted_n = sum(1 for r in latest.values() if r.get("adopted"))
    total = len(latest)

    # 按文档统计正/负反馈
    pos, neg = {}, {}
    for r in latest.values():
        sf = r.get("source_file") or "(未知)"
        if r.get("adopted"):
            pos[sf] = pos.get(sf, 0) + 1
        else:
            neg[sf] = neg.get(sf, 0) + 1

    top_pos = sorted(pos.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    top_neg = sorted(neg.items(), key=lambda kv: kv[1], reverse=True)[:top_n]

    _trace("feedback_stats", {"top_n": top_n}, f"total={total}, adopt_rate={adopted_n/total:.2%}")

    return json.dumps(
        {
            "total_feedback": total,
            "adopted_count": adopted_n,
            "rejected_count": total - adopted_n,
            "adopt_rate": f"{adopted_n / total:.1%}",
            "top_adopted_files": [{"file": k, "count": v} for k, v in top_pos],
            "top_rejected_files": [{"file": k, "count": v} for k, v in top_neg],
        },
        ensure_ascii=False,
        indent=2,
    )


if __name__ == "__main__":
    server.run(transport="stdio")
