FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8081

# 开发期直接跑；生产换 gunicorn（T7.2 收尾时切换）
CMD ["python", "app.py"]
