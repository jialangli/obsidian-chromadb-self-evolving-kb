"""
检索质量评测：纯 LSA 向量  vs  混合(LSA+BM25+RRF)
用带标准答案的中文查询，比较 Hit@1 / Hit@5 / MRR。

用法：
  kb eval                      # 用内置评估集（示例库 gold）评测
  kb eval --cases file.json    # 用外部评估集（[{query, gold}]）评测 → 自己的库
  kb eval --self 20            # 从当前索引自动取样生成自评集并评测 → 无标注也能测
  kb eval --audit-cases        # 仅检查 gold 是否在库中可达（不跑评测）
"""

import argparse
import json
import random
import sys
from pathlib import Path

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


# ── 评估集来源 ──────────────────────────────────────────
def load_cases_file(path: str) -> list:
    """读取外部评估集：JSON 数组，每项 {"query": str, "gold": str}"""
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    cases = []
    for i, item in enumerate(raw):
        q = (item.get("query") or "").strip()
        g = (item.get("gold") or item.get("source_file") or "").strip()
        if not q or not g:
            raise ValueError(f"cases[{i}] 缺少 query/gold: {item}")
        cases.append((q, g))
    return cases


def _first_query_sentence(text: str, max_len: int = 44) -> str:
    """从 chunk 正文取「第一句像人话的问句素材」：跳过头部的 [type] header 前缀与空行。"""
    body = text.split("\n", 1)[1] if "\n" in text else text
    for line in body.splitlines():
        s = line.strip().lstrip("#-* \t`").strip()
        if len(s) >= 12:
            # 截到句末标点或 max_len
            cut = min(len(s), max_len)
            for p in ("。", "！", "？", "；", ".", "!", "?"):
                idx = s.find(p)
                if 8 <= idx < cut:
                    cut = idx
            s = s[:cut].strip(" ，,、")
            if len(s) >= 8:
                return s
    return body.strip()[:max_len]


