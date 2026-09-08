#!/usr/bin/env python3
"""
Knowledge 知识库 → ChromaDB 同步脚本

功能：
  1. 遍历 Obsidian vault 中所有 .md 文件
  2. 解析 YAML frontmatter + 正文
  3. 按 Markdown heading 分块（保留标题层级上下文）
  4. 生成 TF-IDF 向量（字符级 n-gram，支持中文）写入 ChromaDB
  5. 支持增量同步（基于文件修改时间检测）

Embedding 方案：字符级 n-gram TF-IDF（本地离线，无需下载模型）
  - 中文文本按 2-gram + 3-gram 切分，兼顾精确匹配与模糊语义
  - 后续网络恢复后可平滑升级为 sentence-transformers 神经嵌入

用法：
  python sync_obsidian_to_chroma.py          # 增量同步
  python sync_obsidian_to_chroma.py --full   # 全量重建
"""

import hashlib
import json
import os
import pickle
import re
import sys
from datetime import datetime
from pathlib import Path

import numpy as np

# 强制离线：必须在 import chromadb/huggingface_hub 之前设置，否则常量会被提前固化
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import chromadb
import frontmatter
from sklearn.decomposition import TruncatedSVD
from sklearn.feature_extraction.text import TfidfVectorizer

from kb_engine.config import settings

# ── 配置 ──────────────────────────────────────────────
VAULT_PATH = settings.vault_path
CHROMA_PATH = settings.chroma_path
COLLECTION_NAME = settings.collection_lsa
COLLECTION_NAME_BGE = (
    settings.collection_bge
)  # bge-small-zh-v1.5 神经语义向量库（与 LSA 并存，可回退）
LOG_PATH = os.path.join(settings.log_path, "sync_log.json")
VECTORIZER_PATH = settings.lsa_model_path

# 排除目录
EXCLUDE_DIRS = {".obsidian", ".trash", "模板"}

# LSA 语义向量维度
EMBEDDING_DIM = 384


def tokenize_zh(text: str) -> str:
    """中文字符级 n-gram 切分：2-gram + 3-gram，用空格连接便于 TfidfVectorizer 分析"""
    # 只保留中文、英文、数字
    cleaned = re.sub(r"[^\u4e00-\u9fff a-zA-Z0-9]", " ", text)
    tokens = []
    # 中文 2-gram 和 3-gram
    zh_chars = re.findall(r"[\u4e00-\u9fff]", cleaned)
    for i in range(len(zh_chars) - 1):
        tokens.append(zh_chars[i] + zh_chars[i + 1])
        if i + 2 < len(zh_chars):
            tokens.append(zh_chars[i] + zh_chars[i + 1] + zh_chars[i + 2])
    # 英文单词（小写化）
    en_words = re.findall(r"[a-zA-Z0-9]+", cleaned)
    tokens.extend([w.lower() for w in en_words])
    return " ".join(tokens)


class LsaEmbedder:
    """
    基于 TF-IDF + TruncatedSVD 的 LSA（潜在语义分析）嵌入器。
    SVD 从语料共现结构中学习潜在语义空间：
      - 同一主题的文档在低维空间中彼此接近
      - 查询经同一投影变换后可与文档语义匹配
    """

    def __init__(self, n_components: int = EMBEDDING_DIM):
        self.n_components = n_components
        self.vectorizer = TfidfVectorizer(
            analyzer="word",
            token_pattern=r"\S+",
            max_features=50000,
            sublinear_tf=True,
        )
        self.svd = None
        self.vocab_size = 0

    def fit(self, documents: list):
        tokenized = [tokenize_zh(doc) for doc in documents]
        tfidf_matrix = self.vectorizer.fit_transform(tokenized)
        self.vocab_size = len(self.vectorizer.vocabulary_)
        # SVD 分量数不能超过 min(样本数, 特征数)
        n_comp = min(self.n_components, tfidf_matrix.shape[0] - 1, tfidf_matrix.shape[1])
        self.svd = TruncatedSVD(n_components=n_comp, random_state=42)
        self.svd.fit(tfidf_matrix)
        return self

    def transform(self, documents: list) -> np.ndarray:
        tokenized = [tokenize_zh(doc) for doc in documents]
        tfidf_matrix = self.vectorizer.transform(tokenized)
        emb = self.svd.transform(tfidf_matrix).astype(np.float32)
        # L2 归一化（cosine 距离前提）
        norms = np.linalg.norm(emb, axis=1, keepdims=True)
        norms[norms == 0] = 1
        emb = emb / norms
        return emb

    def save(self, path: str):
        with open(path, "wb") as f:
            pickle.dump(
                {
                    "vectorizer": self.vectorizer,
                    "svd": self.svd,
                    "dim": self.n_components,
                },
                f,
            )

    @classmethod
    def load(cls, path: str):
        with open(path, "rb") as f:
            data = pickle.load(f)
        obj = cls()
        obj.vectorizer = data["vectorizer"]
        obj.svd = data["svd"]
        obj.n_components = data["dim"]
        obj.vocab_size = len(obj.vectorizer.vocabulary_)
        return obj


