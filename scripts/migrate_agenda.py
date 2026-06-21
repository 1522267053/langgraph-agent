"""
为 agenda 表添加 recurrence_generated 列
"""
import os
import re
import sqlite3
from pathlib import Path

# 项目根目录（脚本在 scripts/ 下）
project_root = Path(__file__).resolve().parent.parent

# 读取 .env 获取数据库路径
env_path = project_root / '.env'
db_path = 'data/langgraph_agent.db'

if env_path.exists():
    content = env_path.read_text(encoding='utf-8')
    m = re.search(r'^SQLITE_DB_PATH=(.+)$', content, re.M)
    if m:
        db_path = m.group(1).strip()

# 转为绝对路径
db_abs = project_root / db_path

if not db_abs.exists():
    print(f'数据库文件不存在: {db_abs}')
    exit(1)

conn = sqlite3.connect(str(db_abs))
try:
    # 检查列是否已存在
    cursor = conn.execute("PRAGMA table_info(agenda)")
    cols = [row[1] for row in cursor.fetchall()]
    if 'recurrence_generated' in cols:
        print('recurrence_generated 列已存在，跳过')
    else:
        conn.execute(
            "ALTER TABLE agenda ADD COLUMN recurrence_generated SMALLINT NOT NULL DEFAULT 0"
        )
        conn.commit()
        print('迁移完成：已添加 recurrence_generated 列')
finally:
    conn.close()
