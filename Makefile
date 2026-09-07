.PHONY: install install-dev sync api audit eval test lint format clean docker-build docker-up docker-down help

.DEFAULT_GOAL := help

# 变量
PYTHON ?= python
PIP ?= pip
DOCKER_COMPOSE ?= docker compose

help: ## 显示帮助信息
	@echo "kb-engine - 自进化知识检索系统"
	@echo ""
	@echo "可用命令："
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-20s\033[0m %s\n", $$1, $$2}'

# ---------- 安装 ----------

install: ## 安装运行时依赖
	$(PIP) install -e .

install-dev: ## 安装开发依赖（含测试、lint、格式化）
	$(PIP) install -e ".[dev]"
	pre-commit install

# ---------- 运行 ----------

sync: ## 同步知识库（增量）
	kb sync

sync-full: ## 全量重建索引
	kb sync --full

api: ## 启动 HTTP API 服务
	kb api

audit: ## 知识库体检
	kb audit

eval: ## 检索效果评估
	kb eval

stats: ## 显示知识库统计
	kb stats

quickstart: ## 运行快速入门示例
	$(PYTHON) quickstart.py

# ---------- 开发 ----------

test: ## 运行测试
	pytest -v

test-cov: ## 运行测试并显示覆盖率
	pytest --cov=kb_engine --cov-report=term-missing

lint: ## 代码检查（ruff + black + isort）
	ruff check src/ tests/
	black --check src/ tests/
	isort --check --profile black src/ tests/

format: ## 代码格式化（black + isort + ruff --fix）
	black src/ tests/ quickstart.py
	isort --profile black src/ tests/ quickstart.py
	ruff check --fix src/ tests/ quickstart.py

mypy: ## 类型检查
	mypy src/kb_engine/

# ---------- Docker ----------

docker-build: ## 构建 Docker 镜像
	$(DOCKER_COMPOSE) build

docker-up: ## 启动 Docker 容器
	$(DOCKER_COMPOSE) up -d

docker-down: ## 停止 Docker 容器
	$(DOCKER_COMPOSE) down

docker-logs: ## 查看 Docker 日志
	$(DOCKER_COMPOSE) logs -f

# ---------- 清理 ----------

clean: ## 清理缓存文件
	find . -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
	find . -type d -name "*.egg-info" -exec rm -rf {} + 2>/dev/null || true
	rm -rf .pytest_cache .mypy_cache .ruff_cache build dist
