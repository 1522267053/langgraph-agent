"""
文件服务
"""

import asyncio
import logging
import os
import uuid
from datetime import date
from pathlib import Path
from typing import List, Optional
from fastapi import HTTPException, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select, or_

from app.models.file import File
from app.schemas.file_schema import FileBase, FileUpdate
from app.services.base_service import BaseService
from app.config.settings import settings

logger = logging.getLogger(__name__)


def _write_bytes(path: Path, content: bytes) -> None:
    """同步写入字节到文件（供 asyncio.to_thread 调用）"""
    with open(path, "wb") as f:
        f.write(content)


class FileService(BaseService[File, FileBase, FileUpdate]):
    """文件服务类"""

    def __init__(self):
        super().__init__(File)

    def _apply_filters(self, query, count_query, condition):
        mime_type_value = None
        original_name_value = None
        if condition and hasattr(condition, "mime_type") and condition.mime_type:
            mime_type_value = condition.mime_type
            condition.mime_type = None
        if (
            condition
            and hasattr(condition, "original_name")
            and condition.original_name
        ):
            original_name_value = condition.original_name
            condition.original_name = None

        query, count_query = super()._apply_filters(query, count_query, condition)

        if original_name_value:
            condition.original_name = original_name_value
            query, count_query = self._apply_like_filter(
                query, count_query, "original_name", original_name_value
            )

        if mime_type_value:
            condition.mime_type = mime_type_value
            rules = [r.strip() for r in mime_type_value.split(",") if r.strip()]
            like_patterns = []
            ext_types = []
            exact_types = []
            for rule in rules:
                if rule.endswith("/*"):
                    like_patterns.append(rule[:-1] + "%")
                elif rule.startswith("."):
                    ext_types.append(rule.lstrip(".").lower())
                else:
                    exact_types.append(rule)
            or_conditions = []
            if like_patterns:
                or_conditions.extend([File.mime_type.like(p) for p in like_patterns])
            if ext_types:
                or_conditions.append(File.file_type.in_(ext_types))
            if exact_types:
                or_conditions.append(File.mime_type.in_(exact_types))
            if or_conditions:
                mime_filter = or_(*or_conditions)
                if query is not None:
                    query = query.where(mime_filter)
                if count_query is not None:
                    count_query = count_query.where(mime_filter)
        return query, count_query

    @property
    def settings(self):
        return settings

    async def upload_file(
        self, db: AsyncSession, file: UploadFile, source_type: Optional[str] = None
    ) -> File:
        ext = Path(file.filename or "").suffix.lower() or ""
        unique_name = f"{uuid.uuid4().hex}{ext}"
        today = date.today().isoformat()
        relative_path = f"{self.settings.upload_dir}/{today}/{unique_name}"
        absolute_path = settings.get_absolute_path(relative_path)
        absolute_path.parent.mkdir(parents=True, exist_ok=True)

        content = await file.read()
        file_size = len(content)

        max_bytes = settings.max_upload_size * 1024 * 1024
        if file_size > max_bytes:
            raise ValueError(
                f"文件大小 {file_size / 1024 / 1024:.1f}MB 超过限制"
                f"（最大 {settings.max_upload_size}MB）"
            )

        await asyncio.to_thread(_write_bytes, absolute_path, content)
        mime_type = file.content_type or "application/octet-stream"
        file_type = ext.lstrip(".")

        file_obj = File(
            source_type=source_type or "",
            original_name=file.filename or "unnamed",
            file_path=relative_path,
            file_type=file_type,
            file_size=file_size,
            mime_type=mime_type,
            preview_url=f"/{relative_path}",
        )
        db.add(file_obj)
        await db.commit()
        await db.refresh(file_obj)
        return file_obj

    async def list_files(
        self, db: AsyncSession, source_type: Optional[str] = None
    ) -> List[File]:
        stmt = select(File).where(File.is_delete == 0)
        if source_type:
            stmt = stmt.where(File.source_type == source_type)
        stmt = stmt.order_by(File.create_time.desc())
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def get_download_path(
        self, db: AsyncSession, file_id: int
    ) -> tuple[Path, str, str]:
        file_obj = await self.get_by_id(db, file_id, raise_not_found=False)
        if not file_obj:
            raise FileNotFoundError(f"文件不存在: {file_id}")
        file_path = settings.get_absolute_path(file_obj.file_path)
        if not file_path.exists():
            raise FileNotFoundError(f"文件不存在: {file_obj.file_path}")
        return file_path, file_obj.original_name, file_obj.mime_type

    async def delete_with_physical(self, db: AsyncSession, file_id: int) -> None:
        """删除文件记录并物理删除文件，文件被占用时自动重试"""
        file_obj = await self.get_by_id(db, file_id, raise_not_found=False)
        if not file_obj:
            return
        file_path = settings.get_absolute_path(file_obj.file_path)
        if file_path.exists():
            max_retries = 3
            for attempt in range(max_retries):
                try:
                    os.remove(file_path)
                    break
                except PermissionError:
                    if attempt < max_retries - 1:
                        logger.warning(
                            "文件删除失败（被占用），重试 %d/%d: %s",
                            attempt + 1,
                            max_retries,
                            file_path,
                        )
                        await asyncio.sleep(0.5)
                    else:
                        raise HTTPException(
                            status_code=400,
                            detail="文件被占用中，删除失败，请稍后再尝试删除",
                        )
        await self.delete(db, file_id)


file_service = FileService()
