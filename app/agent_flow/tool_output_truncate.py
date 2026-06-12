"""
工具输出统一截断模块

JSON 感知截断：保留 JSON 结构完整，只截断大字段值。
- dict: 保留结构，截断大字符串字段和大列表字段
- str: 先尝试 json.loads() 解析为 dict，成功则走 JSON 感知截断；失败则按行/字节截断（纯文本模式）
- 其他类型: str() 后按纯文本截断

所有工具输出统一为 JSON 字符串，LLM 始终收到结构完整、可解析的 JSON。
阈值通过 .env 配置（TOOL_OUTPUT_MAX_LINES / TOOL_OUTPUT_MAX_BYTES）。
超限时将完整内容保存到临时文件，返回预览 + 文件路径提示。
"""

import json
import logging
import uuid
from typing import Any, Optional

from app.config.build_utils import get_temp_dir
from app.config.settings import settings

logger = logging.getLogger(__name__)

# 截断触发后展示的预览限制（与触发阈值无关，固定值）
_PREVIEW_LINES = 50
_PREVIEW_BYTES = 5120  # 5KB


def smart_truncate_output(result: Any, *, prefix: str = "tool_output") -> str:
    """统一截断入口，返回截断/原文后的字符串

    策略：
    - dict: JSON 感知截断（保留结构，截断大字段值），返回 JSON 字符串
    - str: 先尝试 json.loads() 解析为 dict，成功则走 JSON 感知截断；失败则纯文本截断
    - 其他类型: str() 转换后尝试 JSON 解析或纯文本截断

    Args:
        result: 工具执行结果（dict/str/其他类型）
        prefix: 临时文件名前缀，用于区分来源（如 shell_output、memory_output）

    Returns:
        截断后的字符串（JSON 感知路径返回 JSON 字符串）
    """
    max_lines = settings.tool_output_max_lines
    max_bytes = settings.tool_output_max_bytes

    if isinstance(result, dict):
        truncated = _truncate_dict(
            result, max_lines=max_lines, max_bytes=max_bytes, prefix=prefix
        )
        return json.dumps(truncated, ensure_ascii=False, default=str)

    if isinstance(result, str):
        # 尝试解析为 JSON，成功则走 dict 截断路径
        try:
            parsed = json.loads(result)
            if isinstance(parsed, dict):
                truncated = _truncate_dict(
                    parsed, max_lines=max_lines, max_bytes=max_bytes, prefix=prefix
                )
                return json.dumps(truncated, ensure_ascii=False, default=str)
            # 解析成功但不是 dict（如 list/number），检查是否超限
            if isinstance(parsed, list):
                serialized = json.dumps(parsed, ensure_ascii=False, default=str)
                if _exceeds_limit(serialized, max_lines, max_bytes):
                    kept_items, saved_to = _truncate_list(
                        parsed,
                        max_lines=max_lines,
                        max_bytes=max_bytes,
                        prefix=prefix,
                    )
                    return json.dumps(kept_items, ensure_ascii=False, default=str)
            return result
        except (json.JSONDecodeError, TypeError):
            pass
        # JSON 解析失败 → 纯文本截断
        text, _, _ = _truncate_text(
            result, max_lines=max_lines, max_bytes=max_bytes, prefix=prefix
        )
        return text

    # 其他类型：str() 转换后处理
    text = str(result)
    text, _, _ = _truncate_text(
        text, max_lines=max_lines, max_bytes=max_bytes, prefix=prefix
    )
    return text


