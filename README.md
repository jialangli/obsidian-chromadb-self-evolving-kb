# Obsidian + ChromaDB 自进化知识检索系统

[![CI](https://img.shields.io/github/actions/workflow/status/jialangli/obsidian-chromadb-self-evolving-kb/ci.yml?branch=master&label=CI&logo=github)](https://github.com/jialangli/obsidian-chromadb-self-evolving-kb/actions/workflows/ci.yml)
[![License](https://img.shields.io/github/license/jialangli/obsidian-chromadb-self-evolving-kb?color=blue)](LICENSE)
[![Python](https://img.shields.io/badge/python-3.11%2B-blue?logo=python&logoColor=white)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/jialangli/obsidian-chromadb-self-evolving-kb?sort=semver&color=green&label=release)](https://github.com/jialangli/obsidian-chromadb-self-evolving-kb/releases)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![Ruff](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json)](https://github.com/astral-sh/ruff)
[![GitHub stars](https://img.shields.io/github/stars/jialangli/obsidian-chromadb-self-evolving-kb?style=social)](https://github.com/jialangli/obsidian-chromadb-self-evolving-kb/stargazers)

基于 **Obsidian + ChromaDB** 的个人知识库检索架构，构建「治理 → 向量化 → 混合检索 → Agent 服务 → 数据飞轮自进化」的完整闭环。

纯本地运行，零云依赖。任何时刻删除向量索引，都能通过同步脚本在 30 秒内完整恢复。

## 核心特性

- **五层架构**：数据层（Obsidian Vault）→ 存储层（ChromaDB）→ 检索层（混合检索）→ 服务层（MCP + HTTP 双协议）→ 消费层（Agent 接入）
- **混合检索**：BGE 语义向量 + BM25 关键词，通过 RRF 融合排序，兼顾语义泛化与专有名词精确匹配
- **自动降级**：BGE 模型不可用时无缝降级为 LSA 混合检索，服务永不下线
- **自进化闭环**：从真实使用中收集采纳反馈 → 自动微调嵌入模型 → 离线门禁 + 在线灰度双重验证 → 自动晋升或回滚
- **双协议出口**：MCP Server（stdio，5 个工具）+ FastAPI（HTTP，6 个端点），Agent 与通用程序都能接入
- **治理元数据**：多分类 frontmatter 治理，每条检索结果携带可信度标记

## 快速开始

### 3 分钟跑通示例

```bash
# 1. 克隆项目
git clone https://github.com/jialangli/obsidian-chromadb-self-evolving-kb.git
cd obsidian-chromadb-self-evolving-kb

# 2. 安装（推荐开发模式）
pip install -e ".[dev]"

# （可选）启用 BGE 神经语义 + 两阶段 Rerank：
#   pip install -e ".[bge]"      # 会装 sentence-transformers（连带 torch，体积较大）
#   未装时自动降级为 LSA / 纯 RRF，功能完整可用。

# 3. 一键运行快速入门（自动构建示例索引 + 演示检索）
python quickstart.py
```

就是这么简单！`quickstart.py` 会用 `examples/vault/` 下的示例笔记构建 LSA 向量索引（无需下载任何模型），然后演示混合检索效果。

> 也可以用 `pip install -r requirements.txt` 只装运行时依赖（不含 BGE/rerank）。

### 接入你自己的知识库

```bash
# 1. 复制配置模板
cp config.example.yaml config.yaml

# 2. 编辑 config.yaml，将 vault_path 指向你的 Obsidian Vault
#    vault_path: "/path/to/your/obsidian-vault"
#    注意：config.yaml 必须放在项目根目录（不是 src/ 下）；
#          键名拼错或 YAML 解析失败会在启动时打印警告，不会静默忽略。

# 3. 全量同步构建索引
python -m kb_engine.sync_obsidian_to_chroma --full

# 4. 启动 HTTP API 服务
python -m kb_engine.kb_api_server
# 浏览器打开 http://localhost:8300/docs 查看 API 文档
```

安装后也可以直接用统一 CLI：

```bash
kb sync --full       # 同步知识库（全量）
kb api               # 启动 HTTP API
kb audit             # 知识库体检
kb eval              # 检索效果评估
kb stats             # 查看统计
kb --version         # 版本号
```

### Docker 部署

如果你更喜欢用 Docker，一键启动：

```bash
# 1. 克隆项目
git clone https://github.com/jialangli/obsidian-chromadb-self-evolving-kb.git
cd obsidian-chromadb-self-evolving-kb

# 2. 将你的 Obsidian Vault 软链接或复制到 ./vault 目录
#    ln -s /path/to/your/vault ./vault    # macOS/Linux
#    mklink /D vault C:\path\to\your\vault  # Windows

# 3. 构建并启动
docker compose up -d --build

# 4. 浏览器打开
#    http://localhost:8300/docs
```

数据持久化在 Docker 卷 `kb_data` 中，包含向量库、日志和模型文件。

> 也可以直接 `docker run`：
> ```bash
> docker build -t kb-engine .
> docker run -p 8300:8300 -v /path/to/vault:/app/vault:ro -v kb_data:/app/data kb-engine
> ```

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
├── SECURITY.md                 # 安全政策
├── LICENSE                     # MIT License
├── pyproject.toml              # 项目配置（依赖、构建、工具）
├── requirements.txt            # 运行时依赖清单
├── config.example.yaml         # 配置模板
├── quickstart.py               # 一键快速入门脚本
├── Makefile                    # 常用命令封装
├── Dockerfile                  # Docker 镜像
├── docker-compose.yml          # Docker Compose 编排
├── .dockerignore
├── .pre-commit-config.yaml     # pre-commit 钩子配置
├── .gitignore
│
├── .github/
│   ├── workflows/
│   │   └── ci.yml              # GitHub Actions CI 工作流
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.md       # Bug 报告模板
│   │   ├── feature_request.md  # 功能建议模板
│   │   └── question.md         # 问题求助模板
│   └── PULL_REQUEST_TEMPLATE.md # PR 模板
│
├── src/kb_engine/              # 主包（src layout）
│   ├── __init__.py             # 包初始化，暴露 settings 和版本
│   ├── cli.py                  # 统一 CLI 入口（typer）
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
④ 服务层 · Services         MCP Server (stdio, 5 tools) + FastAPI (:8300, 6 endpoints)
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
| 运行时 | Python 3.11+ | 纯本地，零云依赖 |

## 配置说明

配置优先级（从高到低）：

1. **环境变量**：`KB_` 前缀，如 `KB_API_PORT=8300`
2. **config.yaml**：项目根目录下的配置文件
3. **默认值**：`src/config.py` 中定义的默认值

所有可配置项见 [config.example.yaml](./config.example.yaml)。

## 常用命令

```bash
# 同步知识库（增量）
kb sync

# 全量重建索引
kb sync --full

# 启动 HTTP API
kb api --port 8300

# 知识库体检
kb audit

# 检索效果评估
kb eval

# 查看统计
kb stats
```

> 也可以用 `python -m kb_engine.xxx` 方式调用，效果相同。

## 开发指南

### 代码规范

项目使用以下工具保证代码质量：

| 工具 | 作用 | 配置 |
|------|------|------|
| **black** | 代码格式化 | `pyproject.toml` `[tool.black]` |
| **isort** | import 排序 | `pyproject.toml` `[tool.isort]` |
| **ruff** | 代码检查 | `pyproject.toml` `[tool.ruff]` |
| **mypy** | 类型检查 | `pyproject.toml` `[tool.mypy]` |

```bash
# 安装开发依赖
pip install -e ".[dev]"

# 代码格式化
black src/ tests/
isort src/ tests/

# 代码检查
ruff check src/ tests/

# 类型检查
mypy src/kb_engine/
```

### pre-commit

推荐安装 pre-commit 钩子，提交前自动检查：

```bash
pip install pre-commit
pre-commit install
```

### 运行测试

```bash
# 运行所有测试
pytest

# 查看覆盖率
pytest --cov=kb_engine --cov-report=term-missing
```

### CI/CD

项目使用 GitHub Actions 持续集成，每次 push 和 PR 都会自动运行：

- **Lint**：black + isort + ruff 格式检查
- **Test**：Python 3.11 / 3.12 / 3.13 多版本测试
- **Retrieval gate**：真实 sync + 检索评估，Hit@5 ≥ 85% 才放行

配置文件：`.github/workflows/ci.yml`

更多开发规范请参阅 [CONTRIBUTING.md](./CONTRIBUTING.md)。

## Makefile 速查

项目提供了 `Makefile` 封装常用命令（macOS / Linux / WSL 可用）：

```bash
make help              # 查看所有命令
make install           # 安装运行时依赖
make install-dev       # 安装开发依赖 + pre-commit
make sync              # 同步知识库
make sync-full         # 全量重建索引
make api               # 启动 API 服务
make test              # 运行测试
make test-cov          # 测试 + 覆盖率
make lint              # 代码检查
make format            # 代码格式化
make docker-build      # 构建 Docker 镜像
make docker-up         # 启动 Docker 容器
make docker-down       # 停止 Docker 容器
make clean             # 清理缓存
```

## Docker

详见 [Docker 部署](#docker-部署) 章节。

- `Dockerfile`：多阶段构建，生产级镜像
- `docker-compose.yml`：一键编排，数据卷持久化
- `.dockerignore`：优化构建上下文

## 安全

发现安全漏洞？请查看 [SECURITY.md](./SECURITY.md) 了解报告方式。
**请勿**通过公开 Issue 报告安全问题。

## 版本历史

详见 [CHANGELOG.md](./CHANGELOG.md)。

## MCP 工具清单

| 工具 | 说明 |
|------|------|
| `search_knowledge_base` | 语义检索（自然语言查询），每条结果带 `result_id` |
| `filter_knowledge_base` | 元数据过滤（按类型/状态/来源文件/正文关键词） |
| `knowledge_base_stats` | 知识库统计概览（含灰度 A/B 状态） |
| `record_retrieval_feedback` | 记录检索结果是否被采纳（飞轮反馈，传 `result_id` + `adopted`） |
| `feedback_stats` | 反馈统计（采纳率、被采纳/被忽略最多的文档） |

## API 端点

| 端点 | 方法 | 说明 |
|------|------|------|
| `/health` | GET | 健康检查 |
| `/` | GET | 服务信息与端点索引 |
| `/stats` | GET | 统计概览（块数、文件数、类型分布） |
| `/search` | POST | 混合检索（支持 `memory_type` / `source_file` 过滤） |
| `/filter` | POST | 元数据过滤检索 |
| `/sync` | POST | 触发同步（`{"full": true}` 为全量重建），同步后自动刷新检索器缓存 |

> 反馈埋点走 MCP 的 `record_retrieval_feedback`，HTTP 侧不提供 `/feedback` 端点。

详细文档见 `http://localhost:8300/docs`（启动后访问）。

## 在线文档

架构全景图和详细设计文档：

📖 https://jialangli.github.io/obsidian-chromadb-self-evolving-kb/

## License

[MIT](./LICENSE)