def get_vault_files(vault_path: str, exclude_dirs: set) -> list:
    """遍历 vault，返回所有 .md 文件路径（排除指定目录）"""
    md_files = []
    for root, dirs, files in os.walk(vault_path):
        dirs[:] = [d for d in dirs if d not in exclude_dirs]
        for f in files:
            if f.endswith(".md"):
                md_files.append(os.path.join(root, f))
    return sorted(md_files)


def parse_markdown_to_chunks(filepath: str, vault_root: str) -> list:
    """将 Markdown 文件按 heading 分块，保留标题层级上下文 + frontmatter 元数据"""
    with open(filepath, encoding="utf-8") as f:
        raw = f.read()

    post = frontmatter.loads(raw)
    content = post.content
    metadata = dict(post.metadata)

    rel_path = os.path.relpath(filepath, vault_root).replace("\\", "/")
    filename = Path(filepath).stem

    file_meta = {
        "source_file": rel_path,
        "filename": filename,
        "memory_type": metadata.get("memory_type", "unknown"),
        "tags": (
            "|".join(metadata.get("tags", []))
            if isinstance(metadata.get("tags"), list)
            else str(metadata.get("tags", ""))
        ),
        "source": metadata.get("source", ""),
        "date_created": str(metadata.get("date_created", "")),
        "date_updated": str(metadata.get("date_updated", "")),
        "expiry": str(metadata.get("expiry", "")),
        "status": metadata.get("status", ""),
        "confidence": str(metadata.get("confidence", "")),
        "owner": metadata.get("owner", ""),
        "module": metadata.get("module", ""),
        "outcome": metadata.get("outcome", ""),
        "task_status": metadata.get("task_status", ""),
        "last_synced": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
    }

    lines = content.split("\n")
    chunks = []
    current_section_lines = []
    current_headers = []
    header_pattern = re.compile(r"^(#{1,6})\s+(.+)$")

    def flush_chunk():
        if current_section_lines:
            chunk_text = "\n".join(current_section_lines).strip()
            if len(chunk_text) > 20:
                header_path = " > ".join(current_headers) if current_headers else filename
                chunk_id = hashlib.md5(
                    (rel_path + "|" + header_path + "|" + chunk_text[:100]).encode()
                ).hexdigest()
                doc_text = f"[{file_meta['memory_type']}] {header_path}\n{chunk_text}"
                chunks.append(
                    {
                        "id": chunk_id,
                        "text": doc_text,
                        "metadata": {
                            **file_meta,
                            "header_path": header_path,
                            "chunk_index": len(chunks),
                            # 全文指纹：供 bge 通道增量同步判断「内容是否变更」
                            "content_hash": hashlib.sha256(doc_text.encode("utf-8")).hexdigest(),
                        },
                    }
                )

    for line in lines:
        m = header_pattern.match(line)
        if m:
            flush_chunk()
            current_section_lines = []
            level = len(m.group(1))
            title = m.group(2).strip()
            current_headers = current_headers[: level - 1]
            current_headers.append(title)
        else:
            current_section_lines.append(line)

    flush_chunk()

    if not chunks and content.strip():
        chunk_id = hashlib.md5((rel_path + "|full").encode()).hexdigest()
        doc_text = f"[{file_meta['memory_type']}] {filename}\n{content.strip()}"
        chunks.append(
            {
                "id": chunk_id,
                "text": doc_text,
                "metadata": {
                    **file_meta,
                    "header_path": filename,
                    "chunk_index": 0,
                    "content_hash": hashlib.sha256(doc_text.encode("utf-8")).hexdigest(),
                },
            }
        )

    return chunks


def bge_doc_text(chunk: dict) -> str:
    """bge 与 BM25 共用的增强文本：前缀文件名，令『仅出现在文件名里的实体』也进入语义/关键词空间"""
    meta = chunk["metadata"]
    src = meta.get("source_file", "")
    fn = meta.get("filename", "")
    return f"{src} {fn} | {chunk['text']}"


