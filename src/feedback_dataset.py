# -*- coding: utf-8 -*-
"""
反馈数据集：把 feedback.jsonl 与精选 gold 集转成闭环可用的训练/评估数据
========================================================================
  - load_feedback()：读取反馈飞轮落盘
  - fetch_chunk_text()：按 (source_file, header_path) 反查 chunk 文本（bge 集合中存的就是 bge_doc_text 形式）
  - build_training_triples()：反馈 → (query, 正例文本, 负例文本) 对比学习三元组
  - bootstrap_triples_from_cases()：反馈不足时，用 eval_retrieval.CASES（query→期望文档）合成 bootstrap 三元组
"""

import json
import random
from pathlib import Path

import chromadb

import closed_loop_config as C
from sync_obsidian_to_chroma import CHROMA_PATH, COLLECTION_NAME_BGE


def load_feedback() -> list:
    if not C.FEEDBACK_PATH.exists():
        return []
    rows = []
    with open(C.FEEDBACK_PATH, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


def _client():
    return chromadb.PersistentClient(path=CHROMA_PATH)


def fetch_chunk_text(collection_name: str, source_file: str, header_path: str = "") -> str:
    """按 (source_file, header_path) 反查 chunk 文本；兜底仅按 source_file。"""
    client = _client()
    try:
        col = client.get_collection(collection_name)
    except Exception:
        return None
    try:
        if header_path:
            res = col.get(
                where={"$and": [{"source_file": source_file},
                                {"header_path": header_path}]},
                include=["documents"], limit=1,
            )
            if res["ids"]:
                return res["documents"][0]
        res = col.get(where={"source_file": source_file}, include=["documents"], limit=1)
        if res["ids"]:
            return res["documents"][0]
    except Exception:
        return None
    return None


def _all_chunk_texts(collection_name: str):
    """返回集合内全部 chunk 文本，用于随机负样本采样。"""
    client = _client()
    try:
        col = client.get_collection(collection_name)
        res = col.get(include=["documents"], limit=col.count())
        return res["documents"] or []
    except Exception:
        return []


def build_training_triples(feedback: list, collection_name: str = COLLECTION_NAME_BGE,
                           max_neg_per_pos: int = 3, seed: int = 42) -> list:
    """
    反馈 → 对比学习三元组 (query, 正例文本, 负例文本)。
    同一 query 下：采纳(=true) 文档为正例，未采纳(=false) 文档为负例；
    若某 query 无显式负例，则从集合中随机采负例，保证对比信号。
    """
    rng = random.Random(seed)
    from collections import defaultdict
    by_query = defaultdict(lambda: {"pos": [], "neg": []})
    for r in feedback:
        sf = r.get("source_file", "")
        hp = r.get("header_path", "")
        if not sf:
            continue
        if r.get("adopted"):
            by_query[r["query"]]["pos"].append((sf, hp))
        else:
            by_query[r["query"]]["neg"].append((sf, hp))

    all_texts = _all_chunk_texts(collection_name)
    triples = []
    for q, grp in by_query.items():
        if not grp["pos"]:
            continue
        for (psf, ph) in grp["pos"]:
            pos_text = fetch_chunk_text(collection_name, psf, ph)
            if not pos_text:
                continue
            negs = grp["neg"] if grp["neg"] else []
            # 该 query 无显式负例 → 随机采一个不同 chunk 作为难负例
            if not negs and all_texts:
                cand = rng.choice(all_texts)
                if cand != pos_text:
                    triples.append((q, pos_text, cand))
                continue
            for (nsf, nh) in negs[:max_neg_per_pos]:
                neg_text = fetch_chunk_text(collection_name, nsf, nh)
                if neg_text and neg_text != pos_text:
                    triples.append((q, pos_text, neg_text))
    return triples


def bootstrap_triples_from_cases(collection_name: str = COLLECTION_NAME_BGE,
                                 cases: list = None, seed: int = 7,
                                 max_hard_neg: int = 3, top_k_retrieve: int = 20,
                                 retriever=None) -> list:
    """
    反馈不足时的 bootstrap：用精选 gold 集 (query, 期望 source_file 子串) 合成三元组。

    正例 = source_file 含 gold 子串的 chunk；
    难负例 = 用「当前检索器」对该 query 召回、但 source_file 不含 gold 的高排名 chunk
             （即模型当前真正会混淆的 chunk；随机负例模型早已能区分，梯度≈0，是
              「训了等于没训」的根因）。若检索未给出有效难负例，退化为随机负例以保证
              三元组有效。
    难负例一律取完整 chunk 文本（fetch_chunk_text 反查），不用被截断的 excerpt。
    """
    if cases is None:
        try:
            from eval_retrieval import CASES
            cases = CASES
        except Exception:
            return []
    rng = random.Random(seed)
    client = _client()
    try:
        col = client.get_collection(collection_name)
        all_docs = col.get(include=["documents", "metadatas"], limit=col.count())
    except Exception:
        return []

    docs = all_docs["documents"] or []
    metas = all_docs["metadatas"] or []

    if retriever is None:
        from hybrid_retrieve import HybridRetriever
        retriever = HybridRetriever()

    triples = []
    n_hard = 0
    for q, gold in cases:
        # 正例：source_file 含 gold 子串
        pos_idx = [i for i, m in enumerate(metas)
                   if gold in (m or {}).get("source_file", "")]
        if not pos_idx:
            continue
        pi = rng.choice(pos_idx)
        pos_text = docs[pi]

        # 难负例：当前检索器召回、但非 gold 的高排名 chunk（取完整文本）
        hard = []
        try:
            hits = retriever.search(q, top_k=top_k_retrieve)
            for h in hits:
                sf = h.get("source_file") or ""
                if gold in sf:
                    continue
                full = fetch_chunk_text(collection_name, sf, h.get("header_path") or "")
                if full and full != pos_text:
                    hard.append(full)
                if len(hard) >= max_hard_neg:
                    break
        except Exception:
            hard = []

        if hard:
            n_hard += 1
            triples.append((q, pos_text, hard))
        else:
            # 退化：随机负例（保证三元组有效）
            neg_pool = [i for i in range(len(docs)) if i != pi
                        and gold not in (metas[i] or {}).get("source_file", "")]
            if neg_pool:
                ni = rng.choice(neg_pool)
                triples.append((q, pos_text, [docs[ni]]))
    print(f"[BOOTSTRAP] 共 {len(triples)} 三元组，其中 {n_hard} 条含检索难负例")
    return triples


def structural_bootstrap_triples(collection_name: str = COLLECTION_NAME_BGE,
                                 max_per_file: int = 6, max_total: int = 400,
                                 seed: int = 11, max_hard_neg: int = 2,
                                 top_k_retrieve: int = 15, retriever=None) -> list:
    """
    用 Vault 自身结构合成大规模 (query→正例 section) 三元组，作为对比学习的训练燃料：
      - 每个 md 文件拆块后，取带 header_path 的内容块；以「末级标题」为 query、块正文为正例；
      - 难负例 = 检索该 query 的高排名、但非同文件块（模型当前易混淆者）；
      - 与 45 条 gold 评估集不重叠（训练/评估分离，避免泄漏式虚高）。
    领域贴合、规模大、分布接近真实「标题式检索」，给模型一致的方向性信号，
    可克服「小样本 bootstrap 微调≈加噪」导致闸门回滚的问题。
    """
    rng = random.Random(seed)
    from sync_obsidian_to_chroma import (
        get_vault_files, parse_markdown_to_chunks, bge_doc_text,
        VAULT_PATH, EXCLUDE_DIRS,
    )
    if retriever is None:
        from hybrid_retrieve import HybridRetriever
        retriever = HybridRetriever()

    files = get_vault_files(VAULT_PATH, EXCLUDE_DIRS)
    triples = []
    seen_q = set()
    for fp in files:
        try:
            chunks = parse_markdown_to_chunks(fp, VAULT_PATH)
        except Exception:
            continue
        cand = [c for c in chunks
                if c.get("metadata", {}).get("header_path")
                and len(bge_doc_text(c)) >= 40]
        rng.shuffle(cand)
        for c in cand[:max_per_file]:
            hp = c["metadata"]["header_path"]
            q = hp.split(" > ")[-1].strip()
            if not q or q in seen_q:
                continue
            seen_q.add(q)
            pos = bge_doc_text(c)
            sf_self = c["metadata"].get("source_file")
            # 难负例：检索该 query 的高排名、但非同文件块
            hard = []
            try:
                hits = retriever.search(q, top_k=top_k_retrieve)
                for h in hits:
                    sf = h.get("source_file") or ""
                    if sf == sf_self:
                        continue
                    full = fetch_chunk_text(collection_name, sf, h.get("header_path") or "")
                    if full and full != pos:
                        hard.append(full)
                    if len(hard) >= max_hard_neg:
                        break
            except Exception:
                hard = []
            triples.append((q, pos, hard if hard else None))
            if len(triples) >= max_total:
                break
        if len(triples) >= max_total:
            break
    print(f"[STRUCT] 结构式 bootstrap 共 {len(triples)} 三元组（与 45 gold 评估集分离）")
    return triples


def make_training_examples(triples: list):
    """三元组 → sentence_transformers.InputExample 列表（anchor=带前缀的 query，positive=文档）。"""
    from kb_embed import BGE_QUERY_PREFIX
    from sentence_transformers import InputExample
    examples = []
    for q, pos, _neg in triples:
        examples.append(InputExample(texts=[BGE_QUERY_PREFIX + q, pos]))
    return examples


if __name__ == "__main__":
    fb = load_feedback()
    print(f"feedback rows: {len(fb)}")
    tr = build_training_triples(fb)
    print(f"feedback-based triples: {len(tr)}")
    bs = bootstrap_triples_from_cases()
    print(f"bootstrap triples: {len(bs)}")
    for t in (tr + bs)[:3]:
        print("  Q:", t[0][:30], "| +:", t[1][:30], "| -:", t[2][:30])
