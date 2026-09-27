# T12.3：构建上下文改为平台仓库根（compose build context: .）——
# 容器内 /app 与仓库根同构：/app/server/*（包）+ /app/bridge/*（bridge.js 与
# vendor），config.PLATFORM_DIR / proto_proxy 的 bridge 路径都依赖这一布局。
# 同一镜像跑两个服务（compose 覆盖 command）：server（Flask）与 mcp（FastMCP）。
FROM python:3.11-slim

WORKDIR /app

COPY server/requirements.txt /app/server/requirements.txt
RUN pip install --no-cache-dir -r /app/server/requirements.txt

COPY server/ /app/server/
COPY bridge/ /app/bridge/

EXPOSE 8081
EXPOSE 8082

# 默认起 Flask；mcp 服务在 compose 里覆盖为 python server/mcp_server.py
CMD ["python", "server/app.py"]
