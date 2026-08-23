#!/usr/bin/env bash
# make smoke 入口：自动起环境 → Playwright → 清理
# 策略（2026-08-23 修订）：不再「复用已有环境」——沙箱/IDE 里手动起的后台进程
# 生命周期不可控（会被会话回收），复用会导致假失败。smoke 一律自管：
# 起自己的服务 → 跑完清理，简单可靠。
set -euo pipefail
cd "$(dirname "$0")/.."

WEB_PORT=8080
API_PORT=8081

port_in_use() {
  lsof -nP -iTCP:"$1" -sTCP:LISTEN >/dev/null 2>&1
}

cleanup() {
  echo "[smoke] 清理脚本启动的服务..."
  for pid in ${SERVER_PID:-} ${WEB_PID:-}; do
    [ -n "$pid" ] && kill "$pid" 2>/dev/null || true
  done
  wait 2>/dev/null || true
}
trap cleanup EXIT

# 残留端口占用（如被会话回收前的半死进程）则先杀，保证干净环境
if port_in_use "$API_PORT" || port_in_use "$WEB_PORT"; then
  echo "[smoke] 端口被占用，先清理残留进程..."
  for p in $(lsof -t -nP -iTCP:"$API_PORT" -sTCP:LISTEN 2>/dev/null || true) \
           $(lsof -t -nP -iTCP:"$WEB_PORT" -sTCP:LISTEN 2>/dev/null || true); do
    kill "$p" 2>/dev/null || true
  done
  sleep 1
fi

echo "[smoke] 启动后端..."
(cd server && [ -d .venv ] || { python3 -m venv .venv; .venv/bin/pip install -q -r requirements.txt; })
# SMTP_FAKE=1：验证码写 /tmp/ppp-fake-mailbox/（E2E 登录用）
rm -rf /tmp/ppp-fake-mailbox
(cd server && SMTP_FAKE=1 .venv/bin/python app.py > /tmp/ppp-smoke-server.log 2>&1) &
SERVER_PID=$!

# 测试用户（幂等）+ 清频控残留（60s 频控会让 E2E 登录 429）
# 注意：必须在 platform/ 根执行 -m server.cli（server 包在根下；cd server 会 ModuleNotFoundError）
server/.venv/bin/python -m server.cli user-add e2e@test.local E2E测试员 >/dev/null 2>&1 || true
server/.venv/bin/python -m server.cli user-add e2e-flow@test.local 登录流测试员 >/dev/null 2>&1 || true
server/.venv/bin/python -m server.cli user-add e2e-reload@test.local 刷新恢复测试员 >/dev/null 2>&1 || true
server/.venv/bin/python -m server.cli user-add e2e-rate@test.local 频控测试员 >/dev/null 2>&1 || true
rm -f "/tmp/ppp-fake-mailbox/e2e@test.local" 2>/dev/null || true
server/.venv/bin/python -c "
from server.models import VerificationCode, init_tables
init_tables()
n = VerificationCode.delete().where(VerificationCode.email << ['e2e@test.local', 'e2e-flow@test.local', 'e2e-reload@test.local', 'e2e-rate@test.local']).execute()
print(f'[smoke] 清理 e2e 频控记录 {n} 条')
" || true

echo "[smoke] 启动前端..."
(cd web && [ -d node_modules ] || npm install --no-fund --no-audit --silent)
(cd web && npm run dev > /tmp/ppp-smoke-web.log 2>&1) &
WEB_PID=$!

echo "[smoke] 等待服务就绪..."
ok=0
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
