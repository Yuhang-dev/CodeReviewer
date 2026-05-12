FROM python:3.10-slim

# 设置工作目录
WORKDIR /app

# 设置环境变量，防止 python 缓存 pyc 文件，强制无缓冲输出
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1

# 安装基础依赖
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 将代码复制进容器
COPY . .

# 暴露 FastAPI 端口
EXPOSE 8000
