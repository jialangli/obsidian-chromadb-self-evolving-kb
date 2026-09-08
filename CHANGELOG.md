# Changelog

本项目所有重要变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

## [0.2.0] - 2026-09-08

### Added

- **两阶段 Rerank**：RRF Top-50 → bge-reranker-base → Top-5，中文召回 Hit@5 92%→100%（+8%）
- **BGE 神经通道真实可用**：`sentence-transformers` 移入可选 `[bge]` extra，装好即自动启用、缺失自动降级 LSA
- **DiskBM25Index 磁盘倒排**：倒排驻留 SQLite，进程内存与语料规模无关（合成 60k 块实测 11~13MB 恒定）
- **CI 真闸门**：pytest（3.11/3.12/3.13 矩阵）+ 真实 sync + 检索质量门禁（Hit@5 ≥ 85%）
- **闭环灰度实跑**：`closed_loop --apply` 真实晋升 A/B（候选集合与 base 完全隔离）
- 开源规范化：CODE_OF_CONDUCT、README 徽章（CI/Python/Release/License）
- 支持基线抬至 Python 3.11+

### Changed

- `/stats`、`knowledge_base_stats`：改读同步摘要，O(1) 免全量扫描
- `/sync`：改为后台任务执行，新增 `GET /sync/status` 轮询
- `/health`：真实探活（集合缺失返回 503）
- 启动预热：后台预载 LSA/collection，重型检索器首次请求惰性构建
- sync 向量写入：float32 ndarray 直传，去掉整库 float64 转换

### Fixed

- 6 个 P0：PROJECT_ROOT 层级、缺 `main()`、MCP SDK v1/v2 兼容、评估集 gold 文件缺失、`D:\kb-engine` 硬编码、quickstart 导入失效
- 二轮审查 9 项：/stats 全量扫描、/sync 阻塞、无预热、with_retry 语义、sys.path hack、结果缓存淘汰、`.tolist()` 转换、bge 增量同步、/health 假探活
- Chroma `add()` 对已存在 id 静默忽略 → 增量改用 `upsert()`
- chromadb/检索器并发首建竞态 → 加锁串行化

## [0.1.0] - 2026-09-07

### Added

- 首次公开发布
- **核心功能**
  - Obsidian Vault → ChromaDB 同步（支持增量/全量）
  - 混合检索：BGE 语义向量 + BM25 关键词 → RRF 融合
  - LSA 兜底方案（无模型也能用）
  - FastAPI HTTP 服务（4 个端点 + OpenAPI 文档）
  - MCP Server（5 个工具，stdio 传输）
  - 知识库体检（frontmatter/断链/一致性检查）
  - 检索效果评估（Hit@k / MRR）
- **自进化闭环**
  - 反馈数据集构建（三元组）
  - BGE 模型微调（MNRL 对比学习）
  - 候选隔离集合构建
  - 离线门禁评估（Hit@5 阈值）
  - 在线灰度监控
  - 闭环编排（dry-run 默认安全）
- **配置系统**
  - 三层配置：环境变量 > config.yaml > 默认值
  - 所有路径和参数集中管理
- **示例数据**
  - 3 篇带 frontmatter 的示例笔记（产品/技术/方法论）
  - quickstart.py 一键运行脚本
- **文档**
  - README：快速开始、架构、配置、命令、API 参考
  - 架构文档（index.html）：五层架构全景图 + 演进时间线
  - GitHub Pages 在线预览
- **工程化**
  - pyproject.toml（setuptools + src layout）
  - 单元测试 + 集成测试（pytest）
  - .gitignore
  - MIT License

[0.1.0]: https://github.com/jialangli/obsidian-chromadb-self-evolving-kb/releases/tag/v0.1.0
