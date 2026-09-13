"""
调试临时文件保存工具

将字节写入 workspace/temp/debug_uploads/<debug_session_id>/ 目录，
**不写 File 数据库记录**、**不调 record_tool_file_change**，避免污染
生产环境文件表与会话回退。

仅供 Python 节点调试面板（debug_api）使用，文件通过 /debug-uploads
静态路由对外提供预览，7 天后由 scheduler 统一清理。
"""

import asyncio
import uuid

from app.config.build_utils import get_temp_dir

# MIME → 扩展名映射（与 media_file 保持一致）
_MIME_TO_EXT: dict[str, str] = {
    "image/png": "png",
    "image/jpeg": "jpg",
    "image/gif": "gif",
    "image/webp": "webp",
    "image/svg+xml": "svg",
    "image/bmp": "bmp",
    "audio/mpeg": "mp3",
    "audio/wav": "wav",
    "audio/ogg": "ogg",
    "audio/flac": "flac",
    "audio/aac": "aac",
    "video/mp4": "mp4",
    "video/webm": "webm",
    "video/avi": "avi",
    "video/quicktime": "mov",
}


def _write_bytes(path, content: bytes) -> None:
    with open(path, "wb") as f:
        f.write(content)


async def save_debug_bytes(
    content: bytes,
    debug_session_id: str,
    mime_type: str,
    original_name: str = "",
) -> dict:
    """将字节写入调试临时目录，返回与 save_media_bytes 一致的预览结果

    Args:
        content: 文件字节
        debug_session_id: 调试会话 ID（前端 localStorage 持久化）
        mime_type: MIME 类型
        original_name: 原始文件名（仅用于展示）

    Returns:
        {"success", "preview_url", "file_name", "mime_type"}
        - preview_url: /debug-uploads/<session_id>/<file> 形式
    """
    ext = _MIME_TO_EXT.get(mime_type, "bin")
    unique_name = f"{uuid.uuid4().hex}.{ext}"

    target_dir = get_temp_dir() / "debug_uploads" / debug_session_id
    target_dir.mkdir(parents=True, exist_ok=True)

    absolute_path = target_dir / unique_name
    await asyncio.to_thread(_write_bytes, absolute_path, content)

    if not original_name:
        original_name = f"debug.{ext}"

    # preview_url 直接指向 /debug-uploads/<session_id>/<file>，
    # 由 app_setup._mount_static_files 提供的 StaticFiles 路由处理
    return {
        "success": True,
        "preview_url": f"/debug-uploads/{debug_session_id}/{unique_name}",
        "file_name": original_name,
        "mime_type": mime_type,
    }
