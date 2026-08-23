"""用户管理 CLI（一期管理员入口）。

用法（platform/ 目录下）：
    python -m server.cli user-add <email> <name> [--admin]
    python -m server.cli user-list
    python -m server.cli user-del <email>

示例：
    python -m server.cli user-add zhangsan@corp.com 张三
"""
import argparse
import sys

from server.models import User, init_tables


def cmd_user_add(args):
    email = args.email.strip().lower()
    if User.get_or_none(User.email == email):
        print(f"已存在：{email}")
        return 1
    User.create(email=email, name=args.name, is_admin=args.admin)
    print(f"已添加：{email}（{args.name}{'，管理员' if args.admin else ''}）")
    return 0


def cmd_user_list(_args):
    rows = User.select().order_by(User.id)
    if not rows:
        print("（空）")
        return 0
    print(f"{'id':<4} {'email':<32} {'name':<12} admin  created_at")
    for u in rows:
        print(f"{u.id:<4} {u.email:<32} {u.name:<12} {'Y' if u.is_admin else '-':<5}  {u.created_at}")
    return 0


def cmd_user_del(args):
    email = args.email.strip().lower()
    n = User.delete().where(User.email == email).execute()
    print(f"已删除 {n} 条" if n else f"不存在：{email}")
    return 0 if n else 1


def main(argv=None):
    parser = argparse.ArgumentParser(prog="server.cli", description="平台管理 CLI")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_add = sub.add_parser("user-add", help="添加用户（白名单）")
    p_add.add_argument("email")
    p_add.add_argument("name")
    p_add.add_argument("--admin", action="store_true")
    p_add.set_defaults(fn=cmd_user_add)

    p_list = sub.add_parser("user-list", help="列出用户")
    p_list.set_defaults(fn=cmd_user_list)

    p_del = sub.add_parser("user-del", help="删除用户")
    p_del.add_argument("email")
    p_del.set_defaults(fn=cmd_user_del)

    args = parser.parse_args(argv)
    init_tables()
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