def _truncate_dict(d: dict, *, max_lines: int, max_bytes: int, prefix: str) -> dict:
    """递归截断 dict，保留 JSON 结构

    策略：
    - bool/int/float/None: 完整保留
    - str: 超限则保存文件，字段值替换为预览，追加 _{key}_truncated/_{key}_saved_to
    - list: 超限则保留前 N 项，追加 _{key}_truncated/_{key}_saved_to/_{key}_total
    - dict: 递归应用同样规则
    - 最终序列化后仍超限: 只保留基本类型字段 + 完整 JSON 保存到文件
    """
    result: dict[str, Any] = {}

    for key, value in d.items():
        if isinstance(value, (bool, int, float)) or value is None:
            # 基本类型完整保留
            result[key] = value
        elif isinstance(value, str):
            if _exceeds_limit(value, max_lines, max_bytes):
                # 大字符串字段：_truncate_text 内部保存完整内容并追加提示
                preview, _, saved_to = _truncate_text(
                    value,
                    max_lines=max_lines,
                    max_bytes=max_bytes,
                    prefix=f"{prefix}_{key}",
                )
                result[key] = preview
                result[f"_{key}_truncated"] = True
                if saved_to:
                    result[f"_{key}_saved_to"] = saved_to
            else:
                result[key] = value
        elif isinstance(value, list):
            serialized = json.dumps(value, ensure_ascii=False, default=str)
            if _exceeds_limit(serialized, max_lines, max_bytes):
                # 大列表字段：保留前 N 项，完整列表保存到文件
                kept_items, saved_to = _truncate_list(
                    value,
                    max_lines=max_lines,
                    max_bytes=max_bytes,
                    prefix=f"{prefix}_{key}",
                )
                result[key] = kept_items
                result[f"_{key}_truncated"] = True
                result[f"_{key}_saved_to"] = saved_to
                result[f"_{key}_total"] = len(value)
            else:
                result[key] = value
        elif isinstance(value, dict):
            # 嵌套 dict 递归截断
            result[key] = _truncate_dict(
                value,
                max_lines=max_lines,
                max_bytes=max_bytes,
                prefix=f"{prefix}_{key}",
            )
        else:
            # 其他类型转为字符串
            result[key] = str(value)

    # ---- 最终检查：序列化后仍超限 ----
    serialized = json.dumps(result, ensure_ascii=False, default=str)
    if len(serialized.encode("utf-8")) > max_bytes * 2:
        full_saved = _save_to_temp_file(
            json.dumps(d, ensure_ascii=False, default=str), prefix=prefix
        )
        # 只保留基本类型字段 + 截断标记
        minimal: dict[str, Any] = {
            k: v
            for k, v in result.items()
            if isinstance(v, (bool, int, float, str)) or v is None
        }
        minimal["_output_truncated"] = True
        minimal["_output_saved_to"] = full_saved
        return minimal

    return result


def _truncate_list(
    lst: list, *, max_lines: int, max_bytes: int, prefix: str
) -> tuple[list, Optional[str]]:
    """截断列表：保留前 N 项（按字节累加控制），完整列表保存到文件

    Returns:
        (截断后的列表, 保存路径)
    """
    # 完整列表保存到文件
    saved_to = _save_to_temp_file(
        json.dumps(lst, ensure_ascii=False, default=str), prefix=prefix
    )

    # 逐项累加字节，控制在 max_bytes // 2 以内
    kept: list = []
    byte_budget = max_bytes // 2
    current_bytes = 0

    for item in lst:
        item_str = (
            json.dumps(item, ensure_ascii=False, default=str)
            if isinstance(item, (dict, list))
            else str(item)
        )
        item_bytes = len(item_str.encode("utf-8"))
        if current_bytes + item_bytes > byte_budget:
            break
        # 嵌套 dict 递归截断
        if isinstance(item, dict):
            kept.append(
                _truncate_dict(
                    item, max_lines=max_lines, max_bytes=max_bytes, prefix=prefix
                )
            )
        else:
            kept.append(item)
        current_bytes += item_bytes

    return kept, saved_to


def _truncate_text(
    text: str,
    *,
    max_lines: int,
    max_bytes: int,
    prefix: str,
) -> tuple[str, bool, Optional[str]]:
    """纯文本截断：超限时保存完整内容到临时文件，返回前50行预览

    Args:
        text: 待截断文本
        max_lines: 最大行数（触发阈值）
        max_bytes: 最大字节数（触发阈值）
        prefix: 临时文件名前缀

    Returns:
        (预览文本, 是否截断, 保存路径)
    """
    lines = text.splitlines()
    total_lines = len(lines)
    text_bytes = len(text.encode("utf-8"))

    # 未超限，原样返回
    if total_lines <= max_lines and text_bytes <= max_bytes:
        return text, False, None

    # 超限：保存完整内容到临时文件
    saved_to = _save_to_temp_file(text, prefix=prefix)

    # 逐行累加字节，50行 + 5KB双重限制，先到先停
    out: list[str] = []
    byte_count = 0

    for i in range(min(total_lines, _PREVIEW_LINES)):
        line = lines[i]
        line_size = len(line.encode("utf-8")) + (1 if out else 0)
        if byte_count + line_size > _PREVIEW_BYTES:
            break
        out.append(line)
        byte_count += line_size

    preview = "\n".join(out)
    hint = f"\n\n[输出已截断，共 {total_lines} 行。完整内容已保存到: {saved_to}]"
    preview += hint

    return preview, True, saved_to


def _exceeds_limit(text: str, max_lines: int, max_bytes: int) -> bool:
    """检查文本是否超过行数或字节限制"""
    lines = text.splitlines()
    return len(lines) > max_lines or len(text.encode("utf-8")) > max_bytes


def _save_to_temp_file(text: str, prefix: str) -> str:
    """保存完整内容到临时文件，返回文件路径字符串"""
    temp_dir = get_temp_dir()
    temp_filename = f"{prefix}_{uuid.uuid4().hex[:8]}.log"
    temp_path = temp_dir / temp_filename
    temp_path.write_text(text, encoding="utf-8")
    return str(temp_path)
