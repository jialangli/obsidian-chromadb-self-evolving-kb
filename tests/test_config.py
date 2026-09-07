"""
配置模块单元测试
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest


def test_settings_defaults():
    """测试默认配置是否正确加载"""
    from kb_engine.config import settings, DEFAULTS

    assert settings is not None
    # 检查几个关键默认值
    assert settings.collection_lsa == DEFAULTS["collection_lsa"]
    assert settings.collection_bge == DEFAULTS["collection_bge"]
    assert settings.top_k == DEFAULTS["top_k"]
    assert settings.rrf_k == DEFAULTS["rrf_k"]
    assert settings.api_port == DEFAULTS["api_port"]


def test_settings_dict_access():
    """测试字典访问方式"""
    from kb_engine.config import settings

    assert settings["top_k"] == settings.top_k
    assert settings.get("nonexistent_key", "fallback") == "fallback"


def test_settings_to_dict():
    """测试 to_dict 方法"""
    from kb_engine.config import settings

    d = settings.to_dict()
    assert isinstance(d, dict)
    assert "top_k" in d
    assert "collection_lsa" in d


def test_env_override():
    """测试环境变量覆盖配置"""
    os.environ["KB_TOP_K"] = "42"
    # 重新导入以触发环境变量读取
    import importlib
    from kb_engine import config
    importlib.reload(config)

    assert config.settings.top_k == 42

    # 清理
    del os.environ["KB_TOP_K"]
    importlib.reload(config)


def test_project_root_is_dir():
    """测试 PROJECT_ROOT 指向正确的目录"""
    from kb_engine.config import PROJECT_ROOT

    assert isinstance(PROJECT_ROOT, Path)
    assert PROJECT_ROOT.exists()
    assert PROJECT_ROOT.is_dir()
    # 项目根目录应该包含 src 目录
    assert (PROJECT_ROOT / "src").exists() or (PROJECT_ROOT.parent / "src").exists()


def test_bge_embedder_class_exists():
    """测试 BgeEmbedder 类可以被导入"""
    from kb_engine.kb_embed import BgeEmbedder

    assert BgeEmbedder is not None
    assert callable(BgeEmbedder)
    # 检查方法存在
    assert hasattr(BgeEmbedder, "encode_docs")
    assert hasattr(BgeEmbedder, "encode_query")
