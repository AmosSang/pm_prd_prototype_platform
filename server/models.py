"""数据模型（peewee + SQLite，WAL 模式）。

表随任务卡逐步增加：
- T2.1: users / verification_codes
- T2.3: projects
"""
import datetime as dt

import peewee

from server.config import DB_PATH


def utcnow_str() -> str:
    """统一 UTC ISO 时间戳（DB 全部存这个格式，比较用字符串即可）。"""
    return dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


def parse_utc(s: str) -> dt.datetime:
    return dt.datetime.strptime(s, "%Y-%m-%dT%H:%M:%S.%fZ").replace(tzinfo=dt.timezone.utc)


class Database(peewee.SqliteDatabase):
    """SQLite + WAL + 外键约束。"""

    def connect(self, *args, **kwargs):
        rv = super().connect(*args, **kwargs)
        self.execute_sql("PRAGMA journal_mode=WAL;")
        self.execute_sql("PRAGMA foreign_keys=ON;")
        return rv


db = Database(DB_PATH, pragmas={"busy_timeout": 5000})


class BaseModel(peewee.Model):
    class Meta:
        database = db


class User(BaseModel):
    id = peewee.AutoField(primary_key=True)
    email = peewee.CharField(unique=True, null=False)
    name = peewee.CharField(null=False)
    # admin 标记：一期管理员由 CLI/DB 工具直接维护，不提供界面
    is_admin = peewee.BooleanField(default=False)
    created_at = peewee.CharField(default=utcnow_str)


class VerificationCode(BaseModel):
    email = peewee.CharField(null=False, index=True)
    code = peewee.CharField(null=False)
    expires_at = peewee.CharField(null=False)
    used = peewee.BooleanField(default=False)
    created_at = peewee.CharField(default=utcnow_str)


class Project(BaseModel):
    """项目（T2.3）。project_id 用随机短 slug（kebab-case），是本地 clone 目录名
    与 /proto/{project_id}/ 路径前缀，与数字主键 id 分离——路径不可猜测遍历。"""
    id = peewee.AutoField(primary_key=True)
    project_id = peewee.CharField(unique=True, null=False)
    name = peewee.CharField(null=False)
    repo_url = peewee.CharField(null=False)
    # Fernet 加密的 git token（crypto_util 加解密；任何接口不回传）
    encrypted_token = peewee.CharField(null=False)
    branch = peewee.CharField(default="main", null=False)
    # 项目级「可评论」开关（T4.x 用）
    commentable = peewee.BooleanField(default=True)
    # 同步状态（T3.3 SYNC_PULL 用；T2.3 先记录 clone 结果）
    last_sync_at = peewee.CharField(null=True)
    sync_error = peewee.CharField(null=True)
    created_at = peewee.CharField(default=utcnow_str)


def init_tables() -> None:
    """建表（幂等）。应用启动与测试 fixture 共用。"""
    db.connect(reuse_if_open=True)
    db.create_tables([User, VerificationCode, Project], safe=True)
