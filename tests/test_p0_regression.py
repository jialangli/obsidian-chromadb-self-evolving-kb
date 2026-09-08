"""
P0 回归测试：防止「README 上写着、实际跑不通」这类问题再次出现。

这批 bug 的共同特征是 **import 不报错、只有真正调用才炸**，
所以 CI 里那句 `test_config_module_importable`（只 import 不调用）把它们全放过去了。
这里既检查可导入性，也检查可调用性和配置一致性。
"""

import subprocess
import sys
import tomllib  # Python >= 3.11（pyproject requires-python 已保证）
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def _entry_points() -> dict:
    with open(PROJECT_ROOT / "pyproject.toml", "rb") as f:
        return tomllib.load(f)["project"]["scripts"]


def test_all_entry_points_resolve_to_real_objects():
    """pyproject 声明的每个 console script 都必须指向真实存在的对象"""
    for name, target in _entry_points().items():
        mod_path, sep, attr = target.partition(":")
        assert sep, f"{name}: entry point 缺少 ':' 目标名（{target}）"
        module = __import__(mod_path, fromlist=[attr])
        assert hasattr(module, attr), f"{name} 指向 {target}，但 {mod_path} 没有 {attr}"


@pytest.mark.parametrize(
    "module",
    [
        "kb_engine.sync_obsidian_to_chroma",
        "kb_engine.kb_audit",
        "kb_engine.eval_retrieval",
        "kb_engine.kb_api_server",
    ],
)
def test_module_exposes_callable_main(module):
    """被 entry point 引用的模块必须有可调用的 main()"""
    m = __import__(module, fromlist=["main"])
    assert callable(getattr(m, "main", None)), f"{module}.main 不可调用"


def test_importing_modules_has_no_side_effects():
    """import 不应触发扫描、建目录等副作用（kb_audit 曾把整个扫描写在模块顶层）"""
    before = set()
    for p in PROJECT_ROOT.rglob("*"):
        before.add(str(p.relative_to(PROJECT_ROOT)))

    for mod in ["kb_engine.kb_audit", "kb_engine.config", "kb_engine.eval_retrieval"]:
        __import__(mod)

    after = set()
    for p in PROJECT_ROOT.rglob("*"):
        after.add(str(p.relative_to(PROJECT_ROOT)))
    created = {p for p in after - before if p.split("\\")[0].split("/")[0] in ("data", "src")}
    created = {p for p in created if "__pycache__" not in p}
    assert not created, f"import 产生了副作用，新建了业务目录/文件: {sorted(created)[:5]}"


def test_project_root_points_to_repo_root():
    """PROJECT_ROOT 曾少算一层 parent，导致 config.yaml 静默失效"""
    from kb_engine.config import PROJECT_ROOT as CFG_ROOT

    assert (
        CFG_ROOT.resolve() == PROJECT_ROOT.resolve()
    ), f"PROJECT_ROOT 指向 {CFG_ROOT}，应为仓库根 {PROJECT_ROOT}"
    assert (CFG_ROOT / "pyproject.toml").exists()
    # README 让用户把 config.yaml 放项目根，代码也必须去那里找
    assert (CFG_ROOT / "examples" / "vault").exists()


def test_example_vault_matches_eval_cases():
    """评测集每条 gold 都必须能在示例库中命中，否则闸门永远不可能通过"""
    from kb_engine.eval_retrieval import CASES

    vault = PROJECT_ROOT / "examples" / "vault"
    files = {str(p.relative_to(vault)).replace("\\", "/")[:-3] for p in vault.rglob("*.md")}
    assert files, "示例库为空"

    dead = [(q, gold) for q, gold in CASES if not any(gold in f for f in files)]
    assert not dead, (
        f"{len(dead)}/{len(CASES)} 条 gold 在示例库中不可达，"
        f"会永久压低 Hit@5 使离线闸门无法通过：{dead}"
    )
    # 顺带约束 gold 精度：一条 gold 匹配过多文件会让指标虚高
    loose = [
        (q, gold, len([f for f in files if gold in f]))
        for q, gold in CASES
        if len([f for f in files if gold in f]) > 3
    ]
    assert not loose, f"以下 gold 过于宽泛（各匹配多个文件），指标会虚高：{loose}"


def test_closed_loop_paths_are_portable():
    """闭环路径必须派生自项目根，不能写死某台机器的绝对路径（曾写死 D:\\kb-engine）"""
    import os

    import kb_engine.closed_loop_config as cfg

    override = os.environ.get("KB_ROOT")
    for name in ("KB_ROOT", "LOGS", "MODELS_DIR", "FEEDBACK_PATH", "TRACE_PATH"):
        value = Path(str(getattr(cfg, name)))
        assert "kb-engine" not in value.parts or override, f"{name} 疑似硬编码了本机路径: {value}"
        if not override:
            # 未用 KB_ROOT 覆盖时，必须落在项目根之下（即随仓库迁移）
            assert str(PROJECT_ROOT.resolve()) in str(
                value.resolve()
            ), f"{name} 未派生自项目根: {value}"


def test_closed_loop_uses_same_collection_as_sync():
    """闭环默认激活的集合必须是同步脚本真正创建的那一个"""
    import kb_engine.closed_loop_config as cfg
    from kb_engine.sync_obsidian_to_chroma import COLLECTION_NAME_BGE

    active = cfg.load_active()["active_bge_collection"]
    assert active == COLLECTION_NAME_BGE, (
        f"闭环激活集合 {active} 与同步脚本写入的 {COLLECTION_NAME_BGE} 不一致，"
        "闭环会指向一个不存在的集合"
    )


def test_mcp_and_closed_loop_share_feedback_file():
    """MCP 写入的反馈必须能被闭环读到，否则飞轮断链"""
    import kb_engine.closed_loop_config as cfg

    # MCP server 模块较重（依赖 mcp SDK），这里只校验闭环侧路径与配置来源一致
    assert cfg.FEEDBACK_PATH.name == "feedback.jsonl"
    assert cfg.TRACE_PATH.name == "mcp_trace.jsonl"
    assert cfg.FEEDBACK_PATH.parent == cfg.TRACE_PATH.parent


def test_cli_help_runs():
    """`kb --help` 必须能真正跑起来（直接覆盖 CLI 装配链路）"""
    env = {"PYTHONPATH": str(PROJECT_ROOT / "src")}
    proc = subprocess.run(
        [sys.executable, "-m", "kb_engine.cli", "--help"],
        capture_output=True,
        text=True,
        cwd=str(PROJECT_ROOT),
        env={**__import__("os").environ, **env},
    )
    assert proc.returncode == 0, f"kb --help 失败：{proc.stderr[-500:]}"
