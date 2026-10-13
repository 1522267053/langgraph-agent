"""whoami 工具实现

返回当前流程（Agent/Workflow）的身份信息，供模型在自更新等场景确认自身 id。
由 llm_tool_executor 按条件注入（仅 flow.is_builtin=1 的内置助手），不依赖工具节点。

字段快照在构造时完成：工具 coroutine 可能在 ORM 会话关闭后执行，
延迟访问 ORM 属性会触发 DetachedInstanceError。
"""

import json
import logging
from datetime import datetime
from typing import Optional

from langchain_core.tools import StructuredTool

logger = logging.getLogger(__name__)

# 工具名（内置助手提示词与技能文档引用此名，改名需同步）
WHOAMI_TOOL_NAME = "whoami"

_WHOAMI_DESCRIPTION = (
    "查询你自己的身份信息（whoami）。当你需要知道「我是哪个流程/智能体」「我的 id 是多少」"
    "「要修改自身配置应该操作哪个 id」时调用。返回当前流程的 id、类型、名称、描述、"
    "创建时间、修改时间、是否系统内置、当前会话 id。无参数，只读操作，不修改任何数据。"
)


def _format_time(value: object) -> Optional[str]:
    """时间字段格式化；None 或类型异常时返回 None（不抛）"""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, str):
        return value
    return None


def _flow_type_label(flow_type: object) -> str:
    """流程类型中文化：agent=智能体，flow=工作流"""
    if flow_type == "agent":
        return "agent(智能体)"
    if flow_type == "flow":
        return "flow(工作流)"
    return str(flow_type or "unknown")


def create_whoami_tool(flow: object, session_id: int = 0) -> StructuredTool:
    """构造 whoami 自我认知工具

    Args:
        flow: 当前流程 ORM 对象（需含 id/name/description/flow_type/
            create_time/modify_time/is_builtin），字段缺失按降级值处理
        session_id: 当前会话 id（Agent 模式），0 表示无会话（返回 null）

    Returns:
        StructuredTool，调用时返回身份信息 JSON 字符串
    """
    snapshot = {
        "id": getattr(flow, "id", None),
        "name": getattr(flow, "name", None),
        "description": getattr(flow, "description", None),
        "flow_type": getattr(flow, "flow_type", None),
        "created_at": _format_time(getattr(flow, "create_time", None)),
        "updated_at": _format_time(getattr(flow, "modify_time", None)),
        "is_builtin": getattr(flow, "is_builtin", 0),
        "current_session_id": session_id or None,
    }

    async def whoami_coro() -> str:
        try:
            return json.dumps(
                {
                    "success": True,
                    "id": snapshot["id"],
                    "type": _flow_type_label(snapshot["flow_type"]),
                    "name": snapshot["name"],
                    "description": snapshot["description"],
                    "created_at": snapshot["created_at"],
                    "updated_at": snapshot["updated_at"],
                    "is_builtin": bool(snapshot["is_builtin"]),
                    "current_session_id": snapshot["current_session_id"],
                },
                ensure_ascii=False,
            )
        except Exception as e:  # 身份查询失败不得中断 ReAct 循环
            logger.warning("whoami 工具执行失败: %s", e)
            return json.dumps(
                {"success": False, "error": f"查询自身身份失败: {e}"},
                ensure_ascii=False,
            )

    return StructuredTool.from_function(
        coroutine=whoami_coro,
        name=WHOAMI_TOOL_NAME,
        description=_WHOAMI_DESCRIPTION,
    )
