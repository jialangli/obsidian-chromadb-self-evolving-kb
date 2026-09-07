"""
A/B 在线监控与自动晋升/回滚（自进化闭环第⑤步·线上）
====================================================
灰度期间，反馈飞轮会持续记录每条结果被采纳与否，并带上 model_version（= 使用的 bge 集合名）。
本脚本按臂（基线 / 候选）统计采纳率，样本足够时自动决策：

  - 治疗组(候选)采纳率显著低于对照组(基线)  → 自动回滚（关灰度，保持基线）
  - 治疗组采纳率显著不低于对照组            → 全量晋升（候选变基线，关灰度）
  - 样本不足 / 无显著差异                  → 继续灰度，等待更多信号

  python ab_monitor.py            # 演练（只报告决策）
  python ab_monitor.py --apply    # 真正执行晋升/回滚（写 active_model.json）
"""

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import kb_engine.closed_loop_config as cfg  # noqa: N812
import kb_engine.closed_loop_runtime as closed_loop_runtime
import kb_engine.feedback_dataset as feedback_dataset


def _rate(rows):
    if not rows:
        return 0.0, 0
    adopted = sum(1 for r in rows if r.get("adopted"))
    return adopted / len(rows), len(rows)


def decide_ab(baseline_rows, candidate_rows, config=cfg) -> dict:
    """
    纯函数：给定两臂反馈，返回决策。
    Returns: {action, rate_baseline, rate_candidate, n_baseline, n_candidate, detail}
    """
    rate_b, n_b = _rate(baseline_rows)
    rate_c, n_c = _rate(candidate_rows)
    min_n = config.AB_MIN_SAMPLES_PER_ARM

    if n_b < min_n or n_c < min_n:
        action = "keep_ab"
        detail = f"样本不足（基线 {n_b}/候选 {n_c} < 每臂 {min_n}），继续灰度"
    elif rate_c < rate_b - config.AB_ROLLBACK_MAX_DROP:
        action = "rollback_online"
        detail = (
            f"候选采纳率 {rate_c:.1%} 显著低于基线 {rate_b:.1%}"
            f"（差 {rate_b-rate_c:.1%} > 阈值 {config.AB_ROLLBACK_MAX_DROP:.0%}）→ 回滚"
        )
    elif rate_c >= rate_b + config.AB_PROMOTE_MIN_LIFT:
        action = "promote_full"
        detail = (
            f"候选采纳率 {rate_c:.1%} 不低于基线 {rate_b:.1%}"
            f"（提升 {rate_c-rate_b:+.1%}）→ 全量晋升"
        )
    else:
        action = "keep_ab"
        detail = f"候选 {rate_c:.1%} vs 基线 {rate_b:.1%}，差异不显著，继续灰度"

    return {
        "action": action,
        "rate_baseline": rate_b,
        "rate_candidate": rate_c,
        "n_baseline": n_b,
        "n_candidate": n_c,
        "detail": detail,
    }


def monitor(apply: bool = False) -> dict:
    st = cfg.load_active()
    ab = st["ab"]
    if not ab.get("enabled") or not ab.get("candidate_collection"):
        print("[AB] 当前未开启灰度 A/B，无需监控。")
        return {"action": "no_ab"}

    started = ab.get("started_at") or ""
    base_coll = st["active_bge_collection"]
    cand_coll = ab["candidate_collection"]

    feedback = feedback_dataset.load_feedback()
    base_rows, cand_rows = [], []
    for r in feedback:
        mv = r.get("model_version", "")
        ts = r.get("timestamp", "")
        if not mv or ts < started:
            continue
        if mv == base_coll:
            base_rows.append(r)
        elif mv == cand_coll:
            cand_rows.append(r)

    dec = decide_ab(base_rows, cand_rows)
    print(
        f"[AB] 灰度监控：基线({base_coll}) n={dec['n_baseline']} 采纳率={dec['rate_baseline']:.1%}"
        f" | 候选({cand_coll}) n={dec['n_candidate']} 采纳率={dec['rate_candidate']:.1%}"
    )
    print(f"[AB] 决策：{dec['action']} — {dec['detail']}")

    if apply and dec["action"] != "keep_ab":
        if dec["action"] == "rollback_online":
            st["ab"] = {
                k: (
                    False
                    if k == "enabled"
                    else None if k in ("candidate_collection", "candidate_model") else v
                )
                for k, v in st["ab"].items()
            }
            st["ab"]["enabled"] = False
            st["ab"]["candidate_collection"] = None
            st["ab"]["candidate_model"] = None
            cfg.save_active(st)
            closed_loop_runtime.reset_hubs()
            print(f"[AB] ✅ 已自动回滚：关闭灰度，保持基线 {base_coll}。")
        elif dec["action"] == "promote_full":
            st["active_bge_collection"] = cand_coll
            st["active_bge_model"] = ab["candidate_model"]
            st["version"] = int(st.get("version", 0)) + 1
            st["ab"] = {
                "enabled": False,
                "candidate_collection": None,
                "candidate_model": None,
                "traffic_ratio": 0.0,
                "started_at": None,
                "baseline_metrics": None,
            }
            cfg.save_active(st)
            closed_loop_runtime.reset_hubs()
            print(f"[AB] ✅ 已全量晋升：{cand_coll} 成为新基线（version={st['version']}）。")
    elif apply and dec["action"] == "keep_ab":
        print("[AB] [dry-run/保持] 继续灰度，不写状态。")

    cfg.log_run(
        {
            "event": "ab_monitor",
            "action": dec["action"],
            "n_baseline": dec["n_baseline"],
            "n_candidate": dec["n_candidate"],
            "rate_baseline": round(dec["rate_baseline"], 4),
            "rate_candidate": round(dec["rate_candidate"], 4),
            "applied": apply,
        }
    )
    return dec


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true", help="真正执行晋升/回滚")
    args = ap.parse_args()
    monitor(apply=args.apply)
