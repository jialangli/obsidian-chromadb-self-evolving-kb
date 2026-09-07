"""
知识库引擎配置模块

所有路径和参数统一从这里读取，支持：
1. 环境变量覆盖
2. config.yaml 配置文件（可选）
3. 默认值（基于项目相对路径）

用法：
    from config import settings
    print(settings.vault_path)
"""
import os
import json
from pathlib import Path

# 项目根目录（src 的父目录）
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 默认配置
DEFAULTS = {
    # 路径
    "vault_path": str(PROJECT_ROOT / "examples" / "vault"),
    "chroma_path": str(PROJECT_ROOT / "data" / "chroma"),
    "log_path": str(PROJECT_ROOT / "data" / "logs"),
    "lsa_model_path": str(PROJECT_ROOT / "data" / "chroma" / "lsa_model.pkl"),
    "tfidf_vectorizer_path": str(PROJECT_ROOT / "data" / "chroma" / "tfidf_vectorizer.pkl"),
    "feedback_log": str(PROJECT_ROOT / "data" / "logs" / "feedback.jsonl"),
    "trace_log": str(PROJECT_ROOT / "data" / "logs" / "mcp_trace.jsonl"),
    "active_model_file": str(PROJECT_ROOT / "data" / "logs" / "active_model.json"),
    "models_dir": str(PROJECT_ROOT / "data" / "models"),

    # 集合名
    "collection_lsa": "kb_lsa",
    "collection_bge": "kb_bge",
    "collection_bge_candidate": "kb_bge_candidate",

    # 模型
    "bge_model_name": "BAAI/bge-small-zh-v1.5",
    "lsa_dimensions": 384,
    "bge_dimensions": 512,

    # 检索
    "top_k": 10,
    "rrf_k": 60,
    "chunk_size": 1500,
    "chunk_overlap": 200,

    # 服务
    "api_host": "127.0.0.1",
    "api_port": 8300,

    # 闭环
    "closed_loop_threshold": 8,  # 正例数触发微调
    "dry_run": True,             # 默认演练模式
    "hit_at_5_threshold": 0.6,   # 离线门禁 Hit@5 阈值
}


class Settings:
    """配置对象，支持属性访问和字典访问"""

    def __init__(self):
        self._data = dict(DEFAULTS)
        self._load_yaml()
        self._load_env()
        self._ensure_dirs()

    def _load_yaml(self):
        """从 config.yaml 加载（如果存在）"""
        yaml_path = PROJECT_ROOT / "config.yaml"
        if yaml_path.exists():
            try:
                import yaml
                with open(yaml_path, "r", encoding="utf-8") as f:
                    user_config = yaml.safe_load(f) or {}
                self._data.update(user_config)
            except ImportError:
                pass  # pyyaml 未安装时跳过
            except Exception:
                pass

    def _load_env(self):
        """从环境变量加载（KB_ 前缀）"""
        for key in self._data:
            env_key = "KB_" + key.upper()
            if env_key in os.environ:
                val = os.environ[env_key]
                # 类型转换
                if isinstance(self._data[key], bool):
                    self._data[key] = val.lower() in ("1", "true", "yes")
                elif isinstance(self._data[key], int):
                    self._data[key] = int(val)
                elif isinstance(self._data[key], float):
                    self._data[key] = float(val)
                else:
                    self._data[key] = val

    def _ensure_dirs(self):
        """确保必要的目录存在"""
        for key in ("chroma_path", "log_path", "models_dir"):
            path = Path(self._data[key])
            if key == "log_path":
                path.mkdir(parents=True, exist_ok=True)
            elif key == "chroma_path":
                path.mkdir(parents=True, exist_ok=True)
            elif key == "models_dir":
                path.mkdir(parents=True, exist_ok=True)

    def __getattr__(self, name):
        if name.startswith("_"):
            raise AttributeError(name)
        if name in self._data:
            return self._data[name]
        raise AttributeError(f"Settings has no attribute '{name}'")

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def to_dict(self):
        return dict(self._data)


# 全局单例
settings = Settings()
