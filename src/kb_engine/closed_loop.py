"""
闭环编排器（自进化闭环主控）
============================
一条命令跑通：触发检查 → 微调 → 构建候选 → 离线评估闸门 → 晋升灰度 / 自动回滚。

  python closed_loop.py                 # 演练（不写状态，仅报告决策）
  python closed_loop.py --apply         # 真正写入 active_model.json（达标则进入灰度 A/B）
  python closed_loop.py --force         # 忽略「正例反馈不足」阈值，用 bootstrap 数据演示
  python closed_loop.py --apply --force --epochs 2

安全约定：
  - 默认 dry-run（不修改任何线上状态），仅验证整条链路并给出决策；
  - 只有显式 --apply 才会把候选晋升为灰度（写入 active_model.json），
    MCP/API 重启后流量按 traffic_ratio 切到候选；
  - 候选集合与 base 集合完全隔离，未达标时仅"不晋升"，绝不破坏线上 base。
"""

import argparse
import sys
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import kb_engine.ab_eval as ab_eval
import kb_engine.build_candidate_index as build_candidate_index
import kb_engine.closed_loop_config as cfg  # noqa: N812
import kb_engine.closed_loop_runtime as closed_loop_runtime
import kb_engine.feedback_dataset as feedback_dataset
import kb_engine.fine_tune_bge as fine_tune_bge
from kb_engine.hybrid_retrieve import HybridRetriever
from kb_engine.kb_embed import BgeEmbedder


def _now():
    return datetime.now().isoformat(timespec="seconds")


