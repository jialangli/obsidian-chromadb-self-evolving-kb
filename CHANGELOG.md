# Changelog

本项目所有重要变更都记录在此文件中。

格式基于 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/)，
版本号遵循 [Semantic Versioning](https://semver.org/lang/zh-CN/)。

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
