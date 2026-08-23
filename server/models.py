"""数据模型（peewee + SQLite，WAL 模式）。

表随任务卡逐步增加：
- T2.1: users / verification_codes
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


def init_tables() -> None:
    """建表（幂等）。应用启动与测试 fixture 共用。"""
    db.connect(reuse_if_open=True)
    db.create_tables([User, VerificationCode], safe=True)
