"""配置：环境变量驱动，开发环境提供默认值。"""
import os

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(os.path.dirname(__file__), "..", "data"))
DB_PATH = os.path.join(DATA_DIR, "platform.db")
REPOS_DIR = os.path.join(DATA_DIR, "repos")

PLATFORM_SECRET = os.environ.get("PLATFORM_SECRET", "dev-secret-change-me")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")

PORT = int(os.environ.get("PORT", "8081"))

# 前端 dev server origin（Vite :8080）
WEB_ORIGIN = os.environ.get("WEB_ORIGIN", "http://localhost:8080")
