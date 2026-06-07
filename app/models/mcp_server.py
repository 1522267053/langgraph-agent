"""
MCP服务器配置模型
"""

from enum import Enum
from datetime import datetime
from typing import Optional
from sqlalchemy import String, Text, SmallInteger, Integer, JSON, DateTime
from sqlalchemy.orm import Mapped, mapped_column
from app.models.base_model import DbBaseModel


class McpTransportType(str, Enum):
    """MCP传输类型"""

    STDIO = "stdio"
    SSE = "sse"
    STREAMABLE_HTTP = "streamable-http"


class McpServer(DbBaseModel):
    """
    MCP服务器配置表模型

    存储MCP服务器的连接配置信息
    """

    __tablename__ = "mcp_server"

    name: Mapped[str] = mapped_column(String(100), nullable=False, comment="服务器名称")
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="服务器描述"
    )
    transport: Mapped[str] = mapped_column(
        String(20), nullable=False, comment="传输类型：stdio/sse/streamable-http"
    )

    is_enabled: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, comment="是否启用：0=禁用，1=启用"
    )
    keep_alive: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        default=1,
        comment="保持连接：0=调用后释放，1=保持连接",
    )
    last_connected_at: Mapped[Optional[datetime]] = mapped_column(
        DateTime, nullable=True, comment="最后连接时间"
    )
    last_error: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="最后一次连接错误"
    )

    def __repr__(self) -> str:
        return (
            f"<McpServer(id={self.id}, name={self.name}, transport={self.transport})>"
        )


class McpServerConfig(DbBaseModel):
    """
    MCP服务器详细配置表模型

    存储不同传输类型的详细配置
    """

    __tablename__ = "mcp_server_config"

    server_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="关联MCP服务器ID"
    )

    config_key: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="配置键"
    )
    config_value: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="配置值"
    )

    def __repr__(self) -> str:
        return f"<McpServerConfig(server_id={self.server_id}, key={self.config_key})>"


class McpToolCache(DbBaseModel):
    """
    MCP工具缓存表模型

    缓存从MCP服务器获取的工具列表
    """

    __tablename__ = "mcp_tool_cache"

    server_id: Mapped[int] = mapped_column(
        Integer, nullable=False, index=True, comment="关联MCP服务器ID"
    )

    tool_name: Mapped[str] = mapped_column(
        String(100), nullable=False, comment="工具名称"
    )
    description: Mapped[Optional[str]] = mapped_column(
        Text, nullable=True, comment="工具描述"
    )
    tool_schema: Mapped[Optional[dict]] = mapped_column(
        JSON, nullable=True, comment="工具JSON Schema"
    )

    is_enabled: Mapped[int] = mapped_column(
        SmallInteger, nullable=False, default=1, comment="是否启用：0=禁用，1=启用"
    )

    cached_at: Mapped[datetime] = mapped_column(
        DateTime, default=datetime.now, nullable=False, comment="缓存时间"
    )

    def __repr__(self) -> str:
        return f"<McpToolCache(server_id={self.server_id}, tool={self.tool_name})>"
