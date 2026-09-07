"""
kb_engine - Obsidian + ChromaDB 自进化知识检索系统

一个纯本地运行的个人知识库检索引擎，支持：
- Obsidian Vault → ChromaDB 同步
- 混合检索（BGE 向量 + BM25 关键词 → RRF 融合）
- MCP Server + FastAPI 双协议出口
- 自进化闭环（反馈 → 微调 → 门禁 → 晋升）
"""

__version__ = "0.1.0"
__all__ = [
    "config",
    "sync",
    "embed",
    "retriever",
    "audit",
]

from kb_engine.config import settings  # noqa: F401
