"""
媒体内容解析器

将 input_data 中的文件信息转换为 LangChain 多模态 content blocks。
支持本地文件(base64)和 URL 两种来源。
"""

import asyncio
import base64
import logging
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlparse

from app.config.settings import settings

logger = logging.getLogger(__name__)

MAX_FILE_SIZE = 20 * 1024 * 1024

IMAGE_TYPES = {
    "image/png",
    "image/jpeg",
    "image/gif",
    "image/webp",
    "image/bmp",
    "image/tiff",
    "image/svg+xml",
}

AUDIO_TYPES = {
    "audio/mpeg",
    "audio/mp3",
    "audio/wav",
    "audio/ogg",
    "audio/mp4",
    "audio/webm",
    "audio/flac",
    "audio/aac",
}

VIDEO_TYPES = {
    "video/mp4",
    "video/webm",
    "video/avi",
    "video/mpeg",
    "video/quicktime",
    "video/x-msvideo",
}

PDF_TYPE = "application/pdf"

IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp", ".tiff", ".svg"}
AUDIO_EXTENSIONS = {".mp3", ".wav", ".ogg", ".mp4", ".webm", ".flac", ".aac"}
VIDEO_EXTENSIONS = {".mp4", ".webm", ".avi", ".mpeg", ".mov", ".mkv"}
PDF_EXTENSIONS = {".pdf"}
XLSX_EXTENSIONS = {".xlsx", ".xls"}

CAPABILITY_TO_MIME_MAP = {
    "image": IMAGE_TYPES,
    "audio": AUDIO_TYPES,
    "video": VIDEO_TYPES,
    "pdf": {PDF_TYPE},
    "xlsx": {
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel",
    },
}

CAPABILITY_TO_EXT_MAP = {
    "image": IMAGE_EXTENSIONS,
    # video 在 audio 之前：.mp4/.webm 同时出现在两种集合，优先按视频处理
    "video": VIDEO_EXTENSIONS,
    "audio": AUDIO_EXTENSIONS,
    "pdf": PDF_EXTENSIONS,
    "xlsx": XLSX_EXTENSIONS,
}

_EXT_TO_MIME = {
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".gif": "image/gif",
    ".webp": "image/webp",
    ".bmp": "image/bmp",
    ".svg": "image/svg+xml",
    ".mp3": "audio/mpeg",
    ".wav": "audio/wav",
    ".ogg": "audio/ogg",
    ".flac": "audio/flac",
    ".aac": "audio/aac",
    ".m4a": "audio/mp4",
    ".mp4": "video/mp4",
    ".webm": "video/webm",
    ".avi": "video/x-msvideo",
    ".mov": "video/quicktime",
    ".mkv": "video/x-matroska",
    ".mpeg": "video/mpeg",
    ".pdf": "application/pdf",
}


def guess_mime_by_ext(name_or_path: str) -> str:
    """按文件名/路径扩展名推断 MIME 类型，未知扩展名返回空字符串。"""
    if not name_or_path:
        return ""
    ext = Path(name_or_path).suffix.lower()
    return _EXT_TO_MIME.get(ext, "")


def _classify_mime(mime_type: str) -> str | None:
    for capability, mimes in CAPABILITY_TO_MIME_MAP.items():
        if mime_type.lower() in mimes:
            return capability
    return None


def _classify_by_ext(path_or_name: str) -> str | None:
    ext = Path(path_or_name).suffix.lower()
    for capability, exts in CAPABILITY_TO_EXT_MAP.items():
        if ext in exts:
            return capability
    return None


def _is_url(value: str) -> bool:
    try:
        result = urlparse(value)
        return result.scheme in ("http", "https") and bool(result.netloc)
    except Exception:
        return False


async def _read_file_as_base64(file_path: str) -> tuple[str, str] | None:
    path = Path(file_path)
    if not path.exists() or not path.is_file():
        logger.warning("Media file not found: %s", file_path)
        return None
    file_size = path.stat().st_size
    if file_size > MAX_FILE_SIZE:
        logger.warning(
            "File too large (%d bytes, max %d): %s", file_size, MAX_FILE_SIZE, file_path
        )
        return None
    try:
        data = await asyncio.to_thread(path.read_bytes)
        b64 = base64.b64encode(data).decode("utf-8")
        ext = path.suffix.lower()
        mime_type = _EXT_TO_MIME.get(ext, "application/octet-stream")
        return b64, mime_type
    except Exception as e:
        logger.warning("Failed to read file %s: %s", file_path, e)
        return None


