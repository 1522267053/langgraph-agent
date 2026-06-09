"""
节点处理器注册表

支持通过装饰器注册处理器，实现自动发现和创建
"""

from typing import Callable, Type, Optional, Dict

from app.agent_flow.node_handlers.base_handler import BaseNodeHandler


class NodeHandlerRegistry:
    """
    节点处理器注册表

    支持：
    - 通过装饰器注册处理器类
    - 注册工厂函数（用于需要依赖注入的处理器）
    - 自动创建处理器实例

    Usage:
        # 注册简单处理器
        @NodeHandlerRegistry.register("api")
        class ApiNodeHandler(BaseNodeHandler):
            ...

        # 注册需要依赖注入的处理器
        @NodeHandlerRegistry.register_factory("llm")
        def create_llm_handler(flow, db_session, execution_id, **kwargs):
            return LlmToolNodeHandler(flow=flow, db_session=db_session, ...)

        # 创建处理器实例
        handler = NodeHandlerRegistry.create("api")
        llm_handler = NodeHandlerRegistry.create("llm", flow=flow, db_session=db_session, ...)
    """

    _handlers: Dict[str, Type["BaseNodeHandler"]] = {}
    _factories: Dict[str, Callable] = {}

    @classmethod
    def _get_factory_handler_class(
        cls, node_type: str
    ) -> Optional[Type["BaseNodeHandler"]]:
        """
        获取工厂注册的处理器类（延迟导入）

        Args:
            node_type: 节点类型

        Returns:
            处理器类，未找到则返回 None
        """
        if node_type == "llm":
            from app.agent_flow.node_handlers.llm_tool_handler import (
                LlmToolNodeHandler,
            )

            return LlmToolNodeHandler
        if node_type == "intent_router":
            from app.agent_flow.node_handlers.intent_router_handler import (
                IntentRouterHandler,
            )

            return IntentRouterHandler
        return None

    @classmethod
    def register(cls, node_type: str):
        """
        装饰器：注册处理器类

        Args:
            node_type: 节点类型标识（如 "api", "human"）

        Usage:
            @NodeHandlerRegistry.register("api")
            class ApiNodeHandler(BaseNodeHandler):
                ...
        """

        def decorator(handler_class: Type["BaseNodeHandler"]):
            cls._handlers[node_type] = handler_class
            return handler_class

        return decorator

    @classmethod
    def register_factory(cls, node_type: str):
        """
        装饰器：注册工厂函数

        Args:
            node_type: 节点类型标识

        Usage:
            @NodeHandlerRegistry.register_factory("llm")
            def create_llm_handler(flow, db_session, execution_id, **kwargs):
                return LlmToolNodeHandler(...)
        """

        def decorator(factory: Callable):
            cls._factories[node_type] = factory
            return factory

        return decorator

    @classmethod
    def create(cls, node_type: str, **kwargs) -> Optional["BaseNodeHandler"]:
        """
        创建处理器实例

        优先使用工厂函数，否则使用处理器类

        Args:
            node_type: 节点类型
            **kwargs: 传递给处理器构造函数的参数

        Returns:
            处理器实例，未注册则返回 None
        """
        if node_type in cls._factories:
            return cls._factories[node_type](**kwargs)

        if node_type in cls._handlers:
            return cls._handlers[node_type](**kwargs)

        return None

    @classmethod
    def get_handler_class(cls, node_type: str) -> Optional[Type["BaseNodeHandler"]]:
        """获取处理器类"""
        return cls._handlers.get(node_type)

    @classmethod
    def get_factory(cls, node_type: str) -> Optional[Callable]:
        """获取工厂函数"""
        return cls._factories.get(node_type)

    @classmethod
    def is_registered(cls, node_type: str) -> bool:
        """检查是否已注册"""
        return node_type in cls._handlers or node_type in cls._factories

    @classmethod
    def list_handlers(cls) -> list[str]:
        """列出所有已注册的处理器类型"""
        return list(set(cls._handlers.keys()) | set(cls._factories.keys()))

    @classmethod
    def get_singleton_tool_types(cls) -> set[str]:
        """
        获取不允许重复连接到同一 LLM 的工具节点类型集合

        遍历所有已注册的处理器，收集 allow_multiple_tool_connections() == False 的类型。

        Returns:
            不允许重复连接的节点类型集合
        """
        singleton_types: set[str] = set()
        for node_type, handler_class in cls._handlers.items():
            if not handler_class.allow_multiple_tool_connections():
                singleton_types.add(node_type)
        for node_type in cls._factories:
            factory_class = cls._get_factory_handler_class(node_type)
            if factory_class and not factory_class.allow_multiple_tool_connections():
                singleton_types.add(node_type)
        return singleton_types

    @classmethod
    def clear(cls):
        """清空注册表（主要用于测试）"""
        cls._handlers.clear()
        cls._factories.clear()

    @classmethod
    def get_input_content(cls, node_type: str, node, state, resolver):
        """
        获取节点的输入内容

        Args:
            node_type: 节点类型
            node: 节点对象
            state: 流程状态
            resolver: 变量解析器

        Returns:
            输入内容字典，未注册则返回 None
        """
        # 优先从直接注册的处理器中查找
        handler_class = cls._handlers.get(node_type)
        if handler_class:
            return handler_class.get_input_content(node, state, resolver)

        # 从工厂注册的处理器中查找（延迟导入）
        factory_handler_class = cls._get_factory_handler_class(node_type)
        if factory_handler_class:
            return factory_handler_class.get_input_content(node, state, resolver)

        return None

    @classmethod
    def get_output_content(cls, node_type: str, node, state, resolver):
        """
        获取节点的输出内容

        Args:
            node_type: 节点类型
            node: 节点对象
            state: 流程状态
            resolver: 变量解析器

        Returns:
            输出内容字典，未注册则返回 None
        """
        # 优先从直接注册的处理器中查找
        handler_class = cls._handlers.get(node_type)
        if handler_class:
            return handler_class.get_output_content(node, state, resolver)

        # 从工厂注册的处理器中查找（延迟导入）
        factory_handler_class = cls._get_factory_handler_class(node_type)
        if factory_handler_class:
            return factory_handler_class.get_output_content(node, state, resolver)

        return None
