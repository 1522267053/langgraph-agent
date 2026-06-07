"""
基础 Model 类
提供公共字段：创建人、修改人、逻辑删除等
"""

from datetime import datetime
from typing import Optional
from sqlalchemy import String, Integer, DateTime, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column, DeclarativeBase


class DbBaseModel(DeclarativeBase):
    """
    SQLAlchemy 声明式基类
    基础模型类 - 包含公共字段

    所有业务模型都应该继承此类

    公共字段：
    - id: 主键
    - creator_id: 创建人ID
    - creator_type: 创建人类型（1=管理员，2=用户）
    - creator_name: 创建人名称
    - create_time: 创建时间
    - modifier_id: 修改人ID
    - modifier_type: 修改人类型（1=管理员，2=用户）
    - modifier_name: 修改人名称
    - modify_time: 修改时间
    - is_delete: 逻辑删除标志（0=未删除，1=已删除）
    """

    __abstract__ = True  # 声明为抽象类，不会创建对应的数据库表

    # 主键
    id: Mapped[int] = mapped_column(
        Integer, primary_key=True, autoincrement=True, comment="主键ID"
    )

    # 创建人信息
    creator_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="创建人ID"
    )

    creator_type: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="创建人类型：1=管理员，2=用户"
    )

    creator_name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="创建人名称"
    )

    create_time: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=True, comment="创建时间"
    )

    # 修改人信息
    modifier_id: Mapped[Optional[int]] = mapped_column(
        Integer, nullable=True, comment="修改人ID"
    )

    modifier_type: Mapped[Optional[int]] = mapped_column(
        SmallInteger, nullable=True, comment="修改人类型：1=管理员，2=用户"
    )

    modifier_name: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="修改人名称"
    )

    modify_time: Mapped[Optional[datetime]] = mapped_column(
        DateTime,
        default=datetime.now,
        onupdate=datetime.now,
        nullable=True,
        comment="修改时间",
    )

    # 逻辑删除标志
    is_delete: Mapped[int] = mapped_column(
        SmallInteger,
        default=0,
        nullable=False,
        comment="逻辑删除标志：0=未删除，1=已删除",
    )


# 用户类型枚举
class UserType:
    """创建人/修改人类型枚举"""

    ADMIN = 1  # 管理员
    USER = 2  # 用户


# 删除状态枚举
class DeleteStatus:
    """删除状态枚举"""

    NOT_DELETED = 0  # 未删除
    DELETED = 1  # 已删除