def _is_enabled(capabilities: dict, capability: str) -> bool:
    return bool(capabilities.get(capability, False))


# 各适配器（langchain 包）已实现媒体块转换的能力集。
# 实测：langchain-openai 1.4.1 支持 image/audio（file 含 pdf/xlsx），video 抛错；
# langchain-anthropic 1.5.3 仅支持 image/file，video/audio 抛错。
# pdf/xlsx 在我们的链路中始终生成文本占位块，不经过媒体块转换层。
ADAPTER_MEDIA_SUPPORT = {
    "openai_compatible": {"image", "audio", "pdf", "xlsx"},
    "anthropic": {"image", "pdf", "xlsx"},
}


def filter_capabilities_by_adapter(capabilities: dict, adapter_type: str) -> dict:
    """按适配器已实现的媒体转换能力过滤 capabilities

    模型 capabilities 表示"模型是否支持该模态"，适配器是否已实现对应
    标准块的转换是另一维度。两者取交集：适配器未实现的模态（如 openai
    兼容下的 video、anthropic 下的 video/audio）降级为文本占位，避免
    langchain 在发送时抛 ValueError。

    Args:
        capabilities: 模型能力开关（来自节点配置）
        adapter_type: 适配器类型（如 "openai_compatible"、"anthropic"）

    Returns:
        过滤后的能力开关副本，未知适配器回退 openai_compatible 集合
    """
    supported = ADAPTER_MEDIA_SUPPORT.get(
        adapter_type, ADAPTER_MEDIA_SUPPORT["openai_compatible"]
    )
    return {
        key: bool(value) and key in supported for key, value in capabilities.items()
    }


async def _resolve_file_info_to_block(
    file_info: dict, capabilities: dict
) -> dict | None:
    if not isinstance(file_info, dict):
        return None

    file_path = file_info.get("file_path") or file_info.get("path")
    mime_type = file_info.get("mime_type") or file_info.get("type", "")
    original_name = file_info.get("original_name") or file_info.get("name") or ""

    if not file_path:
        return None

    capability = _classify_mime(mime_type) or _classify_by_ext(file_path)
    if not capability:
        return None
    if not _is_enabled(capabilities, capability):
        return None

    if capability in ("image", "video", "audio"):
        result = await _read_file_as_base64(file_path)
        if result:
            b64_data, detected_mime = result
            if capability in ("image", "video", "audio"):
                return {
                    "type": capability,
                    "base64": b64_data,
                    "mime_type": detected_mime,
                }
        # 文件过大无法内联 base64，回退为文本占位（LLM 仍能感知附件）
        return {
            "type": "text",
            "text": f"[{capability} data: {original_name or capability}]",
        }

    if capability == "pdf":
        return {
            "type": "text",
            "text": f"[pdf document: {original_name or 'document'}]",
        }
    if capability == "xlsx":
        return {
            "type": "text",
            "text": f"[excel spreadsheet: {original_name or 'spreadsheet'}]",
        }

    return None


def _resolve_url_to_block(url: str, capabilities: dict) -> dict | None:
    if not _is_url(url):
        return None

    path_str = urlparse(url).path
    capability = _classify_by_ext(path_str)
    if not capability:
        return None
    if not _is_enabled(capabilities, capability):
        return None

    if capability in ("image", "video"):
        return {
            "type": capability,
            "url": url,
        }

    return None


async def _resolve_string_value(value: str, capabilities: dict) -> dict | None:
    if _is_url(value):
        return _resolve_url_to_block(value, capabilities)
    if len(value) > 4096:
        return None
    try:
        path = Path(value)
        if path.exists() and path.is_file():
            return await _resolve_file_info_to_block({"file_path": value}, capabilities)
    except OSError:
        return None
    return None


def _is_file_info(value) -> bool:
    """判断 value 是否是文件信息 dict"""
    if not isinstance(value, dict):
        return False
    return bool(value.get("file_path") or value.get("id"))


def _resolve_file_meta(file_info: dict) -> tuple[str, str, str]:
    """从 file_info 提取 (original_name, mime_type, abs_path)"""
    original_name = file_info.get("original_name") or file_info.get("name") or "unknown"
    file_path = file_info.get("file_path") or ""
    mime_type = file_info.get("mime_type") or file_info.get("type") or ""
    if not mime_type:
        mime_type = guess_mime_by_ext(file_path or original_name)
    abs_path = str(settings.get_absolute_path(file_path).resolve()) if file_path else ""
    return original_name, mime_type, abs_path


