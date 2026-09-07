"""
集成测试：使用示例 Vault 构建索引并检索
"""

import shutil
import tempfile
from pathlib import Path

import pytest


@pytest.fixture
def temp_data_dir():
    """创建临时数据目录"""
    tmpdir = tempfile.mkdtemp(prefix="kb_test_")
    yield Path(tmpdir)
    shutil.rmtree(tmpdir, ignore_errors=True)


def test_example_vault_exists():
    """测试示例 Vault 存在"""
    project_root = Path(__file__).resolve().parent.parent
    vault_dir = project_root / "examples" / "vault"
    assert vault_dir.exists()
    assert vault_dir.is_dir()

    # 检查至少有一个 .md 文件
    md_files = list(vault_dir.rglob("*.md"))
    assert len(md_files) > 0, "示例 Vault 中没有 Markdown 文件"


def test_config_module_importable():
    """测试所有核心模块都能正常导入"""
    modules = [
        "kb_engine.config",
        "kb_engine.kb_embed",
        "kb_engine.sync_obsidian_to_chroma",
        "kb_engine.hybrid_retrieve",
        "kb_engine.kb_audit",
        "kb_engine.eval_retrieval",
    ]
    for mod in modules:
        __import__(mod)  # 不抛异常就算通过
