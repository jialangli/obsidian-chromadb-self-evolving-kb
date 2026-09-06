# Obsidian + ChromaDB 自进化知识检索系统

基于 **Obsidian + ChromaDB** 的个人知识库检索架构，构建「治理 → 向量化 → 混合检索 → Agent 服务 → 数据飞轮自进化」的完整闭环。

纯本地运行，零云依赖。任何时刻删除向量索引，都能通过同步脚本在 30 秒内完整恢复。

## 核心特性

- **五层架构**：数据层（Obsidian Vault）→ 存储层（ChromaDB）→ 检索层（混合检索）→ 服务层（MCP + HTTP 双协议）→ 消费层（Agent 接入）
- **混合检索**：BGE 语义向量（512d）+ BM25 关键词，通过 RRF 融合排序，兼顾语义泛化与专有名词精确匹配
- **自动降级**：BGE 模型不可用时无缝降级为 LSA 混合检索，服务永不下线
- **自进化闭环**：从真实使用中收集采纳反馈 → 自动微调嵌入模型 → 离线门禁 + 在线灰度双重验证 → 自动晋升或回滚
- **双协议出口**：MCP Server（stdio，5 个工具）+ FastAPI（HTTP，4 个端点），Agent 与通用程序都能接入
- **治理元数据**：六类 frontmatter 分类（fact / preference / experience / safety / task_state / navigation），每条检索结果携带可信度标记

## 快速开始

### 在线预览

直接在浏览器中打开 [`index.html`](./index.html) 即可查看完整架构文档（含 Mermaid 流程图、数据表格、演进时间线）。

或访问 GitHub Pages 在线版（启用后自动生成）。

### 本地运行

```bash
git clone https://github.com/jialangli/obsidian-chromadb-self-evolving-kb.git
cd obsidian-chromadb-self-evolving-kb
# 直接用浏览器打开 index.html
```

文档为纯静态页面，无需安装任何依赖。所有字体和脚本均已内置。

## 架构概览

```
⑤ 消费层 · Consumers        WorkBuddy (MCP) / TRAE / 任意 HTTP Agent
④ 服务层 · Services         MCP Server (stdio, 5 tools) + FastAPI (:8300, 4 endpoints)
③ 检索层 · Retrieval        Hybrid Retriever (BGE + BM25 → RRF)
② 存储层 · Storage           ChromaDB (kb_bge 512d 主通道 / kb_lsa 384d 兜底)
① 数据层 · Data              Obsidian Vault (Markdown + frontmatter 治理)
```

**核心设计原则**：Obsidian Vault 是唯一事实源（Source of Truth），向量库只是可全量重建的派生索引。

## 自进化闭环

系统从真实使用中收集 Agent 采纳反馈，自动微调嵌入模型，经三重安全闸验证后晋升：

| 安全机制 | 规则 | 意义 |
|---------|------|------|
| 演练默认 | 闭环默认 dry-run，仅 `--apply` 显式触发 | 误操作零风险 |
| 候选隔离 | 微调模型写入独立集合，绝不触碰线上基线 | 实验失败不伤基线 |
| 双重门禁 | 离线：Hit@5 ≥ 0.6 且无回归；在线：灰度采纳率监控 | 先证明不坏，再谈变好 |

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 知识源 | Obsidian | Markdown + frontmatter，唯一事实源 |
| 向量库 | ChromaDB | 持久化存储，支持全量重建 |
| 嵌入模型 | bge-small-zh-v1.5 | 512 维，中文优化 |
| 兜底嵌入 | LSA (TF-IDF + SVD) | 384 维，完全离线可用 |
| 检索策略 | BGE + BM25 → RRF | 双通道融合，k=60 |
| 服务层 | MCP Server + FastAPI | 双协议出口 |
| 运行时 | Python 3.13 | 纯本地，零云依赖 |

## 项目结构

```
obsidian-chromadb-self-evolving-kb/
├── index.html              # 架构文档（GitHub Pages 兼容）
├── README.md               # 项目说明
├── LICENSE                 # MIT License
├── assets/
│   └── charts.js           # 图表脚本
└── _shared/
    ├── fonts/              # 内置字体（Outfit, JetBrainsMono 等）
    └── js/
        └── mermaid.min.js  # Mermaid 流程图库
```

## 工程目录参考

实际知识库引擎的工程结构（本文档描述的 `kb-engine` 目录）：

```
kb-engine/
├── venv/                        # Python 虚拟环境
├── chroma_data/                 # ChromaDB 持久化 + LSA/TF-IDF 模型
├── scripts/                     # 核心脚本
│   ├── sync_obsidian_to_chroma.py   # 同步：Vault → 向量库
│   ├── hybrid_retrieve.py           # 混合检索核心
│   ├── kb_api_server.py             # FastAPI 服务
│   ├── kb_mcp_server.py             # MCP Server
│   ├── feedback_dataset.py          # 反馈 → 训练三元组
│   ├── fine_tune_bge.py            # MNRL 对比微调
│   ├── build_candidate_index.py     # 候选隔离集合构建
│   ├── ab_eval.py / ab_monitor.py   # 离线门禁 / 在线灰度监控
│   └── closed_loop*.py              # 闭环编排
├── logs/                        # 调用记录 / 反馈 / 审计日志
└── backup/                      # 变更前快照
```

## License

[MIT](./LICENSE)
