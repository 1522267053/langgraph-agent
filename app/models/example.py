"""
关于我们模型
"""

from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class Example(DbBaseModel):
    """
    例子表模型

    继承 DbBaseModel，自动拥有：
    - id, creator_id, creator_type, creator_name, create_time
    - modifier_id, modifier_type, modifier_name, modify_time
    - is_delete
    """

    __tablename__ = "example"

    # 业务字段
    title: Mapped[str] = mapped_column(String(255), nullable=False, comment="标题")

    def __repr__(self) -> str:
        return f"<Example(id={self.id}, title={self.title})>"
