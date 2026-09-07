# 贡献指南

感谢你对本项目的关注！任何形式的贡献都欢迎，包括但不限于：

- 🐛 提交 Bug 报告
- 💡 提出新功能建议
- 📝 改进文档
- 🔧 提交代码修复或新功能
- ✅ 帮助审核 PR

## 快速开始

### 环境准备

```bash
# 1. Fork 并克隆仓库
git clone https://github.com/your-username/obsidian-chromadb-self-evolving-kb.git
cd obsidian-chromadb-self-evolving-kb

# 2. 创建虚拟环境
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 3. 安装开发依赖
pip install -e ".[dev]"
```

### 运行测试

```bash
# 运行所有测试
pytest

# 运行测试并查看覆盖率
pytest --cov=kb_engine --cov-report=term-missing

# 运行特定测试文件
pytest tests/test_config.py -v
```

### 代码风格

项目使用以下工具保证代码质量：

- **black**：代码格式化
- **isort**：import 排序
- **ruff**：代码检查

```bash
# 格式化代码
black src/ tests/
isort src/ tests/

# 代码检查
ruff check src/ tests/
```

## 提交 PR 的流程

1. **Fork 仓库**并创建你的特性分支
   ```bash
   git checkout -b feature/amazing-feature
   ```

2. **做出修改**并确保：
   - 代码通过 lint 检查
   - 所有测试通过
   - 如果你加了新功能，补充对应的测试
   - 更新相关文档（README、架构文档等）

3. **提交更改**
   ```bash
   git add .
   git commit -m "feat: add amazing feature"
   ```
   提交信息建议使用 [Conventional Commits](https://www.conventionalcommits.org/zh-hans/v1.0.0/) 格式：
   - `feat:` 新功能
   - `fix:` Bug 修复
   - `docs:` 文档更新
   - `refactor:` 重构
   - `test:` 测试相关
   - `chore:` 构建/工具链相关

4. **推送到你的 Fork**
   ```bash
   git push origin feature/amazing-feature
   ```

5. **创建 Pull Request**
   - 清晰描述改动内容和目的
   - 关联相关 issue（如果有）
   - 勾选 PR 模板中的检查项

## Issue 指南

### Bug 报告

提交 Bug 时请提供以下信息：

- **环境信息**：Python 版本、操作系统、项目版本
- **复现步骤**：清晰描述如何复现问题
- **预期行为**：你认为应该发生什么
- **实际行为**：实际发生了什么
- **错误日志**：完整的错误堆栈（如果有）

### 功能建议

描述你想要的功能、为什么需要它，以及可能的实现思路。

## 代码规范

### 项目结构

```
src/kb_engine/          # 主包
  config.py             # 配置模块
  sync_obsidian_to_chroma.py  # 同步脚本
  hybrid_retrieve.py    # 混合检索
  kb_api_server.py      # HTTP API
  kb_mcp_server.py      # MCP Server
  ...
tests/                  # 测试
examples/               # 示例数据
```

### 命名约定

- 模块名：小写 + 下划线（snake_case）
- 类名：大驼峰（PascalCase）
- 函数/方法名：小写 + 下划线（snake_case）
- 常量：全大写 + 下划线（UPPER_SNAKE_CASE）

### 文档

- 每个公共函数/类应有 docstring
- 复杂逻辑处添加行内注释
- README 及时更新

## 行为准则

本项目采用 [Contributor Covenant](https://www.contributor-covenant.org/) 行为准则，
参与即表示你同意遵守其条款。

简单来说：
- 尊重他人的观点和经验
- 接受建设性批评
- 关注社区整体利益
- 对其他社区成员保持友善

## 有问题？

可以通过以下方式寻求帮助：

1. 先搜索已有 [Issues](https://github.com/jialangli/obsidian-chromadb-self-evolving-kb/issues)
2. 如果没有相关问题，新建一个并打上 `question` 标签

再次感谢你的贡献！🎉
