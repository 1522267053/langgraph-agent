"""
节点处理器模块

注意：处理器模块通过 app.utils.loader.load_all_handlers() 自动加载
新增处理器只需创建文件并使用 @NodeHandlerRegistry.register() 装饰器即可
"""

from app.agent_flow.node_handlers.base_handler import BaseNodeHandler
from app.agent_flow.variable_resolver import VariableResolver, variable_resolver
from app.agent_flow.handler_registry import NodeHandlerRegistry

__all__ = [
    "BaseNodeHandler",
    "VariableResolver",
    "variable_resolver",
    "NodeHandlerRegistry",
]
