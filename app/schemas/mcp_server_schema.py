"""
MCP服务器配置相关数据模型
"""

from typing import Optional, Any
from pydantic import Field, field_validator
from app.schemas.base_schema import BaseView, PaginationParams, ChinaDateTime


class McpServerConfigDetail(BaseView):
    """MCP服务器配置详情"""

    command: Optional[str] = Field(default=None, description="执行命令（stdio）")
    args: Optional[list[str]] = Field(default=None, description="命令参数")
    env: Optional[dict[str, str]] = Field(default=None, description="环境变量")
    url: Optional[str] = Field(
        default=None, description="服务器URL（sse/streamable-http）"
    )
    headers: Optional[dict[str, str]] = Field(default=None, description="请求头")
    timeout: Optional[int] = Field(
        default=None, ge=1, le=600, description="工具调用超时时间（秒），1-600"
    )


class McpServerBase(BaseView):
    """MCP服务器基础模型"""

    name: Optional[str] = Field(default=None, description="服务器名称")
    description: Optional[str] = Field(default=None, description="描述")
    transport: Optional[str] = Field(
        default=None, description="传输类型：stdio/sse/streamable-http"
    )
    is_enabled: Optional[int] = Field(default=1, description="是否启用：0=禁用，1=启用")
    keep_alive: Optional[int] = Field(
        default=1, description="保持连接：0=调用后释放，1=保持连接"
    )
    last_connected_at: Optional[ChinaDateTime] = Field(
        default=None, description="最后刷新时间"
    )
    config: Optional[McpServerConfigDetail] = Field(
        default=None, description="配置详情"
    )

    @field_validator("name")
    @classmethod
    def validate_name(cls, v: Optional[str]) -> Optional[str]:
        """校验服务器名称"""
        if v is None:
            return v
        if len(v) > 100:
            raise ValueError("服务器名称不能超过100个字符")
        return v

    @field_validator("transport")
    @classmethod
    def validate_transport(cls, v: Optional[str]) -> Optional[str]:
        """校验传输类型（兼容 http/streamable_http 等常见别名写法）"""
        if v is None:
            return v
        normalized = v.strip().lower().replace("_", "-")
        if normalized in ("http", "streamablehttp", "streamable-http"):
            return "streamable-http"
        if normalized in ("sse", "stdio"):
            return normalized
        raise ValueError("传输类型必须是: stdio, sse, streamable-http")


class McpServerCreate(McpServerBase):
    """创建MCP服务器"""

    name: str = Field(..., description="服务器名称")
    transport: str = Field(..., description="传输类型")


class McpServerUpdate(McpServerBase):
    """更新MCP服务器"""

    pass


class McpServerQuery(BaseView):
    """查询MCP服务器条件"""

    name: Optional[str] = Field(default=None, description="服务器名称")
    transport: Optional[str] = Field(default=None, description="传输类型")
    is_enabled: Optional[int] = Field(default=None, description="是否启用")


class McpServerPageParams(PaginationParams[McpServerQuery]):
    """MCP服务器分页参数"""

    pass


class McpToolInfo(BaseView):
    """MCP工具信息"""

    name: Optional[str] = Field(default=None, description="工具名称")
    description: Optional[str] = Field(default=None, description="工具描述")
    input_schema: Optional[dict[str, Any]] = Field(
        default=None, description="工具Schema"
    )
    is_enabled: Optional[int] = Field(default=1, description="是否启用：0=禁用，1=启用")


class McpServerTestResult(BaseView):
    """MCP服务器测试结果"""

    success: Optional[bool] = Field(default=None, description="是否成功")
    tools: list[McpToolInfo] = Field(default_factory=list, description="可用工具列表")
    error: Optional[str] = Field(default=None, description="错误信息")
