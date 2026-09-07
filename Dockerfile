# syntax=docker/dockerfile:1.6

FROM python:3.11-slim AS builder

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 安装构建依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# 先复制依赖文件，利用 Docker 缓存
COPY pyproject.toml requirements.txt ./

# 安装运行时依赖
RUN pip install --upgrade pip && \
    pip install -e .

# ---- 运行时镜像 ----
FROM python:3.11-slim AS runtime

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# 复制 site-packages
COPY --from=builder /usr/local/lib/python3.11/site-packages /usr/local/lib/python3.11/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

# 复制项目代码
COPY src/ ./src/
COPY config.example.yaml ./config.yaml
COPY examples/ ./examples/

# 数据卷：挂载你的 Obsidian Vault 和数据目录
VOLUME ["/app/data", "/app/vault"]

# 环境变量（可用 docker run -e 覆盖）
ENV KB_VAULT_PATH=/app/vault \
    KB_CHROMA_PATH=/app/data/chroma \
    KB_LOG_PATH=/app/data/logs \
    KB_MODELS_DIR=/app/data/models \
    KB_API_HOST=0.0.0.0 \
    KB_API_PORT=8300

EXPOSE 8300

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8300/health')" || exit 1

CMD ["kb", "api", "--host", "0.0.0.0", "--port", "8300"]
