# Obsidian + ChromaDB 自进化知识检索系统

基于 **Obsidian + ChromaDB** 的个人知识库检索架构，构建「治理 → 向量化 → 混合检索 → Agent 服务 → 数据飞轮自进化」的完整闭环。

纯本地运行，零云依赖。任何时刻删除向量索引，都能通过同步脚本在 30 秒内完整恢复。

## 核心特性

- **五层架构**：数据层（Obsidian Vault）→ 存储层（ChromaDB）→ 检索层（混合检索）→ 服务层（MCP + HTTP 双协议）→ 消费层（Agent 接入）
- **混合检索**：BGE 语义向量 + BM25 关键词，通过 RRF 融合排序，兼顾语义泛化与专有名词精确匹配
- **自动降级**：BGE 模型不可用时无缝降级为 LSA 混合检索，服务永不下线
- **自进化闭环**：从真实使用中收集采纳反馈 → 自动微调嵌入模型 → 离线门禁 + 在线灰度双重验证 → 自动晋升或回滚
- **双协议出口**：MCP Server（stdio，5 个工具）+ FastAPI（HTTP，4 个端点），Agent 与通用程序都能接入
- **治理元数据**：多分类 frontmatter 治理，每条检索结果携带可信度标记

## 快速开始

### 3 分钟跑通示例

```bash
# 1. 克隆项目
git clone https://github.com/jialangli/obsidian-chromadb-self-evolving-kb.git
cd obsidian-chromadb-self-evolving-kb

# 2. 安装（推荐开发模式）
pip install -e ".[dev]"

# 3. 一键运行快速入门（自动构建示例索引 + 演示检索）
python quickstart.py
```

就是这么简单！`quickstart.py` 会用 `examples/vault/` 下的示例笔记构建 LSA 向量索引（无需下载任何模型），然后演示混合检索效果。

> 也可以用 `pip install -r requirements.txt` 只装运行时依赖。

### 接入你自己的知识库

```bash
# 1. 复制配置模板
cp config.example.yaml config.yaml

# 2. 编辑 config.yaml，将 vault_path 指向你的 Obsidian Vault
#    vault_path: "/path/to/your/obsidian-vault"

# 3. 全量同步构建索引
python -m kb_engine.sync_obsidian_to_chroma --full

# 4. 启动 HTTP API 服务
python -m kb_engine.kb_api_server
# 浏览器打开 http://localhost:8300/docs 查看 API 文档
```

安装后也可以直接用 CLI 命令：

```bash
kb-sync --full     # 等同于 python -m kb_engine.sync_obsidian_to_chroma
kb-api             # 等同于 python -m kb_engine.kb_api_server
kb-audit           # 等同于 python -m kb_engine.kb_audit
kb-eval            # 等同于 python -m kb_engine.eval_retrieval
```

### 接入 MCP 客户端

在 MCP 客户端的配置文件中添加：

```json
{
  "mcpServers": {
    "kb-engine": {
      "command": "python",
      "args": ["-m", "kb_engine.kb_mcp_server"],
      "cwd": "/path/to/obsidian-chromadb-self-evolving-kb"
    }
  }
}
```

## 项目结构

```
obsidian-chromadb-self-evolving-kb/
├── index.html                  # 架构文档（GitHub Pages 在线预览）
├── README.md                   # 项目说明（本文件）
├── CHANGELOG.md                # 版本变更记录
├── CONTRIBUTING.md             # 贡献指南
├── LICENSE                     # MIT License
├── pyproject.toml              # 项目配置（依赖、构建、工具）
├── requirements.txt            # 运行时依赖清单
├── config.example.yaml         # 配置模板
├── quickstart.py               # 一键快速入门脚本
├── .gitignore
│
├── src/kb_engine/              # 主包（src layout）
│   ├── __init__.py             # 包初始化，暴露 settings
│   ├── config.py               # 统一配置模块（环境变量 + config.yaml + 默认值）
│   ├── sync_obsidian_to_chroma.py  # Vault → ChromaDB 同步脚本
│   ├── kb_embed.py             # BGE 嵌入模型封装
│   ├── hybrid_retrieve.py      # 混合检索核心（BGE + BM25 → RRF）
│   ├── kb_api_server.py        # FastAPI HTTP 服务
│   ├── kb_mcp_server.py        # MCP Server（stdio）
│   ├── kb_api_guard.py         # API 守护进程（自动重启）
│   ├── kb_audit.py             # 知识库体检（frontmatter/断链/一致性）
│   ├── eval_retrieval.py       # 检索效果评估
│   │
│   ├── feedback_dataset.py     # 反馈 → 训练三元组
│   ├── fine_tune_bge.py        # MNRL 对比微调
│   ├── build_candidate_index.py # 候选隔离集合构建
│   ├── ab_eval.py              # 离线门禁评估
│   ├── ab_monitor.py           # 在线灰度监控
│   ├── closed_loop.py          # 闭环编排
│   ├── closed_loop_config.py   # 闭环配置
│   └── closed_loop_runtime.py  # 闭环运行时
│
├── tests/                      # 测试（pytest）
│   ├── test_config.py          # 配置模块测试
│   ├── test_retriever.py       # 检索模块测试
│   └── test_integration.py     # 集成测试
│
├── examples/
│   └── vault/                  # 示例知识库（3 篇带 frontmatter 的笔记）
│       ├── 产品/智能设备产品手册.md
│       ├── 技术/向量检索原理.md
│       └── 方法论/知识库治理方法论.md
│
├── data/                       # 运行时数据（默认 .gitignore）
│   ├── chroma/                 # ChromaDB 持久化 + LSA 模型
│   ├── logs/                   # 调用日志/反馈/审计
│   └── models/                 # 微调模型
│
├── assets/                     # 文档资源
│   └── charts.js
└── _shared/                    # 文档字体和脚本
    ├── fonts/
    └── js/mermaid.min.js
```

