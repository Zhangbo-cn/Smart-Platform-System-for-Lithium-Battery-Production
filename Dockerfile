# 锂电智能化平台 — 统一 Agent 基础镜像
# 用法: docker build -t battery-agent-base .
#       docker compose -f deploy/docker-compose.platform.yml up -d

FROM python:3.11-slim

WORKDIR /app

# 系统依赖（uvicorn + httpx 需要）
RUN apt-get update -qq && apt-get install -y -qq --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

# 安装 Python 依赖（一层，利用 Docker 缓存）
COPY requirements.txt .
RUN pip install --no-cache-dir -q -r requirements.txt

# 安装本地 packages（可编辑模式）
COPY packages/platform-contracts/ packages/platform-contracts/
COPY packages/harness-core/ packages/harness-core/
RUN pip install --no-cache-dir -q -e packages/platform-contracts \
    && pip install --no-cache-dir -q -e packages/harness-core

# 所有代码在运行时通过 volume 挂载，不需要 COPY
ENV PYTHONPATH=/app:/app/packages/platform-contracts/src:/app/packages/harness-core/src
ENV PYTHONUNBUFFERED=1

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD curl -sf http://localhost:${SERVICE_PORT:-8000}/health || exit 1
