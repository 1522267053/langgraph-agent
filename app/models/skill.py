"""
Agent Skill 模型

Skill 是 Agent 可以调用的能力模块，基于 Markdown 文档定义。
LLM 通过 load_skill 工具加载 SKILL.md 文档，理解后自主执行。
"""

from typing import Optional
from sqlalchemy import String, Integer, SmallInteger, Text
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class Skill(DbBaseModel):
    """
    Agent Skill 表模型

    Skill 是 Agent 可调用的能力单元，通过上传 ZIP 文件添加。
    ZIP 中必须包含 SKILL.md 文件，定义 name 和 description。

    执行方式：
    LLM 通过 load_skill 工具加载 SKILL.md 文档，理解后自主调用相关 API
    """

    __tablename__ = "skill"

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="Skill 名称")
    description: Mapped[str] = mapped_column(
        Text, nullable=False, comment="描述（LLM 用于判断何时调用）"
    )
    skill_path: Mapped[Optional[str]] = mapped_column(
        String(500),
        nullable=True,
        comment="SKILL.md 文件相对路径（如 uploads/skills/invoice-expense/SKILL.md）",
    )
    category: Mapped[Optional[str]] = mapped_column(
        String(50), nullable=True, comment="分类（工具、AI、数据处理等）"
    )
    tags: Mapped[Optional[str]] = mapped_column(
        String(255), nullable=True, comment="标签（逗号分隔）"
    )
    icon: Mapped[Optional[str]] = mapped_column(
        String(100), nullable=True, comment="图标名称"
    )
    is_enabled: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, comment="是否启用：0=禁用，1=启用"
    )
    is_system: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=0, comment="是否系统预置：0=否，1=是"
    )
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, comment="排序顺序"
    )

    def __repr__(self) -> str:
        return f"<Skill(id={self.id}, name={self.name})>"
