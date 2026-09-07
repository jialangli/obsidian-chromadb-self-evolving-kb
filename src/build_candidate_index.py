# -*- coding: utf-8 -*-
"""
候选索引构建（自进化闭环第③步）
================================
用微调后的模型重新编码全库，写入独立的候选 ChromaDB 集合（kb-engine_bge_ft_vN）。
绝不触碰线上 base 集合（kb-engine_bge），可随时丢弃回滚。

用法：
  python build_candidate_index.py --collection kb-engine_bge_ft_candidate --model models/bge_ft_candidate
"""
import os
os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import sys
import argparse
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import chromadb
from kb_embed import BgeEmbedder
from sync_obsidian_to_chroma import (
    get_vault_files, parse_markdown_to_chunks, bge_doc_text,
    VAULT_PATH, EXCLUDE_DIRS, CHROMA_PATH,
)


def build_candidate(collection_name: str, model_path: str, full_rebuild: bool = True) -> dict:
    """
    用微调模型编码全库并写入候选集合。
    Returns: {"built": bool, "count", "dim", "reason?"}
    """
    try:
        emb = BgeEmbedder(model_name=model_path, local_files_only=True)
        print(f"[CAND] 加载微调模型: {model_path}  (dim={emb.dim})")
    except Exception as e:
        return {"built": False, "reason": f"model-load-failed: {e}"}

    md_files = get_vault_files(VAULT_PATH, EXCLUDE_DIRS)
    all_chunks = []
    for fp in md_files:
        try:
            all_chunks.extend(parse_markdown_to_chunks(fp, VAULT_PATH))
        except Exception as e:
            print(f"  [PARSE-ERR] {fp}: {e}")
    print(f"[CAND] 解析 {len(all_chunks)} 个内容块")

    texts = [bge_doc_text(c) for c in all_chunks]
    vecs = emb.encode_docs(texts, batch_size=64)
    print(f"[CAND] 编码完成，向量维度 {vecs.shape[1]}")

    client = chromadb.PersistentClient(path=CHROMA_PATH)
    if full_rebuild:
        try:
            client.delete_collection(collection_name)
            print(f"[CAND] 已删除旧候选集合: {collection_name}")
        except Exception:
            pass
    col = client.get_or_create_collection(
        name=collection_name,
        metadata={
            "description": "Knowledge 知识库 - 候选(微调后)神经语义向量库",
            "embedding_model": model_path,
            "dim": int(vecs.shape[1]),
            "role": "candidate",
        },
        embedding_function=None,
    )
    ids = [c["id"] for c in all_chunks]
    metas = [c["metadata"] for c in all_chunks]
    BATCH = 500
    for i in range(0, len(ids), BATCH):
        b = slice(i, i + BATCH)
        try:
            col.delete(ids=ids[b])
        except Exception:
            pass
        col.add(ids=ids[b], documents=texts[b], metadatas=metas[b],
                embeddings=vecs[b].tolist())
    print(f"[CAND] 候选集合完成：{col.count()} 块，维度 {vecs.shape[1]}")
    return {"built": True, "count": col.count(), "dim": int(vecs.shape[1])}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--collection", required=True)
    ap.add_argument("--model", required=True)
    args = ap.parse_args()
    info = build_candidate(args.collection, args.model)
    print("[DONE]", info)
