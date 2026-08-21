"""pytest 配置：契约测试 fixture 索引。"""
import os
import sys

# 让 tests/ 能导入 server 包
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

FIXTURES_DIR = os.path.join(os.path.dirname(__file__), "fixtures")
