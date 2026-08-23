#!/usr/bin/env bash
# make smoke 入口：自动起环境 → Playwright → 清理
# 端口被占用时视为环境已由开发者手动启动（make dev），直接复用，结束后不杀
set -euo pipefail
cd "$(dirname "$0")/.."

WEB_PORT=8080
API_PORT=8081
STARTED_BY_SCRIPT=0

port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

cleanup() {
  if [ "$STARTED_BY_SCRIPT" -eq 1 ]; then
    echo "[smoke] 清理脚本启动的服务..."
    # set -u 下未定义变量会炸 cleanup；用默认空值兜底
    for pid in ${SERVER_PID:-} ${WEB_PID:-}; do
      [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
    done
    wait 2>/dev/null || true
  else
    echo "[smoke] 复用已有环境，不清理"
  fi
}
trap cleanup EXIT

if ! port_in_use "$API_PORT"; then
  echo "[smoke] 启动后端..."
  (cd server && [ -d .venv ] || { python3 -m venv .venv; .venv/bin/pip install -q -r requirements.txt; })
  (cd server && .venv/bin/python app.py > /tmp/ppp-smoke-server.log 2>&1) &
  SERVER_PID=$!
  STARTED_BY_SCRIPT=1
fi

if ! port_in_use "$WEB_PORT"; then
  echo "[smoke] 启动前端..."
  (cd web && [ -d node_modules ] || npm install --no-fund --no-audit --silent)
  (cd web && npm run dev > /tmp/ppp-smoke-web.log 2>&1) &
  WEB_PID=$!
  STARTED_BY_SCRIPT=1
fi

echo "[smoke] 等待服务就绪..."
for i in $(seq 1 30); do
  ok=0
  curl -s -m 2 "http://localhost:$API_PORT/api/health" >/dev/null 2>&1 && ok=$((ok+1))
  curl -s -m 2 -o /dev/null "http://localhost:$WEB_PORT/" && ok=$((ok+1))
  [ "$ok" -eq 2 ] && break
  sleep 1
done

if [ "$ok" -ne 2 ]; then
  echo "[smoke] 服务未就绪，查看日志：/tmp/ppp-smoke-server.log /tmp/ppp-smoke-web.log"
  exit 1
fi

echo "[smoke] 运行 Playwright..."
cd tests
[ -d node_modules ] || npm install --no-fund --no-audit --silent
npx playwright install chromium 2>/dev/null | tail -1 || true
npm run smoke
