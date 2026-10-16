"""
工具集统一契约（BaseToolKit）

所有 tools/ 目录下的工具集（Toolkit）必须继承本基类，统一两个入口：
- build_tools():  构造工具列表（handler.get_tool 委托调用）
- tool_info(node): 工具元数据 [{name, description}]（handler.get_tool_info
  委托调用；延迟域索引与前端展示共用，零建连）

约定：
- build_tools() 统一无参签名——运行时依赖（审批回调、配置值、writer）
  一律经构造函数显式注入，不做方法参数
- tool_info(node) 为 classmethod：延迟域索引与前端展示都是零建连场景，
  不应为拿元数据而构造 Kit 实例（实例构造需要注入运行时依赖）
- 描述单源（强制）：tool_info 返回的 description 必须与 build_tools
  产出的 StructuredTool description 逐字节一致——从同一模块级常量/
  构建函数派生，禁止手抄短句（违反即产生两处维护，漂移后延迟域
  search 召回与展示失真）。assert_consistency 提供逐字节校验，e2e
  直接复用

依赖方向：tools/* → langchain_core/pydantic/app/utils/*；
禁止 import node_handlers（handler 只负责构造 Kit 并注入依赖）。

不适用本契约的形态：
- 动态预设工具（python/api/sub_agent/flow_tool）：name/schema 运行时
  从节点配置生成，无法静态 tool_info，留 handler 构建器
- 单工具服务（FileReadService）：build_tool 单数已自洽，不强套
"""

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Sequence

from langchain_core.tools import BaseTool, StructuredTool

if TYPE_CHECKING:
    from app.models.flow_node import FlowNode


class BaseToolKit(ABC):
    """工具集基类：一个族一个 Kit，闭包逻辑 + 构造函数依赖注入"""

    @abstractmethod
    async def build_tools(self) -> Sequence[StructuredTool]:
        """构造工具列表（运行时依赖已在构造函数注入）"""
        ...

    @classmethod
    @abstractmethod
    def tool_info(cls, node: "FlowNode") -> list[dict]:
        """工具元数据 [{name, description}]（零建连、纯静态/纯读 node 配置）

        描述单源：description 必须与 build_tools 产出的 StructuredTool
        逐字节一致（同一常量/构建函数派生），禁止手抄短句。
        handler.get_tool_info 直接委托本方法。
        """
        ...

    @classmethod
    def tool_names(cls, node: "FlowNode") -> list[str]:
        """工具名清单（从 tool_info 派生）"""
        return [t["name"] for t in cls.tool_info(node)]


async def assert_consistency(kit: BaseToolKit, node: "FlowNode") -> tuple[bool, str]:
    """校验 tool_info 与 build_tools 产出逐字节一致（e2e 直接复用）

    描述单源契约的可执行守护：名单一致 + description 逐字节一致。

    Args:
        kit: 待校验的 Toolkit 实例
        node: 节点对象（传给 tool_info）

    Returns:
        (是否一致, 不一致详情；一致时为空串)
    """
    info = {t["name"]: t["description"] for t in kit.tool_info(node)}
    tools: Sequence[BaseTool] = await kit.build_tools()
    actual = {t.name: t.description for t in tools}
    only_info = sorted(set(info) - set(actual))
    only_actual = sorted(set(actual) - set(info))
    desc_diff = sorted(n for n in set(info) & set(actual) if info[n] != actual[n])
    ok = not (only_info or only_actual or desc_diff)
    detail = (
        ""
        if ok
        else (
            f"仅索引有: {only_info}；仅实际有: {only_actual}；描述不一致: {desc_diff}"
        )
    )
    return ok, detail
