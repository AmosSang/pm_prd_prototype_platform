"""数据模型（peewee + SQLite，WAL 模式）。

表随任务卡逐步增加：
- T2.1: users / verification_codes
- T2.3: projects
- T4.2: comments（评论展示缓存，事实源为项目目录 reviews/）
- T8.1（去 Git 本地化）：projects 去 git 字段、加 creator_id 与
  content_updated_at；git_tasks 表删除（落仓队列随 git 链路移除）
"""
import datetime as dt
import os

import peewee

from server.config import ADMIN_EMAIL, DATA_DIR, DB_PATH, PROJECTS_DIR


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
    # 停用标志（T2.1 用户管理）：停用后不发验证码、已登录调用接口即 401 强制登出
    disabled = peewee.BooleanField(default=False)
    created_at = peewee.CharField(default=utcnow_str)


class VerificationCode(BaseModel):
    email = peewee.CharField(null=False, index=True)
    code = peewee.CharField(null=False)
    expires_at = peewee.CharField(null=False)
    used = peewee.BooleanField(default=False)
    created_at = peewee.CharField(default=utcnow_str)


class Project(BaseModel):
    """项目（T2.3 建，T8.1 去 Git 本地化改造）。

    project_id 用随机短 slug（kebab-case），是本地项目目录名与
    /proto/{project_id}/ 路径前缀，与数字主键 id 分离——路径不可猜测遍历。
    creator_id 记录创建者（权限体系：上传/导出/开关/管理任意评论专属）；
    项目内容存 /data/projects/{project_id}/（prototype/prd/reviews）。
    """
    id = peewee.AutoField(primary_key=True)
    project_id = peewee.CharField(unique=True, null=False)
    name = peewee.CharField(null=False)
    # 创建者（FK users；创建者为当前登录用户）
    creator_id = peewee.IntegerField(null=False)
    # 项目级「可评论」开关（T4.5；关闭 = 冻结一切写评论操作）
    commentable = peewee.BooleanField(default=True)
    # 最近一次原型/PRD 上传时间（T8.1 加；上传接口维护）
    content_updated_at = peewee.CharField(null=True)
    created_at = peewee.CharField(default=utcnow_str)


class Comment(BaseModel):
    """评论（T4.2，展示缓存；事实源为项目目录 reviews/comments/*.json）。

    评论 JSON 全量（含截图路径、doc 关联、interaction_state）落 payload_json；
    常用筛选字段（status/priority/scope/target_type/prototype_page/anchor_id）
    冗余成列，评论列表抽屉（T4.4）查询免 JSON 解析。
    comment_id 格式 c-YYYYMMDD-NNN（当日序号，跨 DB 与项目文件查重顺延）。
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


def init_tables() -> None:
    """建表（幂等）。应用启动与测试 fixture 共用。

    服务器全新部署时 `data/` 目录不存在会导致连库失败——这里先建目录骨架
    （data、data/projects、data/shots 三处），使「启动即自初始化」，
    无需手动 mkdir。
    """
    os.makedirs(DATA_DIR, exist_ok=True)
    os.makedirs(PROJECTS_DIR, exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "shots"), exist_ok=True)
    db.connect(reuse_if_open=True)
    db.create_tables([User, VerificationCode, Project, Comment], safe=True)
    _migrate()
    seed_admin()


def _migrate() -> None:
    """轻量迁移：旧库补 user.disabled 列（create_tables 不改已存在的表）。

    注意：peewee 默认表名 = 模型类名小写（User → user），不是 users。
    """
    tbl = User._meta.table_name
    try:
        db.execute_sql(f"ALTER TABLE {tbl} ADD COLUMN disabled BOOLEAN NOT NULL DEFAULT 0")
    except Exception:  # noqa: BLE001 —— 列已存在/等无关错误，忽略
        pass


def seed_admin() -> None:
    """按 ADMIN_EMAIL 环境变量种子超级管理员（幂等，多方启动只保证一次）。

    不存在则创建（name=admin）；已存在则确保 is_admin=True。
    """
    if not ADMIN_EMAIL:
        return
    email = ADMIN_EMAIL.strip().lower()
    if not email:
        return
    user = User.get_or_none(User.email == email)
    if user:
        if not user.is_admin:
            user.is_admin = True
            user.save()
        return
    User.create(email=email, name="admin", is_admin=True)
