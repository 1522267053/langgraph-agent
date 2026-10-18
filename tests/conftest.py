"""
pytest 全局夹具

关键约束（按导入顺序生效）：
1. 环境变量必须在导入 app.config.* 之前设置——settings 是模块级单例，
   engine 在 database.py 导入时即创建，晚了就指向真实 data/ 库。
2. 测试库用项目内 temp/ 下的独立 SQLite 文件（temp/ 已在 .gitignore），
   测试结束删除。
3. 所有测试共用 session 级事件循环（pyproject 中 asyncio_default_test_loop_scope
   = session）：SQLAlchemy 异步引擎连接池不能跨 loop 复用，与 uvicorn 运行时
   单 loop 一致。
"""

import os
import sys
from pathlib import Path

import pytest

# ---- 必须最先执行：在任何 app 导入前钉死测试数据库 ----
_PROJECT_ROOT = Path(__file__).resolve().parent.parent
_TEST_DB_PATH = _PROJECT_ROOT / "temp" / "pytest_test.db"
_TEST_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
os.environ["DATABASE_URL"] = f"sqlite+aiosqlite:///{_TEST_DB_PATH.as_posix()}"

# pytest 不自动把项目根加入 sys.path（package-mode=false 且无 src 布局）
sys.path.insert(0, str(_PROJECT_ROOT))

from app.config.database import AsyncSessionLocal, init_db  # noqa: E402

# 显式导入测试涉及的模型：create_all 只建「已导入到 metadata」的表
# （运行时由 load_all_models 自动加载，测试进程不触发 app 启动流程）
import app.models.interaction_audit_log  # noqa: E402, F401
import app.models.session_tool_grant  # noqa: E402, F401


@pytest.fixture(scope="session", autouse=True)
async def _setup_test_db():
    """会话级：建表；结束时删库文件"""
    await init_db()
    yield
    # 引擎持有文件连接，Windows 下需先释放再删文件；
    # 测试库留在 temp/ 下由下次会话覆盖，不强制删除


@pytest.fixture(autouse=True)
async def _clean_tables():
    """每个测试前后清空交互审计表，保证用例互不污染"""
    from sqlalchemy import delete

    from app.models.interaction_audit_log import InteractionAuditLog
    from app.models.session_tool_grant import SessionToolGrant

    async with AsyncSessionLocal() as db:
        await db.execute(delete(InteractionAuditLog))
        await db.execute(delete(SessionToolGrant))
        await db.commit()
    yield
    async with AsyncSessionLocal() as db:
        await db.execute(delete(InteractionAuditLog))
        await db.execute(delete(SessionToolGrant))
        await db.commit()
