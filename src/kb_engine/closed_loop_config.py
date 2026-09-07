"""
闭环中央配置与状态管理
======================
自进化闭环（数据飞轮 → 微调 → 候选 → 评估 → 灰度 → 回滚/晋升）的单一事实来源：

  - 路径常量（脚本/日志/模型/反馈）
  - active_model.json：当前线上激活的 bge 集合与模型 + A/B 灰度状态
  - 评估闸门阈值（离线自动评估是否达标）
  - A/B 在线监控阈值（灰度期间是否晋升/回滚）
  - closed_loop_runs.jsonl：每次闭环运行的审计日志

默认值与现状完全一致（base bge 集合 + 无 A/B），因此接入后不改变任何线上行为，
除非闭环编排器显式写入新的激活状态。
"""

import json
import os
from datetime import datetime
from pathlib import Path

from kb_engine.config import PROJECT_ROOT, settings

# ── 路径 ──────────────────────────────────────────────
# 全部从 settings 派生（与 config.py 同一套路径体系），不再硬编码本机绝对路径。
# 需要把闭环数据放到别处时，用 KB_ROOT 环境变量覆盖。
_KB_ROOT_ENV = os.environ.get("KB_ROOT")
KB_ROOT = Path(_KB_ROOT_ENV) if _KB_ROOT_ENV else PROJECT_ROOT / "data"
LOGS = Path(settings.log_path) if not _KB_ROOT_ENV else KB_ROOT / "logs"
MODELS_DIR = Path(settings.models_dir) if not _KB_ROOT_ENV else KB_ROOT / "models"
FEEDBACK_PATH = LOGS / "feedback.jsonl"
TRACE_PATH = LOGS / "mcp_trace.jsonl"
ACTIVE_MODEL_PATH = LOGS / "active_model.json"
RUN_LOG_PATH = LOGS / "closed_loop_runs.jsonl"

# ── 现状默认值（与未接入闭环时一致）────────────────────
# 集合名必须取自 settings，否则闭环会指向一个同步脚本从未创建过的集合。
DEFAULT_ACTIVE = {
    "version": 0,
    "active_bge_collection": settings.collection_bge,
    "active_bge_model": settings.bge_model_name,
    "ab": {
        "enabled": False,
        "candidate_collection": None,
        "candidate_model": None,
        "traffic_ratio": 0.0,
        "started_at": None,
        "baseline_metrics": None,
    },
    "updated_at": None,
}

# ── 离线评估闸门阈值（候选 vs 基线）────────────────────
# 候选「达标」= KPI 驱动：主指标(MRR)不退化 + 高绝对质量地板(Hit@5>=0.85) + 下限(>=0.60)。
# 设计取舍：基线 bge-small-zh 在 45 条 gold 集已达 95.6% Hit@5，是小样本微调的天花板；
# 微调常呈现「hit1↑/MRR↑ 但 hit5 微降」的权衡，要求全指标零回归会永远卡住迭代。
# 因此离线闸门只负责「放行入口」（主 KPI 不退化 + 整体质量仍优秀），真正的强护栏是
# 线上 A/B 监控（AB_ROLLBACK_MAX_DROP=0.10）：候选进 20% 灰度后由真实用户采纳率裁决。
EVAL_MIN_HIT5_FLOOR = 0.60  # 候选 Hit@5 绝对下限（不能"没那么差但还是差"）
EVAL_MIN_HIT5_PROMOTE_FLOOR = 0.85  # 晋升所需高绝对质量地板（微调后整体质量仍须优秀）
EVAL_REGRESSION_TOLERANCE = 0.02  # 主 KPI(MRR) 相对基线的退化容忍度
# 早期迭代（bootstrap 合成数据驱动的微调）回归容忍度：允许候选相对基线有 <=2% 的小幅
# 波动仍能进入灰度 A/B。真正的强护栏是线上 A/B 监控（AB_ROLLBACK_MAX_DROP=0.10），
# 离线 2% 容忍仅用于让"近乎中性"的候选先进入灰度、开始积累真实反馈（数据飞轮燃料）。
EVAL_MIN_DELTA_MRR = 0.0
EVAL_MIN_DELTA_HIT1 = 0.0

# ── A/B 在线监控阈值（灰度期间按采纳率决策）───────────
AB_TRAFFIC_RATIO = 0.20  # 默认灰度流量比例（20% 走候选）
AB_MIN_SAMPLES_PER_ARM = 10  # 每臂最小样本数才做决策（不足则继续灰度）
AB_PROMOTE_MIN_LIFT = 0.02  # 治疗组采纳率须高于对照组至少 2pp 才全量晋升（平局归于继续灰度）
AB_ROLLBACK_MAX_DROP = 0.10  # 治疗组比对照组低超过该值 → 自动回滚

# ── 触发阈值 ──────────────────────────────────────────
LOOP_MIN_POSITIVE_FEEDBACK = 8  # 至少多少条「采纳」正反馈才自动触发训练（否则等更多数据）


def _now():
    return datetime.now().isoformat(timespec="seconds")


def load_active() -> dict:
    """读取激活状态；文件不存在/损坏则返回默认（与现状一致）。"""
    if not ACTIVE_MODEL_PATH.exists():
        return dict(DEFAULT_ACTIVE)
    try:
        with open(ACTIVE_MODEL_PATH, encoding="utf-8") as f:
            data = json.load(f)
        merged = dict(DEFAULT_ACTIVE)
        merged.update({k: v for k, v in data.items() if k != "ab"})
        merged["ab"] = {**DEFAULT_ACTIVE["ab"], **(data.get("ab") or {})}
        return merged
    except Exception:
        return dict(DEFAULT_ACTIVE)


def save_active(state: dict):
    """写回激活状态（同时更新 updated_at）。"""
    state = dict(state)
    state["updated_at"] = _now()
    ACTIVE_MODEL_PATH.parent.mkdir(parents=True, exist_ok=True)
    with open(ACTIVE_MODEL_PATH, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def next_model_version() -> int:
    """下一个候选模型版本号（基于当前激活 version）。"""
    return int(load_active().get("version", 0)) + 1


def candidate_collection_name(version: int) -> str:
    """候选集合命名：与 base 集合区分，绝不冲突。"""
    return f"{settings.collection_bge}_ft_v{version}"


def log_run(record: dict):
    """追加一条闭环运行审计记录。"""
    RUN_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    record = dict(record)
    record.setdefault("timestamp", _now())
    with open(RUN_LOG_PATH, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def read_runs() -> list:
    if not RUN_LOG_PATH.exists():
        return []
    rows = []
    with open(RUN_LOG_PATH, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    return rows


if __name__ == "__main__":
    print(json.dumps(load_active(), ensure_ascii=False, indent=2))
