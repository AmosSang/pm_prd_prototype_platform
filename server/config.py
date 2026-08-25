"""配置：环境变量驱动，开发环境提供默认值。"""
import os

PLATFORM_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DATA_DIR = os.environ.get("DATA_DIR", os.path.join(PLATFORM_DIR, "data"))
DB_PATH = os.path.join(DATA_DIR, "platform.db")
# T8.1 去 Git 本地化：项目内容目录（每项目 prototype/prd/reviews 三目录）
PROJECTS_DIR = os.path.join(DATA_DIR, "projects")

# T1.1 起：开发期演示项目 demo 直接指向 fixture
DEMO_REPO_DIR = os.environ.get("DEMO_REPO_DIR", os.path.join(PLATFORM_DIR, "tests", "fixtures"))

# 上传限制（T8.2 上传接口用；T8.1 先定常量）
PROTO_ZIP_MAX_BYTES = 100 * 1024 * 1024    # 原型 zip ≤ 100MB
PROTO_UNZIP_MAX_BYTES = 300 * 1024 * 1024  # 解压总量 ≤ 300MB（防炸弹）
PROTO_UNZIP_MAX_FILES = 5000               # 条目数上限
PRD_MAX_BYTES = 5 * 1024 * 1024            # PRD markdown ≤ 5MB
SHOT_MAX_BYTES = 10 * 1024 * 1024          # 截图 ≤ 10MB

PLATFORM_SECRET = os.environ.get("PLATFORM_SECRET", "dev-secret-change-me")

# 超级管理员邮箱（T2.1 增强）：初始启动时写入 users 表为超管（name=admin，is_admin=True）
ADMIN_EMAIL = os.environ.get("ADMIN_EMAIL", "")

SMTP_HOST = os.environ.get("SMTP_HOST", "")
SMTP_PORT = int(os.environ.get("SMTP_PORT", "25"))
SMTP_USER = os.environ.get("SMTP_USER", "")
SMTP_PASS = os.environ.get("SMTP_PASS", "")
# T2.1 增强：163 等免费邮箱用隐式 SSL（465/994），需 SMTP_USE_SSL=1；
# SMTP_FROM 独立发件地址（缺省沿用 SMTP_USER）
SMTP_USE_SSL = os.environ.get("SMTP_USE_SSL", "0").lower() in ("1", "true", "yes", "on")
SMTP_FROM = os.environ.get("SMTP_FROM", "")

PORT = int(os.environ.get("PORT", "8081"))

# 前端 dev server origin（Vite :8080）
WEB_ORIGIN = os.environ.get("WEB_ORIGIN", "http://localhost:8080")
