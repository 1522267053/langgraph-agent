"""
节点配置默认值辅助函数

提供从 NodeHandlerRegistry 获取 ConfigClass 默认配置的通用方法，
供 flow_template_service 和 builtin_agent_service 等模块使用。
"""

from typing import Optional

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

# 全 False 的能力基线（xlsx 恒 False：后端链路不生成 xlsx 媒体块）
_EMPTY_CAPABILITIES = {
    "image": False,
    "video": False,
    "audio": False,
    "pdf": False,
    "xlsx": False,
}


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


def has_any_capability(caps: Optional[dict]) -> bool:
    """capabilities 是否已启用任意多模态能力（全 False/缺失则视为未初始化）"""
    if not isinstance(caps, dict):
        return False
    return any(bool(caps.get(key)) for key in _EMPTY_CAPABILITIES)


async def derive_model_capabilities(
    db: AsyncSession, provider: str, model: str
) -> dict:
    """按模型元数据（ai_model.modalities.input）推导多模态能力开关

    查不到模型记录时返回全 False 基线，不抛错。

    Returns:
        {"image","video","audio","pdf","xlsx"} 能力开关副本
    """
    from app.models.ai_model import AIModel

    caps = dict(_EMPTY_CAPABILITIES)
    if not provider or not model:
        return caps

    query = select(AIModel).where(
        AIModel.provider_id == provider,
        AIModel.model_id == model,
        AIModel.is_delete == 0,
    )
    result = await db.execute(query)
    ai_model = result.scalar_one_or_none()
    if not ai_model:
        return caps

    modalities = ai_model.modalities or {}
    input_mods = modalities.get("input") if isinstance(modalities, dict) else None
    if isinstance(input_mods, list):
        for modality in ("image", "video", "audio", "pdf"):
            caps[modality] = modality in input_mods
    return caps


async def inject_llm_defaults(
    base_config: dict,
    global_cfg: dict,
    db: AsyncSession | None = None,
    *,
    node_type: str = "",
) -> dict:
    """
    为 LLM 节点配置注入全局默认值。

    触发条件：model 或 api_key 任一为空时，用全局值覆盖全部 5 个字段
    （provider/model/api_key/base_url/context_length），不再仅填空。

    全局注入后（或 model 已就位时），若当前是 LLM 节点、capabilities 全 False/缺失
    且提供 db，按模型元数据推导 capabilities（用户手动勾选过的绝不覆盖）。

    Args:
        base_config: 节点当前配置
        global_cfg: 全局 LLM 默认配置
        db: 数据库会话（None 时跳过 capabilities 推导，兼容纯同步场景）
        node_type: 节点类型，仅 llm 推导 capabilities

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

    # capabilities 未初始化时按模型元数据推导（仅 LLM 节点有该字段）
    if (
        db is not None
        and node_type == "llm"
        and not has_any_capability(bc.get("capabilities"))
        and bc.get("provider")
        and bc.get("model")
    ):
        bc["capabilities"] = await derive_model_capabilities(
            db, bc["provider"], bc["model"]
        )
    return bc
