"""
检索质量评测：纯 LSA 向量  vs  混合(LSA+BM25+RRF)
用带标准答案的中文查询，比较 Hit@1 / Hit@5 / MRR。
"""

import sys

from kb_engine.hybrid_retrieve import HybridRetriever

# (查询, 期望命中的 source_file 子串)
#
# 构造原则（2026-09-02 重构，取代原 14 条）：
#   1. gold 必须「可达」——库中确实存在该子串的 source_file，否则永久无法命中，
#      只会把 Hit@5 天花板压低、让闸门测量噪声（原 14 条中 3 条是死用例）
#   2. gold 必须「精确」——尽量只匹配 1 个文件；匹配几十个文件的宽 gold 会虚高指标
#   3. query 用「用户会怎么问」的措辞，避免直接复述文件名（否则退化为字符串匹配）
#   4. 覆盖面与 Vault 体量成比例，尤其要覆盖新入库的产品手册（原 14 条零覆盖）
CASES = [
    # ── 产品类示例 ──
    ("智能设备的主要功能特点是什么", "产品/智能设备产品手册"),
    ("设备支持哪些连接方式和协议", "产品/智能设备技术规格"),
    ("产品的使用注意事项和安全说明", "产品/安全使用指南"),
    # ── 技术类示例 ──
    ("向量检索的基本原理是什么", "技术/向量检索原理"),
    ("RAG 系统如何构建知识库", "技术/RAG 实践指南"),
    ("混合检索相比单一检索的优势", "技术/混合检索方案"),
    # ── 方法论类示例 ──
    ("如何设计一套有效的知识分类体系", "方法论/知识分类方法论"),
    ("知识库治理的最佳实践有哪些", "方法论/知识库治理方法论"),
    ("如何评估检索系统的效果", "方法论/检索评估方法"),
    # ── 运营类示例 ──
    ("新用户如何快速上手使用系统", "运营/新用户入门指南"),
    ("常见问题和故障排查方法", "运营/常见问题 FAQ"),
    ("系统配置和个性化设置", "运营/配置说明"),
]


def evaluate(r, mode_name, searcher):
    hit1 = hit5 = 0
    rr = 0.0
    detail = []
    for q, gold in CASES:
        hits = searcher(q, top_k=5)
        srcs = [h["source_file"] for h in hits]
        rank = next((i + 1 for i, s in enumerate(srcs) if gold in s), None)
        if rank == 1:
            hit1 += 1
        if rank and rank <= 5:
            hit5 += 1
        rr += (1.0 / rank) if rank else 0.0
        detail.append((q, gold, rank))
    n = len(CASES)
    print(f"\n===== {mode_name} =====")
    print(
        f"Hit@1 = {hit1}/{n} = {hit1/n:.0%}   Hit@5 = {hit5}/{n} = {hit5/n:.0%}   MRR = {rr/n:.3f}"
    )
    for q, gold, rank in detail:
        mark = "OK " if rank else "MISS"
        print(f"  [{mark}] rank={rank or '-'}  {q!r} -> 期望含 {gold!r}")
    return hit1, hit5, rr / n


def audit_cases(r: HybridRetriever) -> list:
    """
    评估前自检：报告 gold 在库中不可达的用例。
    不可达的 gold 会永久无法命中，只会把指标天花板压低、让闸门测量噪声 —— 必须显式暴露。
    """
    indexed = {m.get("source_file", "") for m in (r.metas or [])}
    dead = [(q, gold) for q, gold in CASES if not any(gold in sf for sf in indexed)]
    if dead:
        print(f"\n⚠  gold 集自检：{len(dead)}/{len(CASES)} 条 gold 在库中不可达")
        for q, gold in dead:
            print(f"    - {q!r} -> 期望含 {gold!r}（库中没有匹配该子串的 source_file）")
        print("    这些用例永远无法命中，会持续压低 Hit@5；请补笔记或修正 gold。")
    return dead


def main(argv: list = None):
    """CLI 入口（pyproject: kb-eval = kb_engine.eval_retrieval:main）"""
    argv = sys.argv[1:] if argv is None else list(argv)
    r = HybridRetriever()
    if "--audit-cases" in argv:
        audit_cases(r)
        return
    audit_cases(r)
    a = evaluate(
        r, "改造前 · 原始 LSA (collection.query)", lambda q, top_k: r.search_legacy(q, top_k=top_k)
    )
    b = evaluate(
        r, "改造后 · 混合 (LSA增强+BM25含文件名+RRF)", lambda q, top_k: r.search(q, top_k=top_k)
    )
    print("\n===== 变化 =====")
    print(f"Hit@1: {a[0]}/{len(CASES)} -> {b[0]}/{len(CASES)}  ({(b[0]-a[0])*100//len(CASES):+d}%)")
    print(f"Hit@5: {a[1]}/{len(CASES)} -> {b[1]}/{len(CASES)}  ({(b[1]-a[1])*100//len(CASES):+d}%)")
    print(f"MRR  : {a[2]:.3f} -> {b[2]:.3f}  ({b[2]-a[2]:+.3f})")

    # 两阶段 Rerank 对比：仅当 cross-encoder reranker 实际可用时展示增益
    if r.reranker is not None and r.reranker.available:
        print("\n===== 两阶段 Rerank (RRF → bge-reranker-base → Top-K) =====")
        r_norer = HybridRetriever(enable_rerank=False)
        c = evaluate(
            r_norer, "对照 · 仅 RRF（关闭 rerank）", lambda q, top_k: r_norer.search(q, top_k=top_k)
        )
        print(f"RRF Hit@5        : {c[1]}/{len(CASES)}  (MRR {c[2]:.3f})")
        print(f"RRF+rerank Hit@5 : {b[1]}/{len(CASES)}  (MRR {b[2]:.3f})")
        print(f"Rerank 增益 Hit@5: {(b[1]-c[1])*100//len(CASES):+d}%   MRR {b[2]-c[2]:+.3f}")
    else:
        print(
            "\n[提示] reranker 未启用/不可用（sentence_transformers 未安装或模型未下载），"
            "本次仅评估 RRF；装好依赖后重跑可看到两阶段重排增益。"
        )


if __name__ == "__main__":
    main()
