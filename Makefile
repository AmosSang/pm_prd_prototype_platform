# 平台仓库根
PLATFORM_DIR := $(shell pwd)

.PHONY: dev dev-server dev-web check smoke clean

# 一键起环境：后端与前端 dev server 各自后台运行
dev:
	@make -j2 dev-server dev-web

dev-server:
	@cd server && (python3 -m venv .venv 2>/dev/null; \
		.venv/bin/pip install -q -r requirements.txt; \
		.venv/bin/python app.py)

dev-web:
	@cd web && (npm install --no-fund --no-audit; npm run dev)

# lint + 单元测试 + 契约测试（T0.2 任务卡实现）
check:
	@echo "T0.2 将实现：lint + pytest + 契约测试"

# 端到端冒烟（T0.2 任务卡实现）
smoke:
	@echo "T0.2 将实现：Playwright E2E"

clean:
	@rm -rf server/.venv server/__pycache__ web/node_modules web/dist
