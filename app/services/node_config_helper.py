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


def inject_llm_defaults(base_config: dict, global_cfg: dict) -> dict:
    """
    为 LLM 节点配置注入全局默认值。

    触发条件：model 或 api_key 任一为空时，用全局值覆盖全部 5 个字段
    （provider/model/api_key/base_url/context_length），不再仅填空。

    Args:
        base_config: 节点当前配置
        global_cfg: 全局 LLM 默认配置

    Returns:
        注入后的配置字典
    """
    bc = dict(base_config)
    needs_inject = not bc.get("model") or not bc.get("api_key")
    if needs_inject and global_cfg.get("model") and global_cfg.get("api_key"):
        bc["api_key"] = global_cfg.get("api_key", "")
        bc["base_url"] = global_cfg["base_url"]
        bc["context_length"] = global_cfg["context_length"]
        bc["model"] = global_cfg.get("model", "")
        bc["provider"] = global_cfg.get("provider", "")
    return bc
