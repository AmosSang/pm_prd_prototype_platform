"""配置：环境变量驱动，开发环境提供默认值。"""
import os

PLATFORM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(PLATFORM_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "platform.db")
REPOS_DIR = os.path.join(DATA_DIR, "repos")

# T1.1 起：开发期演示项目 demo 直接指向 fixture（T2.3 实现真实 clone 后移除）
DEMO_REPO_DIR = os.environ.get("DEMO_REPO_DIR", os.path.join(PLATFORM_DIR, "tests", "fixtures"))

PLATFORM_SECRET = os.environ.get("PLATFORM_SECRET", "dev-secret-change-me")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")

PORT = int(os.environ.get("PORT", "8081"))

# 前端 dev server origin（Vite :8080）
WEB_ORIGIN = os.environ.get("WEB_ORIGIN", "http://localhost:8080")
