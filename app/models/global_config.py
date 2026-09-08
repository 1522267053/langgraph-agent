"""
全局配置模型

存储用户全局配置（API Key、模型、供应商等）
"""

from typing import Optional
from sqlalchemy import String, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class GlobalConfig(DbBaseModel):
    """
    全局配置表

    以 key-value 形式存储配置项，如：
    - initialized: 是否完成初始化 ("true"/"false")
    - embedding_*: 向量模型配置
    - login_password_hash / login_username: 登录凭据
    - proxy_url: 出站网络代理

    注：默认 LLM 配置（provider/model/api_key/base_url/context_length）
    已迁移至 ai_provider_connection 表（is_default=1 的记录）。
    """

    __tablename__ = "global_config"

    key: Mapped[str] = mapped_column(
        String(100), nullable=False, unique=True, comment="配置键"
    )
    value: Mapped[Optional[str]] = mapped_column(Text, nullable=True, comment="配置值")
    description: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="配置说明"
    )

    def __repr__(self) -> str:
        return f"<GlobalConfig(key={self.key})>"
