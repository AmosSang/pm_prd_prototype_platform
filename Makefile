PYTHON ?= python3

.PHONY: dev dev-server dev-web check smoke clean help

help:
	@echo "make dev     一键起开发环境（前后端并行，Ctrl+C 停止）"
	@echo "make check   lint + 单测/契约测试 + 前端构建检查（全绿才算过）"
	@echo "make smoke   自动起环境跑 Playwright E2E 后清理"
	@echo "make clean   清理所有依赖与构建产物"

# 一键起开发环境：前后端在本终端内并行运行，Ctrl+C 一起停止
dev:
	@echo "═══ 启动开发环境 ═══"
	@echo "说明：本命令会同时启动后端(:8081)和前端(:8080)，并保持终端运行。"
	@echo "      直接 Ctrl+C 即可一起停止；关闭终端也会同时停止两者。"
	@echo "      如需分别控制，可在两个终端分别跑：make dev-server / make dev-web"
	@echo ""
	@$(MAKE) -j2 dev-server dev-web

dev-server:
	@cd server && \
		if [ ! -d .venv ]; then \
			echo "[dev-server] 创建 venv..." && $(PYTHON) -m venv .venv; \
		fi && \
		echo "[dev-server] 安装依赖..." && \
		.venv/bin/pip install -q -r requirements.txt && \
		echo "[dev-server] 后端启动在 http://localhost:8081" && \
		echo "[dev-server] SMTP_FAKE=1（验证码写入 /tmp/ppp-fake-mailbox/，真实 SMTP 由 compose 部署启用）" && \
		SMTP_FAKE=1 .venv/bin/python app.py

dev-web:
	@cd web && \
		if [ ! -d node_modules ]; then \
			echo "[dev-web] 安装前端依赖..." && npm install --no-fund --no-audit; \
		fi && \
		echo "[dev-web] 前端启动在 http://localhost:8080" && \
		npm run dev

# ─── 验收命令 ───────────────────────────────────────────────
# lint + 单元测试 + 契约测试 + 前端类型检查，全绿才算过
check:
	@echo "═══ [1/4] Python lint (ruff) ═══"
	@cd server && .venv/bin/ruff check ../server ../tests
	@echo "═══ [2/4] Python 单元测试 + 契约测试 (pytest) ═══"
	@cd server && .venv/bin/python -m pytest ../tests -v
	@echo "═══ [3/4] 前端类型检查 (vue-tsc) ═══"
	@cd web && npm run build --silent
	@echo "═══ [4/4] 前端 lint (vue-tsc -b 已含) ═══"
	@echo ""
	@echo "✅ check 全绿"

# 端到端冒烟：自动起环境 → 跑 Playwright → 清理
smoke:
	@bash tests/run-smoke.sh

clean:
	@rm -rf server/.venv server/__pycache__ web/node_modules web/dist tests/node_modules tests/playwright-report tests/test-results
