"""数据模型（peewee + SQLite，WAL 模式）。

表随任务卡逐步增加：
- T2.1: users / verification_codes
- T2.3: projects
- T4.2: comments（评论展示缓存，事实源为仓库 reviews/）
- T4.3: git_tasks（落仓任务状态，队列本体在内存 server/git_tasks.py）
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
    # 项目级「可评论」开关（T4.5 接口化；表结构 T2.3 预留）
    commentable = peewee.BooleanField(default=True)
    # 同步状态（T3.3 SYNC_PULL 用；T2.3 先记录 clone 结果）
    last_sync_at = peewee.CharField(null=True)
    sync_error = peewee.CharField(null=True)
    created_at = peewee.CharField(default=utcnow_str)


class Comment(BaseModel):
    """评论（T4.2，展示缓存；事实源为仓库 reviews/comments/*.json）。

    评论 JSON 全量（含截图路径、doc 关联、interaction_state）落 payload_json；
    常用筛选字段（status/priority/scope/target_type/prototype_page/anchor_id）
    冗余成列，评论列表抽屉（T4.4）查询免 JSON 解析。
    comment_id 格式 c-YYYYMMDD-NNN（当日序号，跨 DB 与仓库文件查重顺延）。
    """
    id = peewee.AutoField(primary_key=True)
    comment_id = peewee.CharField(unique=True, null=False)
    project = peewee.ForeignKeyField(Project, backref="comments", null=False)
    author_email = peewee.CharField(null=False)
    author_name = peewee.CharField(null=False)
    # 待确认 / 已确认待修改 / 已修改 / 忽略（产品方案 §3.4）
    status = peewee.CharField(null=False)
    priority = peewee.CharField(null=False)   # P1 / P2 / P3
    scope = peewee.CharField(null=False)      # prototype / doc / both
    target_type = peewee.CharField(null=False)  # dom / page / doc_block
    prototype_page = peewee.CharField(null=True)
    anchor_id = peewee.CharField(null=True)
    payload_json = peewee.CharField(null=False)
    deleted = peewee.BooleanField(default=False)  # 软删（T4.4 编辑删除规则用）
    created_at = peewee.CharField(default=utcnow_str)
    updated_at = peewee.CharField(default=utcnow_str)


class GitTask(BaseModel):
    """落仓任务状态（T4.3，技术方案 §3）。

    队列本体在内存（每项目 queue.Queue + 单 worker 线程，server/git_tasks.py）；
    本表持久化任务状态（pending/done/error）供排查与界面提示（项目卡片
    sync_error 由 worker 维护）。注意：进程重启时未完成的 pending 任务不
    自动恢复（一期不做；评论以仓库为事实源，T5.1 SYNC_PULL 比对可补差异）。
    """
    id = peewee.AutoField(primary_key=True)
    project = peewee.ForeignKeyField(Project, backref="git_tasks", null=False)
    task_type = peewee.CharField(null=False)   # COMMIT_COMMENT/COMMIT_STATUS/COMMIT_DELETE
    ref_id = peewee.CharField(null=True)       # 关联 comment_id
    status = peewee.CharField(null=False)      # pending/done/error
    retry_count = peewee.IntegerField(default=0)  # push 尝试次数（含首次）
    error = peewee.CharField(null=True)
    created_at = peewee.CharField(default=utcnow_str)
    updated_at = peewee.CharField(default=utcnow_str)


def init_tables() -> None:
    """建表（幂等）。应用启动与测试 fixture 共用。"""
    db.connect(reuse_if_open=True)
    db.create_tables([User, VerificationCode, Project, Comment, GitTask], safe=True)
