"""
BGE 对比学习微调（自进化闭环第②步）
====================================
以当前激活的 bge 模型为起点，在 (query, 正例文档) 对上用 MultipleNegativesRankingLoss
做对比学习微调（in-batch 负例），产出新的 checkpoint。

  - 完全离线：local_files_only=True
  - query 侧加 BGE 指令前缀，与推理时 BgeEmbedder.encode_query 保持一致
  - 不依赖 sentence_transformers 的 model.fit（其需要 datasets 包），改用纯 torch 手动实现 MNRL
  - 不修改 base 模型，新权重写入独立目录 models/bge_ft_vN

用法：
  python fine_tune_bge.py --triples-file tri.json --out models/bge_ft_candidate
  # 或在代码里调用 train(triples, base_model, out_dir)
"""

import os

os.environ.setdefault("HF_HUB_OFFLINE", "1")
os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")

import argparse
import json
import random
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from kb_engine.kb_embed import BGE_QUERY_PREFIX
from kb_engine.kb_embed import MODEL_NAME as DEFAULT_BASE_MODEL


def train(
    triples: list,
    out_dir: str,
    base_model: str = DEFAULT_BASE_MODEL,
    epochs: int = 1,
    batch_size: int = 8,
    lr: float = 2e-5,
    seed: int = 42,
    scale: float = 20.0,
    hard_neg_weight: float = 1.0,
):
    """
    Args:
        triples: [(query, pos_text, neg_text), ...]
                 第三个元素可以是 str，也可以是 list[str]（多个难负例）
        out_dir: 新模型保存目录
        base_model: 起点模型（本地路径或 HF 名，需离线可加载）
        scale: 余弦相似度缩放（InfoNCE 温度倒数）。sentence-transformers 的
               MultipleNegativesRankingLoss 默认 20；此前用 1.0 导致 logits 挤在
               [-1,1]、梯度极弱，是「训了等于没训」的主因之一。
        hard_neg_weight: 显式难负例损失项的权重
    Returns:
        dict: {"out_dir", "n_examples", "base_model", "final_loss", "n_with_hard_neg"}
    """
    import torch
    from sentence_transformers import SentenceTransformer, util

    if not triples:
        raise ValueError("triples 为空，无法微调")

    rng = random.Random(seed)

    examples = []
    for t in triples:
        q, pos, neg = t[0], t[1], t[2]
        if isinstance(neg, (list, tuple)):
            negs = [n for n in neg if n]
        else:
            negs = [neg] if neg else []
        examples.append((BGE_QUERY_PREFIX + q, pos, negs))

    n_hard = sum(1 for e in examples if e[2])
    print(f"[FT] 训练样本数: {len(examples)}，含难负例: {n_hard}，起点模型: {base_model}")

    model = SentenceTransformer(base_model, device="cpu", local_files_only=True)
    model.train()
    optimizer = torch.optim.AdamW(model.parameters(), lr=lr)
    loss_fn = torch.nn.CrossEntropyLoss()

    def _encode(texts):
        # 用内部 forward 保持梯度连通（model.encode 会 detach，无法反传）
        if hasattr(model, "preprocess"):
            feats = model.preprocess(texts)
        else:
            feats = model.tokenize(texts)
        out = model(feats)
        emb = out["sentence_embedding"]
        return torch.nn.functional.normalize(emb, p=2, dim=1)

    n = len(examples)
    n_batches = max(1, (n + batch_size - 1) // batch_size)
    final_loss = 0.0
    for ep in range(epochs):
        idx = list(range(n))
        rng.shuffle(idx)
        ep_loss = 0.0
        for i in range(0, n, batch_size):
            b = idx[i : i + batch_size]
            qs = [examples[j][0] for j in b]
            ps = [examples[j][1] for j in b]
            # 训练时保持梯度连通（train 模式），归一化后算余弦相似度
            q_emb = _encode(qs)
            p_emb = _encode(ps)

            # 项①：in-batch MNRL（批内其他样本的正例充当负例）
            sim = util.cos_sim(q_emb, p_emb) * scale
            labels = torch.arange(len(b), device=sim.device)
            loss = loss_fn(sim, labels)  # 对角线为正例

            # 项②：显式难负例 InfoNCE —— 把「模型当前真正会混淆的 chunk」压下去。
            # 随机负例模型早已能区分，梯度近乎为零；难负例才是有效信号。
            hard_terms = []
            for row, j in enumerate(b):
                negs = examples[j][2]
                if not negs:
                    continue
                n_emb = _encode(negs)  # (k, d)
                cand = torch.cat([p_emb[row : row + 1], n_emb], dim=0)  # (1+k, d)
                logits = (q_emb[row : row + 1] @ cand.T) * scale  # (1, 1+k)
                target = torch.zeros(1, dtype=torch.long, device=logits.device)
                hard_terms.append(loss_fn(logits, target))
            if hard_terms:
                loss = loss + hard_neg_weight * torch.stack(hard_terms).mean()

            optimizer.zero_grad()
            loss.backward()
            optimizer.step()
            ep_loss += loss.item()
        avg = ep_loss / n_batches
        final_loss = avg
        print(f"[FT] epoch {ep + 1}/{epochs}  avg_loss={avg:.4f}")

    Path(out_dir).parent.mkdir(parents=True, exist_ok=True)
    model.save(out_dir)
    print(f"[FT] 微调完成，保存至: {out_dir}")
    return {
        "out_dir": out_dir,
        "n_examples": n,
        "base_model": base_model,
        "final_loss": round(final_loss, 4),
        "n_with_hard_neg": n_hard,
        "scale": scale,
    }


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--triples-file", help="含三元组 [[q,pos,neg],...] 的 json 文件")
    ap.add_argument("--out", required=True, help="新模型保存目录")
    ap.add_argument("--base-model", default=DEFAULT_BASE_MODEL)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--batch-size", type=int, default=8)
    args = ap.parse_args()

    if args.triples_file:
        with open(args.triples_file, encoding="utf-8") as f:
            triples = json.load(f)
    else:
        import kb_engine.feedback_dataset as fd

        fb = fd.load_feedback()
        triples = fd.build_training_triples(fb) or fd.bootstrap_triples_from_cases()
    train(
        triples,
        args.out,
        base_model=args.base_model,
        epochs=args.epochs,
        batch_size=args.batch_size,
    )
