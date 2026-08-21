PYTHON ?= python3

.PHONY: dev dev-server dev-web check smoke clean

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
		.venv/bin/python app.py

dev-web:
	@cd web && \
		if [ ! -d node_modules ]; then \
			echo "[dev-web] 安装前端依赖..." && npm install --no-fund --no-audit; \
		fi && \
		echo "[dev-web] 前端启动在 http://localhost:8080" && \
		npm run dev

# lint + 单元测试 + 契约测试（T0.2 任务卡实现）
check:
	@echo "T0.2 将实现：lint + pytest + 契约测试"

# 端到端冒烟（T0.2 任务卡实现）
smoke:
	@echo "T0.2 将实现：Playwright E2E"

clean:
	@rm -rf server/.venv server/__pycache__ web/node_modules web/dist
