"""
共享媒体文件保存工具

将字节写入 uploads 目录并创建 File 数据库记录，
返回统一格式的预览结果供 ai_provider、api_handler、python_handler 使用。
"""

import asyncio
import uuid
from datetime import date

from app.config.database import AsyncSessionLocal
from app.config.settings import settings
from app.models.file import File


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


async def save_media_bytes(
    content: bytes,
    mime_type: str,
    source_type: str = "tool_generated",
    flow_id: int = 0,
    original_name: str = "",
) -> dict:
    """
    将媒体字节写入 uploads → 创建 File DB 记录 → 返回标准预览结果

    Returns:
        {"success": True, "preview_url": "/uploads/...", "download_url": "/api/file/download/{id}",
         "file_name": "...", "mime_type": "...", "file_id": id}
    """
    ext = _MIME_TO_EXT.get(mime_type, "bin")
    unique_name = f"{uuid.uuid4().hex}.{ext}"
    today = date.today().isoformat()
    relative_path = f"{settings.upload_dir}/{source_type}/{today}/{unique_name}"
    absolute_path = settings.get_absolute_path(relative_path)
    absolute_path.parent.mkdir(parents=True, exist_ok=True)

    await asyncio.to_thread(_write_bytes, absolute_path, content)

    if not original_name:
        original_name = f"generated.{ext}"

    async with AsyncSessionLocal() as db:
        file_obj = File(
            flow_id=flow_id,
            source_type=source_type,
            original_name=original_name,
            file_path=relative_path,
            file_type=ext,
            file_size=len(content),
            mime_type=mime_type,
        )
        db.add(file_obj)
        await db.commit()
        await db.refresh(file_obj)

    return {
        "success": True,
        "preview_url": f"/{file_obj.file_path}",
        "download_url": f"/api/file/download/{file_obj.id}",
        "file_name": file_obj.original_name,
        "mime_type": file_obj.mime_type,
        "file_id": file_obj.id,
    }