def gen_self_cases(r: HybridRetriever, n: int = 20, seed: int = 42) -> list:
    """从当前索引自动取样生成自评集：query=块正文首句，gold=该块所在文件。

    无标注数据时的近似评估（self/pseudo-relevance）：
    「拿块自己的措辞去问，能否找回原文」——衡量库的召回一致性，
    不测人工写的问法，但能暴露断链/分块/向量化问题，比没有强得多。
    """
    rng = random.Random(seed)
    # (source_file, index) 按文件分组，保证跨文件覆盖
    groups = {}
    for i, m in enumerate(r.metas or []):
        sf = (m or {}).get("source_file", "")
        if sf:
            groups.setdefault(sf, []).append(i)
    if not groups:
        raise RuntimeError("当前集合没有可用的 source_file，请先 kb sync --full")
    files = sorted(groups)
    rng.shuffle(files)
    cases = []
    cap = max(1, n // max(len(files), 1) + 1)
    for sf in files:
        idxs = groups[sf]
        rng.shuffle(idxs)
        for i in idxs[:cap]:
            if len(cases) >= n:
                break
            text = (r.docs[i] if i < len(r.docs) else "") or ""
            q = _first_query_sentence(text)
            if q:
                cases.append((q, sf))
        if len(cases) >= n:
            break
    rng.shuffle(cases)
    return cases[:n]


def write_cases_file(cases: list, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(
            [{"query": q, "gold": g} for q, g in cases],
            f,
            ensure_ascii=False,
            indent=2,
        )


# ── 评测核心 ──────────────────────────────────────────
def evaluate(r, mode_name, searcher, cases: list = None):
    cases = CASES if cases is None else cases
    hit1 = hit5 = 0
    rr = 0.0
    detail = []
    for q, gold in cases:
        hits = searcher(q, top_k=5)
        srcs = [h["source_file"] for h in hits]
        rank = next((i + 1 for i, s in enumerate(srcs) if gold in s), None)
        if rank == 1:
            hit1 += 1
        if rank and rank <= 5:
            hit5 += 1
        rr += (1.0 / rank) if rank else 0.0
        detail.append((q, gold, rank))
    n = len(cases)
    print(f"\n===== {mode_name} =====")
    print(
        f"Hit@1 = {hit1}/{n} = {hit1/n:.0%}   Hit@5 = {hit5}/{n} = {hit5/n:.0%}   MRR = {rr/n:.3f}"
    )
    for q, gold, rank in detail:
        mark = "OK " if rank else "MISS"
        print(f"  [{mark}] rank={rank or '-'}  {q!r} -> 期望含 {gold!r}")
    return hit1, hit5, rr / n


def audit_cases(r: HybridRetriever, cases: list = None) -> list:
    """
    评估前自检：报告 gold 在库中不可达的用例。
    不可达的 gold 会永久无法命中，只会把指标天花板压低、让闸门测量噪声 —— 必须显式暴露。
    """
    cases = CASES if cases is None else cases
    indexed = {m.get("source_file", "") for m in (r.metas or [])}
    dead = [(q, gold) for q, gold in cases if not any(gold in sf for sf in indexed)]
    if dead:
        print(f"\n⚠  gold 集自检：{len(dead)}/{len(cases)} 条 gold 在库中不可达")
        for q, gold in dead:
            print(f"    - {q!r} -> 期望含 {gold!r}（库中没有匹配该子串的 source_file）")
        print("    这些用例永远无法命中，会持续压低 Hit@5；请补笔记或修正 gold。")
    return dead


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="检索质量评测（Hit@1 / Hit@5 / MRR）")
    p.add_argument("--audit-cases", action="store_true", help="只检查 gold 可达性，不跑评测")
    p.add_argument("--cases", type=str, default=None, help="外部评估集 JSON（[{query, gold}]）")
    p.add_argument(
        "--self",
        type=int,
        default=0,
        metavar="N",
        help="从当前索引自动取样 N 条自评集并评测（无标注库的近似质量评估）",
    )
    p.add_argument(
        "--cases-out",
        type=str,
        default=None,
        help="--self 生成的自评集写出路径（默认 data/logs/eval_cases_self.json）",
    )
    return p


def main(argv: list = None):
    """CLI 入口（pyproject: kb-eval = kb_engine.eval_retrieval:main）"""
    args = _build_parser().parse_args(argv if argv is not None else sys.argv[1:])
    if args.cases and args.self:
        print("错误：--cases 与 --self 互斥，只能选其一")
        raise SystemExit(2)

    r = HybridRetriever()
    cases = None
    cases_label = "内置评估集"
    if args.self:
        from kb_engine.config import settings

        out = args.cases_out or str(Path(settings.log_path) / "eval_cases_self.json")
        cases = gen_self_cases(r, n=max(1, min(args.self, 500)), seed=42)
        write_cases_file(cases, out)
        cases_label = f"自评集(N={len(cases)})"
        print(f"[SELF] 已从当前库取样 {len(cases)} 条自评用例 -> {out}")
        print("       说明：query=块正文首句，gold=块所在文件；衡量「自己的措辞能否找回原文」。")
    elif args.cases:
        cases = load_cases_file(args.cases)
        cases_label = f"外部评估集(N={len(cases)})"

    audit_cases(r, cases)
    if args.audit_cases:
        return

    if cases is not None:
        print(f"\n评估集来源：{cases_label}")
    a = evaluate(
        r,
        "改造前 · 原始 LSA (collection.query)",
        lambda q, top_k: r.search_legacy(q, top_k=top_k),
        cases,
    )
    b = evaluate(
        r,
        "改造后 · 混合 (LSA增强+BM25含文件名+RRF)",
        lambda q, top_k: r.search(q, top_k=top_k),
        cases,
    )
    denom = len(cases or CASES)
    print("\n===== 变化 =====")
    print(f"Hit@1: {a[0]}/{denom} -> {b[0]}/{denom}  ({(b[0]-a[0])*100//max(denom,1):+d}%)")
    print(f"Hit@5: {a[1]}/{denom} -> {b[1]}/{denom}  ({(b[1]-a[1])*100//max(denom,1):+d}%)")
    print(f"MRR  : {a[2]:.3f} -> {b[2]:.3f}  ({b[2]-a[2]:+.3f})")

    # 两阶段 Rerank 对比：仅当 cross-encoder reranker 实际可用时展示增益
    if r.reranker is not None and r.reranker.available:
        print("\n===== 两阶段 Rerank (RRF → bge-reranker-base → Top-K) =====")
        r_norer = HybridRetriever(enable_rerank=False)
        c = evaluate(
            r_norer,
            "对照 · 仅 RRF（关闭 rerank）",
            lambda q, top_k: r_norer.search(q, top_k=top_k),
            cases,
        )
        print(f"RRF Hit@5        : {c[1]}/{denom}  (MRR {c[2]:.3f})")
        print(f"RRF+rerank Hit@5 : {b[1]}/{denom}  (MRR {b[2]:.3f})")
        print(f"Rerank 增益 Hit@5: {(b[1]-c[1])*100//max(denom,1):+d}%   MRR {b[2]-c[2]:+.3f}")
    else:
        print(
            "\n[提示] reranker 未启用/不可用（sentence_transformers 未安装或模型未下载），"
            "本次仅评估 RRF；装好依赖后重跑可看到两阶段重排增益。"
        )


if __name__ == "__main__":
    main()
