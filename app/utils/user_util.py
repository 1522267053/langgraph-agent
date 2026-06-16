"""
用户工具函数

提供获取当前登录用户名的便捷方法，避免各模块重复定义。
"""

from app.config.database import AsyncSessionLocal
from app.services.global_config_service import global_config_service


async def get_current_username() -> str:
    """获取当前登录用户名（自动管理 session），未配置时返回 'default'"""
    try:
        async with AsyncSessionLocal() as db:
            username = await global_config_service.get_username(db)
            return username or "default"
    except Exception:
        return "default"