def build_bge_collection(all_chunks: list, full_rebuild: bool = False) -> dict:
    """构建/更新 bge 神经语义向量库（独立 collection，失败不影响 LSA 同步）。

    非全量时做**内容哈希增量**：对比库内已存 content_hash，只编码新增/变更的块，
    并删除库中已消失的块 —— 未变更的块完全跳过（模型都不用加载，秒级完成）。
    旧库（元数据没有 content_hash）会退化为一次性全量重建。
    """
    try:
        from kb_engine.kb_embed import BgeEmbedder, available

        client = chromadb.PersistentClient(path=CHROMA_PATH)
        if full_rebuild:
            try:
                client.delete_collection(COLLECTION_NAME_BGE)
            except Exception:
                pass
        col = client.get_or_create_collection(
            name=COLLECTION_NAME_BGE,
            metadata={"description": "Knowledge 知识库 - bge-small-zh-v1.5 神经语义向量库"},
            embedding_function=None,
        )

        # 目标集合
        ids = [c["id"] for c in all_chunks]
        new_hashes = {c["id"]: (c["metadata"].get("content_hash") or "") for c in all_chunks}
        # 库内现状（id -> 已存 content_hash）
        old = col.get(include=["metadatas"], limit=max(col.count(), 1))
        old_hashes = (
            {
                cid: (m or {}).get("content_hash", "") or ""
                for cid, m in zip(old["ids"], old["metadatas"] or [])
            }
            if old["ids"]
            else {}
        )

        if not full_rebuild:
            to_add = [
                c
                for c in all_chunks
                if c["id"] not in old_hashes or new_hashes[c["id"]] != old_hashes.get(c["id"])
            ]
            to_del = [cid for cid in old_hashes if cid not in new_hashes]
        else:
            to_add = list(all_chunks)
            to_del = list(old_hashes)
        removed = len(to_del)
        changed = len(to_add)

        if not changed and not to_del:
            print(f"[BGE]  无变更块，跳过（{len(ids)} 块已是最新）")
            return {
                "built": True,
                "changed": 0,
                "removed": 0,
                "count": col.count(),
                "skipped": True,
            }

        if not available():
            print("[BGE]  模型权重未缓存，跳过 bge 建库（检索将回退 LSA 混合）")
            return {"built": False, "reason": "model-not-cached"}

        print("[BGE]  加载 bge-small-zh-v1.5 ...")
        emb = BgeEmbedder()
        # 只编码有变更的块
        add_texts = [bge_doc_text(c) for c in to_add]
        print(
            f"[BGE]  编码 {len(add_texts)} 个变更文本块（跳过未变更 {len(ids) - len(to_add)} 个）..."
        )
        add_vecs = emb.encode_docs(add_texts, batch_size=64) if add_texts else None

        if to_del:
            for i in range(0, len(to_del), 500):
                col.delete(ids=to_del[i : i + 500])
        if to_add:
            add_metas = [c["metadata"] for c in to_add]
            for i in range(0, len(to_add), 500):
                s = slice(i, i + 500)
                # 必须用 upsert：chroma 的 add() 对已存在 id 会「静默忽略」整条更新
                # （文档/元数据都不动），增量补写会永远不生效
                col.upsert(
                    ids=[c["id"] for c in to_add[s]],
                    documents=add_texts[s],
                    metadatas=add_metas[s],
                    embeddings=add_vecs[s],  # float32 ndarray 直传
                )
        print(
            f"[BGE]  bge 库完成：{col.count()} 块，维度 {add_vecs.shape[1] if add_vecs is not None else '?'}（变更 {changed}，删除 {removed}）"
        )
        return {
            "built": True,
            "changed": changed,
            "removed": removed,
            "dim": int(add_vecs.shape[1]) if add_vecs is not None else None,
            "count": col.count(),
        }
    except Exception as e:
        print(f"[BGE]  bge 建库失败（已忽略，检索回退 LSA）：{e}")
        return {"built": False, "reason": str(e)}


