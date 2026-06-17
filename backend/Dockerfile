FROM python:3.10

# 设置工作目录
WORKDIR /app

# 设置环境变量
ENV PYTHONDONTWRITEBYTECODE=1
# 确保日志即时输出到 Docker Console
ENV PYTHONUNBUFFERED=1
# 设置 Python 路径，确保能导入 app 模块
ENV PYTHONPATH=/app

# 安装系统级依赖
RUN apt-get update && apt-get install -y \
    build-essential \
    curl \
    ca-certificates \
    default-libmysqlclient-dev \
    && rm -rf /var/lib/apt/lists/* \
    && update-ca-certificates

# 1. 先复制依赖文件
COPY requirements.txt .

# 2. 安装 Python 依赖
RUN pip install --no-cache-dir -r requirements.txt

# 3. 复制项目源代码和脚本
COPY app/ ./app/
COPY scripts/ ./scripts/
COPY tests/ ./tests/

# 暴露 FastAPI 端口
EXPOSE 8000

# 启动命令
CMD ["python", "app/server.py"]
