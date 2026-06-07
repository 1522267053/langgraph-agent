"""
媒体内容解析器

将 input_data 中的文件信息转换为 LangChain 多模态 content blocks。
支持本地文件(base64)和 URL 两种来源。
"""

import base64
import logging
from pathlib import Path
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
    "audio": AUDIO_EXTENSIONS,
    "video": VIDEO_EXTENSIONS,
    "pdf": PDF_EXTENSIONS,
    "xlsx": XLSX_EXTENSIONS,
}


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


def _read_file_as_base64(file_path: str) -> tuple[str, str] | None:
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
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("utf-8")
        ext = path.suffix.lower()
        mime_map = {
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
            ".mp4": "video/mp4",
            ".webm": "video/webm",
            ".pdf": "application/pdf",
        }
        mime_type = mime_map.get(ext, "application/octet-stream")
        return b64, mime_type
    except Exception as e:
        logger.warning("Failed to read file %s: %s", file_path, e)
        return None


def _is_enabled(capabilities: dict, capability: str) -> bool:
    return bool(capabilities.get(capability, False))


def _resolve_file_info_to_block(file_info: dict, capabilities: dict) -> dict | None:
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

    result = _read_file_as_base64(file_path)
    if not result:
        return None

    b64_data, detected_mime = result

    if capability == "image":
        return {
            "type": "image_url",
            "image_url": {"url": f"data:{detected_mime};base64,{b64_data}"},
        }
    elif capability == "audio":
        return {
            "type": "text",
            "text": f"[audio data: {original_name or 'audio'}]",
        }
    elif capability == "video":
        return {
            "type": "text",
            "text": f"[video data: {original_name or 'video'}]",
        }
    elif capability == "pdf":
        return {
            "type": "text",
            "text": f"[pdf document: {original_name or 'document'}]",
        }
    elif capability == "xlsx":
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

    if capability == "image":
        return {
            "type": "image_url",
            "image_url": {"url": url},
        }

    return None


def _resolve_string_value(value: str, capabilities: dict) -> dict | None:
    if _is_url(value):
        return _resolve_url_to_block(value, capabilities)
    if len(value) > 4096:
        return None
    try:
        path = Path(value)
        if path.exists() and path.is_file():
            return _resolve_file_info_to_block({"file_path": value}, capabilities)
    except OSError:
        return None
    return None


def _is_file_info(value) -> bool:
    """判断 value 是否是文件信息 dict"""
    if not isinstance(value, dict):
        return False
    return bool(value.get("file_path") or value.get("id"))


def collect_media_blocks(
    input_data: dict, capabilities: dict | None = None
) -> tuple[list[dict], str]:
    """
    从 input_data 中收集媒体 content blocks 和文件索引文本

    Returns:
        (media_blocks, file_index_text)
        - media_blocks: LangChain 多模态 content blocks（受 capabilities 控制）
        - file_index_text: 上传文件的索引文本，包含 file_id 供 LLM 引用（始终生成）
    """
    blocks: list[dict] = []
    file_entries: list[str] = []
    caps = capabilities or {}

    for value in input_data.values():
        if isinstance(value, dict):
            file_id = value.get("id")
            block = _resolve_file_info_to_block(value, caps)
            if block:
                blocks.append(block)
                if block.get("type") != "image_url" and _is_file_info(value):
                    _append_file_entry(file_entries, value, file_id)
            elif _is_file_info(value):
                _append_file_entry(file_entries, value, file_id)
        elif isinstance(value, list):
            for item in value:
                if isinstance(item, dict):
                    file_id = item.get("id")
                    block = _resolve_file_info_to_block(item, caps)
                    if block:
                        blocks.append(block)
                        if block.get("type") != "image_url" and _is_file_info(item):
                            _append_file_entry(file_entries, item, file_id)
                    elif _is_file_info(item):
                        _append_file_entry(file_entries, item, file_id)
                elif isinstance(item, str):
                    block = _resolve_string_value(item, caps)
                    if block:
                        blocks.append(block)
        elif isinstance(value, str):
            block = _resolve_string_value(value, caps)
            if block:
                blocks.append(block)

    index_text = ""
    if file_entries:
        index_lines = ["[附件文件]", *file_entries]
        index_text = "\n".join(index_lines)

    return blocks, index_text


def _append_file_entry(
    entries: list[str], file_info: dict, file_id: int | None
) -> None:
    """将文件信息追加到索引列表，包含绝对路径"""
    original_name = file_info.get("original_name") or file_info.get("name") or "unknown"
    mime_type = file_info.get("mime_type") or file_info.get("type") or ""
    file_path = file_info.get("file_path") or ""
    abs_path = str(settings.get_absolute_path(file_path).resolve()) if file_path else ""
    if file_id is not None:
        entries.append(
            f"- file_id={file_id}, {original_name} ({mime_type})"
            + (f", path={abs_path}" if abs_path else "")
        )
    else:
        entries.append(
            f"- {original_name} ({mime_type})"
            + (f", path={abs_path}" if abs_path else "")
        )


def build_multimodal_content(text: str, media_blocks: list[dict]) -> str | list[dict]:
    if not media_blocks:
        return text

    content: list[dict] = [{"type": "text", "text": text}]
    content.extend(media_blocks)
    return content