def sync(full_rebuild: bool = False):
    """执行同步"""
    settings.ensure_dirs()
    print(f"{'='*60}")
    print("Knowledge 知识库 → ChromaDB 同步")
    print(f"  Vault:       {VAULT_PATH}")
    print(f"  ChromaDB:    {CHROMA_PATH}")
    print(f"  Collection:  {COLLECTION_NAME}")
    print("  Embedding:   LSA（TF-IDF + SVD，离线）")
    mode_line = "全量重建" if full_rebuild else "增量同步（bge 按内容哈希增量；LSA 需全局重训）"
    print(f"  Mode:        {mode_line}")
    print(f"{'='*60}")

    # 1. 收集全部 chunk
    md_files = get_vault_files(VAULT_PATH, EXCLUDE_DIRS)
    print(f"[SCAN] 找到 {len(md_files)} 个 Markdown 文件")

    all_chunks = []
    for filepath in md_files:
        try:
            chunks = parse_markdown_to_chunks(filepath, VAULT_PATH)
            all_chunks.extend(chunks)
        except Exception as e:
            print(f"  [PARSE-ERR] {filepath}: {e}")
    print(f"[PARSE] 共解析出 {len(all_chunks)} 个内容块")

    if not all_chunks:
        print("[ABORT] 无内容可同步")
        return {"error": "no chunks"}

    # 2. 训练 LSA 嵌入器（TF-IDF + SVD，在全部 chunk 上 fit）
    print("[FIT] 训练 LSA 嵌入器（TF-IDF + TruncatedSVD）...")
    embedder = LsaEmbedder()
    embedder.fit([c["text"] for c in all_chunks])
    print(f"[FIT] 词汇表大小: {embedder.vocab_size}, 语义维度: {embedder.svd.n_components}")
    embedder.save(VECTORIZER_PATH)
    print(f"[FIT] LSA 模型已保存: {VECTORIZER_PATH}")

    # 3. 生成全部 embedding
    print(f"[EMBED] 生成 {len(all_chunks)} 个向量...")
    embeddings = embedder.transform([c["text"] for c in all_chunks])

    # 4. 写入 ChromaDB
    client = chromadb.PersistentClient(path=CHROMA_PATH)
    if full_rebuild:
        try:
            client.delete_collection(COLLECTION_NAME)
            print(f"[DELETE] 已删除旧 collection: {COLLECTION_NAME}")
        except Exception:
            pass

    collection = client.get_or_create_collection(
        name=COLLECTION_NAME,
        metadata={"description": "Knowledge 知识库 - 中文语义检索向量库 (LSA)"},
        embedding_function=None,  # 手动管理 embedding
    )

    ids = [c["id"] for c in all_chunks]
    texts = [c["text"] for c in all_chunks]
    metadatas = [c["metadata"] for c in all_chunks]

    # 分批写入（避免单次过大）。embeddings 直接传 float32 ndarray 切片，
    # 省去 .tolist() 到 Python float64 列表的整库转换（对 10 万级 chunk 是实打实的耗时+内存）
    batch_size = 500  # noqa: N806
    for i in range(0, len(ids), batch_size):
        batch_ids = ids[i : i + batch_size]
        batch_texts = texts[i : i + batch_size]
        batch_meta = metadatas[i : i + batch_size]
        batch_emb = embeddings[i : i + batch_size]
        try:
            collection.delete(ids=batch_ids)  # 幂等：先删后加
        except Exception:
            pass
        collection.add(
            ids=batch_ids,
            documents=batch_texts,
            metadatas=batch_meta,
            embeddings=batch_emb,
        )
        print(f"  [WRITE] 批次 {i//batch_size + 1}: {len(batch_ids)} 块入库")

    # 4.5 构建 bge 神经语义向量库（独立 collection，失败不影响上面的 LSA 结果）
    bge_info = build_bge_collection(all_chunks, full_rebuild)

    # 5. 记录同步日志（含统计摘要，供 /stats、knowledge_base_stats O(1) 读取，免全量扫描）
    from collections import Counter

    type_dist = Counter((c["metadata"].get("memory_type") or "unknown") for c in all_chunks)
    file_set = {c["metadata"].get("source_file", "") for c in all_chunks}
    sync_record = {
        "last_sync": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "files": len(md_files),
        "chunks": len(all_chunks),
        "total_chunks": len(all_chunks),
        "total_files": len(file_set),
        "type_distribution": dict(type_dist),
        "vocab_size": embedder.vocab_size,
        "embedding_dim": EMBEDDING_DIM,
        "embedding_method": (
            "hybrid(lsa+bm25) + bge-small-zh" if bge_info.get("built") else "hybrid(lsa+bm25)"
        ),
        "bge": bge_info,
    }
    os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
    with open(LOG_PATH, "w", encoding="utf-8") as f:
        json.dump(sync_record, f, ensure_ascii=False, indent=2)

    collection_count = collection.count()
    print(f"\n{'='*60}")
    print("同步完成！")
    print(f"  文件数:          {len(md_files)}")
    print(f"  内容块数:        {len(all_chunks)}")
    print(f"  词汇表大小:      {embedder.vocab_size}")
    print(f"  语义维度:        {embedder.svd.n_components}")
    print(f"  Collection 总量: {collection_count}")
    print(f"{'='*60}")

    return sync_record


def load_sync_summary() -> dict | None:
    """读取最近一次同步写入的统计摘要（/stats、knowledge_base_stats 免全量扫描用）。

    Returns:
        摘要 dict（total_chunks/total_files/type_distribution/last_sync...）；无文件或损坏返回 None。
    """
    try:
        with open(LOG_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, json.JSONDecodeError):
        return None


def main(argv: list = None):
    """CLI 入口（pyproject: kb-sync = kb_engine.sync_obsidian_to_chroma:main）"""
    argv = sys.argv[1:] if argv is None else list(argv)
    sync(full_rebuild="--full" in argv)


if __name__ == "__main__":
    main()