## 架构概览

```
⑤ 消费层 · Consumers        MCP 客户端 / TRAE / 任意 HTTP Agent
④ 服务层 · Services         MCP Server (stdio, 5 tools) + FastAPI (:8300, 4 endpoints)
③ 检索层 · Retrieval        Hybrid Retriever (BGE + BM25 → RRF)
② 存储层 · Storage           ChromaDB (kb_bge 主通道 / kb_lsa 兜底)
① 数据层 · Data              Obsidian Vault (Markdown + frontmatter 治理)
```

**核心设计原则**：Obsidian Vault 是唯一事实源（Source of Truth），向量库只是可全量重建的派生索引。

## 自进化闭环

系统从真实使用中收集 Agent 采纳反馈，自动微调嵌入模型，经三重安全闸验证后晋升：

| 安全机制 | 规则 | 意义 |
|---------|------|------|
| 演练默认 | 闭环默认 dry-run，仅显式触发 apply | 误操作零风险 |
| 候选隔离 | 微调模型写入独立集合，绝不触碰线上基线 | 实验失败不伤基线 |
| 双重门禁 | 离线：Hit@5 ≥ 阈值且无回归；在线：灰度采纳率监控 | 先证明不坏，再谈变好 |

## 技术栈

| 组件 | 选型 | 说明 |
|------|------|------|
| 知识源 | Obsidian | Markdown + frontmatter，唯一事实源 |
| 向量库 | ChromaDB | 持久化存储，支持全量重建 |
| 嵌入模型 | bge-small-zh-v1.5 | 512 维，中文优化 |
| 兜底嵌入 | LSA (TF-IDF + SVD) | 纯离线可用，零模型依赖 |
| 检索策略 | BGE + BM25 → RRF | 双通道融合 |
| 服务层 | MCP Server + FastAPI | 双协议出口 |
| 运行时 | Python 3.9+ | 纯本地，零云依赖 |

## 配置说明

配置优先级（从高到低）：

1. **环境变量**：`KB_` 前缀，如 `KB_API_PORT=8300`
2. **config.yaml**：项目根目录下的配置文件
3. **默认值**：`src/config.py` 中定义的默认值

所有可配置项见 [config.example.yaml](./config.example.yaml)。

## 常用命令

```bash
# 同步知识库（增量）
python -m kb_engine.sync_obsidian_to_chroma

# 全量重建索引
python -m kb_engine.sync_obsidian_to_chroma --full

# 启动 HTTP API
python -m kb_engine.kb_api_server

# 知识库体检
python -m kb_engine.kb_audit

# 检索效果评估
python -m kb_engine.eval_retrieval

# 启动 API 守护（自动重启）
python -m kb_engine.kb_api_guard
```

## 开发指南

### 运行测试

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 运行所有测试
pytest

# 查看覆盖率
pytest --cov=kb_engine --cov-report=term-missing
```

### 代码规范

```bash
# 格式化
black src/ tests/
isort src/ tests/

# 检查
ruff check src/ tests/
```

更多开发规范请参阅 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## 版本历史

详见 [CHANGELOG.md](./CHANGELOG.md)。

## MCP 工具清单

| 工具 | 说明 |
|------|------|
| `search_knowledge_base` | 语义检索（自然语言查询） |
| `filter_knowledge_base` | 元数据过滤（按类型/状态/标签） |
| `knowledge_base_stats` | 知识库统计概览 |
| `get_knowledge_entry` | 获取单条笔记全文 |
| `mark_feedback` | 标记检索结果是否有用（飞轮反馈） |

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/search` | GET/POST | 混合检索 |
| `/stats` | GET | 统计概览 |
| `/feedback` | POST | 提交反馈 |

详细文档见 `http://localhost:8300/docs`（启动后访问）。

## 在线文档

架构全景图和详细设计文档：

📖 https://jialangli.github.io/obsidian-chromadb-self-evolving-kb/

## License

[MIT](./LICENSE)