def format_file_label(
    file_id: int | None, original_name: str, mime_type: str, abs_path: str
) -> str:
    """格式化文件内联标注文本，包含 file_id、文件名、MIME 和绝对路径"""
    if file_id is not None:
        label = f"[附件 file_id={file_id}: {original_name} ({mime_type})"
    else:
        label = f"[附件: {original_name} ({mime_type})"
    if abs_path:
        label += f", path={abs_path}"
    return label + "]"


def _build_inline_label(file_info: dict, file_id: int | None) -> str:
    """从 file_info 构建媒体块内联标注文本"""
    original_name, mime_type, abs_path = _resolve_file_meta(file_info)
    return format_file_label(file_id, original_name, mime_type, abs_path)


def _append_file_entry(
    entries: list[str], file_info: dict, file_id: int | None
) -> None:
    """将文件信息追加到索引列表，包含绝对路径"""
    original_name, mime_type, abs_path = _resolve_file_meta(file_info)
    if file_id is not None:
        entry = f"- file_id={file_id}, {original_name} ({mime_type})"
    else:
        entry = f"- {original_name} ({mime_type})"
    if abs_path:
        entry += f", path={abs_path}"
    entries.append(entry)


async def _process_file_entry(
    file_info: dict,
    caps: dict,
    blocks: list[dict],
    file_entries: list[str],
    resolve_path: Callable[[dict], Awaitable[str | None]] | None = None,
) -> None:
    """处理单个文件信息，媒体块(image/audio/video)前插入内联标注，其余归入索引文本"""
    file_id = file_info.get("id")

    if resolve_path:
        try:
            resolved = await resolve_path(file_info)
        except Exception:
            resolved = None
        if resolved:
            file_info = {**file_info, "file_path": resolved}

    block = await _resolve_file_info_to_block(file_info, caps)
    if block:
        if block.get("type") in ("image", "audio", "video"):
            blocks.append(
                {"type": "text", "text": _build_inline_label(file_info, file_id)}
            )
            blocks.append(block)
        else:
            blocks.append(block)
            if _is_file_info(file_info):
                _append_file_entry(file_entries, file_info, file_id)
    elif _is_file_info(file_info):
        _append_file_entry(file_entries, file_info, file_id)


async def collect_media_blocks(
    input_data: dict,
    capabilities: dict | None = None,
    resolve_path: Callable[[dict], Awaitable[str | None]] | None = None,
) -> tuple[list[dict], str]:
    """
    从 input_data 中收集媒体 content blocks 和文件索引文本

    媒体块（image/audio/video）前插入内联标注（含 file_id 和路径），与块紧邻；
    非媒体文件（pdf/xlsx 等）归入 [附件文件] 索引文本。

    Args:
        input_data: 输入数据（值为 file_info dict / list / str）
        capabilities: 模型能力开关
        resolve_path: 异步路径解析策略，None 时从 file_info["file_path"] 取路径

    Returns:
        (media_blocks, file_index_text)
        - media_blocks: 含内联标注和媒体块的 content blocks 列表
        - file_index_text: 非媒体文件的索引文本，包含 file_id 供 LLM 引用
    """
    blocks: list[dict] = []
    file_entries: list[str] = []
    caps = capabilities or {}

    for value in input_data.values():
        if isinstance(value, dict):
            await _process_file_entry(value, caps, blocks, file_entries, resolve_path)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    await _process_file_entry(
                        item, caps, blocks, file_entries, resolve_path
                    )
                elif isinstance(item, str):
                    block = await _resolve_string_value(item, caps)
                    if block:
                        blocks.append(block)
        elif isinstance(value, str):
            block = await _resolve_string_value(value, caps)
            if block:
                blocks.append(block)

    index_text = ""
    if file_entries:
        index_lines = ["[附件文件]", *file_entries]
        index_text = "\n".join(index_lines)

    return blocks, index_text


def build_multimodal_content(text: str, media_blocks: list[dict]) -> str | list[dict]:
    if not media_blocks:
        return text

    content: list[dict] = [{"type": "text", "text": text}]
    content.extend(media_blocks)
    return content
