"""敏感配置出参脱敏工具。"""

from typing import Any


MASK_MARKER = "****"


def _is_api_key_field(key: Any) -> bool:
    return isinstance(key, str) and key.lower() == "api_key"


def mask_secret(value: Any) -> Any:
    """保留首尾少量字符展示密钥，避免返回完整值。"""
    if not value:
        return value
    if not isinstance(value, str):
        return MASK_MARKER
    if len(value) > 8:
        return value[:4] + MASK_MARKER + value[-4:]
    return MASK_MARKER


def is_masked_secret(value: Any) -> bool:
    """判断值是否符合本模块生成的掩码格式。"""
    if not isinstance(value, str):
        return False
    return value == MASK_MARKER or (len(value) > 8 and value[4:8] == MASK_MARKER)


def mask_api_keys(value: Any) -> Any:
    """递归脱敏字典和列表中的 api_key 字段。"""
    if isinstance(value, dict):
        return {
            key: mask_secret(item) if _is_api_key_field(key) else mask_api_keys(item)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [mask_api_keys(item) for item in value]
    return value


def restore_masked_api_keys(value: Any, original: Any) -> Any:
    """将请求中的脱敏 api_key 恢复为数据库中的真实值。"""
    if isinstance(value, dict):
        original_dict = original if isinstance(original, dict) else {}
        restored: dict[Any, Any] = {}
        for key, item in value.items():
            original_item = original_dict.get(key)
            if _is_api_key_field(key) and is_masked_secret(item) and original_item:
                restored[key] = original_item
            else:
                restored[key] = restore_masked_api_keys(item, original_item)
        return restored
    if isinstance(value, list):
        original_list = original if isinstance(original, list) else []
        return [
            restore_masked_api_keys(
                item, original_list[index] if index < len(original_list) else None
            )
            for index, item in enumerate(value)
        ]
    return value
