# syntax=docker/dockerfile:1
# ============================================================
# CropGuard CPU Dockerfile
# 102类农业害虫检测系统 - CPU版本
# 构建: docker build -f docs/docker/Dockerfile -t pest-detection:cpu .
# 运行: docker run -p 8080:8080 pest-detection:cpu
# ============================================================
FROM python:3.10-slim

# 系统依赖
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    git \
    curl \
    ca-certificates \
    libgl1 \
    libglib2.0-0 \
    libxext6 libxrender1 libsm6 \
    libgomp1 \
    xvfb \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# 复制并安装 Python 依赖
COPY requirements-server.txt ./
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements-server.txt \
    && pip install --no-cache-dir torch torchvision --index-url https://download.pytorch.org/whl/cpu

# 复制项目文件
COPY . .

# 创建输出目录
RUN mkdir -p output

ENV PYTHONUNBUFFERED=1 \
    QT_X11_NO_MITSHM=1

EXPOSE 8080

# 默认启动 Web API 服务器
CMD ["python", "api_server.py", "--host", "0.0.0.0", "--port", "8080"]