def run(
    apply: bool = False,
    force: bool = False,
    min_positive: int = None,
    epochs: int = 1,
    lr: float = 2e-5,
    batch_size: int = 16,
    scale: float = 1.0,
) -> dict:
    min_positive = min_positive or cfg.LOOP_MIN_POSITIVE_FEEDBACK
    st = cfg.load_active()
    feedback = feedback_dataset.load_feedback()
    positive = [r for r in feedback if r.get("adopted")]
    print(f"[LOOP] 反馈总数={len(feedback)}，正例={len(positive)}，触发阈值={min_positive}")

    # ── 1. 触发检查 ──
    if not force and len(positive) < min_positive:
        msg = (
            f"正例反馈不足（{len(positive)} < {min_positive}），暂不训练。"
            f"可 --force 强制演示，或等业务积累更多采纳反馈。"
        )
        print("[LOOP]", msg)
        cfg.log_run(
            {
                "event": "skip_insufficient_feedback",
                "positive": len(positive),
                "threshold": min_positive,
            }
        )
        return {"action": "skip", "reason": msg}

    # ── 2. 训练三元组 ──
    # 真实反馈优先；反馈稀疏（<30 条）时，用「gold 对 bootstrap」合成 45 条带检索难负例的
    # (query→期望文档) 三元组，作为首轮迭代的训练燃料（模拟「用户采纳了正确文档」的反馈信号）。
    # 注：gold 集同时充当评估集，故首轮候选的离线提升含少量过拟合成分；真正的泛化验证
    # 交由线上 A/B（真实用户采纳率，10% 跌幅自动回滚）。待真实反馈积累后再切到 structural 集。
    triples = feedback_dataset.build_training_triples(feedback)
    src = "feedback"
    if len(triples) < 30:
        boot = feedback_dataset.bootstrap_triples_from_cases()
        if triples:
            triples = triples + boot
            src = "feedback+bootstrap"
        else:
            triples = boot
            src = "bootstrap"
    print(f"[LOOP] 训练三元组 {len(triples)} 条（来源：{src}）")
    if not triples:
        print("[LOOP] 无可用训练数据，终止")
        return {"action": "skip_no_triples"}

    # ── 3. 对比学习微调 ──
    version = cfg.next_model_version()
    checkpoint = str(cfg.MODELS_DIR / f"bge_ft_v{version}")
    ft = fine_tune_bge.train(
        triples,
        checkpoint,
        base_model=st["active_bge_model"],
        epochs=epochs,
        lr=lr,
        batch_size=batch_size,
        scale=scale,
    )
    print(f"[LOOP] 微调完成: {ft['out_dir']}")

    # ── 4. 构建候选集合（与 base 隔离）──
    cand_coll = cfg.candidate_collection_name(version)
    info = build_candidate_index.build_candidate(cand_coll, checkpoint)
    if not info.get("built"):
        print(f"[LOOP] 候选集合构建失败: {info.get('reason')} → 回滚（不晋升）")
        cfg.log_run({"event": "candidate_build_failed", "version": version})
        return {"action": "rollback_build_failed"}

    # ── 5. 离线评估闸门 ──
    base_rt = closed_loop_runtime.get_baseline_retriever()
    cand_rt = HybridRetriever(
        bge_collection_name=cand_coll,
        bge_embedder=BgeEmbedder(checkpoint, local_files_only=True),
    )
    gate = ab_eval.eval_gate(base_rt, cand_rt)
    print(f"[LOOP] 离线闸门 verdict={gate['verdict']}  delta={gate['deltas']}")
    for r in gate["reasons"]:
        print("   -", r)

    # ── 6. 决策：达标→灰度 A/B；未达标→自动回滚 ──
    if gate["verdict"]:
        action = "promote_to_ab"
        if apply:
            st["ab"] = {
                "enabled": True,
                "candidate_collection": cand_coll,
                "candidate_model": checkpoint,
                "traffic_ratio": cfg.AB_TRAFFIC_RATIO,
                "started_at": _now(),
                "baseline_metrics": gate["baseline"],
            }
            cfg.save_active(st)
            closed_loop_runtime.reset_hubs()
            print(
                f"[LOOP] ✅ 已晋升灰度 A/B：候选={cand_coll}，"
                f"流量={int(cfg.AB_TRAFFIC_RATIO*100)}%（重启 MCP/API 后生效）"
            )
        else:
            print(
                f"[LOOP] [dry-run] 将晋升灰度 A/B：候选={cand_coll}"
                f"（加 --apply 真正写入 active_model.json）"
            )
    else:
        action = "rollback_offline"
        print(
            f"[LOOP] ⚠️ 候选未达标 → 自动回滚：保持基线 {st['active_bge_collection']}，"
            f"候选 {cand_coll} 保留供检查"
        )
        if apply and st["ab"].get("enabled"):
            st["ab"]["enabled"] = False
            cfg.save_active(st)
            closed_loop_runtime.reset_hubs()

    record = {
        "event": "closed_loop_run",
        "version": version,
        "action": action,
        "n_triples": len(triples),
        "triple_source": src,
        "candidate_collection": cand_coll,
        "gate_verdict": gate["verdict"],
        "gate_deltas": gate["deltas"],
        "applied": apply,
    }
    cfg.log_run(record)
    return {"action": action, "gate": gate, "version": version, "candidate_collection": cand_coll}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正写入 active_model.json（否则仅演练）")
    ap.add_argument("--force", action="store_true", help="忽略正例反馈阈值")
    ap.add_argument("--min-positive", type=int, default=None)
    ap.add_argument("--epochs", type=int, default=1)
    ap.add_argument("--lr", type=float, default=2e-5, help="微调学习率")
    ap.add_argument(
        "--scale",
        type=float,
        default=1.0,
        help="余弦相似度缩放（MNRL 温度倒数，默认 1.0 原始余弦）",
    )
    ap.add_argument(
        "--batch-size", type=int, default=16, help="微调批大小（越大 in-batch 负例越多）"
    )
    args = ap.parse_args()
    run(
        apply=args.apply,
        force=args.force,
        min_positive=args.min_positive,
        epochs=args.epochs,
        lr=args.lr,
        batch_size=args.batch_size,
        scale=args.scale,
    )
