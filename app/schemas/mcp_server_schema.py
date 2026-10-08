"""
MCP服务器配置相关数据模型
"""

import re
from typing import Optional, Any
from pydantic import Field, field_validator
from app.schemas.base_schema import BaseView, PaginationParams, ChinaDateTime

# MCP 服务器名称即 OpenAI 兼容工具名的组成部分（mcp__<server>__<tool>），
# 必须满足 function calling 的名称规范：字母开头，仅字母/数字/下划线/连字符，≤64
MCP_SERVER_NAME_PATTERN = re.compile(r"^[a-zA-Z][a-zA-Z0-9_-]{0,63}$")
MCP_SERVER_NAME_RULE = (
    "服务器名称仅允许字母/数字/下划线/连字符（字母开头，最长64字符），"
    "不能包含中文或特殊符号——名称将用于拼接工具名（mcp__<server>__<tool>）"
)


def sanitize_mcp_server_name(raw: str) -> str:
    """将任意名称转写为符合 MCP 服务器名规范的形态（导入等场景的自动降级）

    非法字符（中文/全角/符号/空格）→ _；非字母开头补 mcp_ 前缀；超长截断到 64。
    转写结果为空时返回 mcp_server 兜底。
    """
    cleaned = re.sub(r"[^a-zA-Z0-9_-]+", "_", (raw or "").strip())
    cleaned = re.sub(r"_+", "_", cleaned).strip("_")
    if not cleaned:
        return "mcp_server"
    if not cleaned[0].isalpha():
        cleaned = f"mcp_{cleaned}"
    return cleaned[:64]


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
        """校验服务器名称（需满足 OpenAI 兼容工具名规范）"""
        if v is None:
            return v
        name = v.strip()
        if len(name) > 100:
            raise ValueError("服务器名称不能超过100个字符")
        if name and not MCP_SERVER_NAME_PATTERN.match(name):
            raise ValueError(MCP_SERVER_NAME_RULE)
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
