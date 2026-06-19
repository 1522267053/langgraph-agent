"""
节点配置默认值辅助函数

提供从 NodeHandlerRegistry 获取 ConfigClass 默认配置的通用方法，
供 flow_template_service 和 builtin_agent_service 等模块使用。
"""


def fill_node_defaults(node_type: str, overrides: dict | None = None) -> dict:
    """
    用 handler 的 ConfigClass 默认值补全，overrides 覆盖特定字段。

    Args:
        node_type: 节点类型标识
        overrides: 需要覆盖的字段字典

    Returns:
        完整配置字典（默认值 + 覆盖值）
    """
    from app.agent_flow.handler_registry import NodeHandlerRegistry

    handler_cls = NodeHandlerRegistry.get_handler_class(node_type)
    if not handler_cls:
        handler_cls = NodeHandlerRegistry._get_factory_handler_class(node_type)
    defaults = handler_cls.get_default_config() if handler_cls else {}
    if overrides:
        defaults.update(overrides)
    return defaults
