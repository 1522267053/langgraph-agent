"""
市场资源导入服务

将市场下载的资源文件导入到本地
"""

import json
import logging
import os
import zipfile
from io import BytesIO
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

from app.config.settings import settings
from app.models.skill import Skill

logger = logging.getLogger(__name__)


class MarketplaceImportService:
    """市场资源导入服务"""

    async def import_resource(
        self,
        db: AsyncSession,
        resource_info: dict,
        file_bytes: bytes,
    ) -> dict:
        """根据资源类型分发导入"""
        resource_type = resource_info.get("resource_type", "")
        name = resource_info.get("name", "unknown")

        try:
            if resource_type == "skill":
                result = await self._import_skill(db, file_bytes, resource_info)
            elif resource_type in ("flow", "agent"):
                result = await self._import_flow(db, file_bytes, resource_info)
            else:
                return {
                    "success": False,
                    "message": f"不支持的资源类型: {resource_type}",
                }
            return result
        except Exception as e:
            logger.exception("导入资源「%s」失败", name)
            return {"success": False, "message": f"导入失败: {e}"}

    async def _import_skill(
        self, db: AsyncSession, file_bytes: bytes, resource_info: dict
    ) -> dict:
        """导入 Skill：解压 ZIP → 解析 SKILL.md → 保存到本地"""
        upload_dir = settings.get_absolute_path(settings.upload_dir)
        skill_name = resource_info.get("name", "unknown_skill")

        existing = await db.execute(
            select(Skill).where(Skill.name == skill_name, Skill.is_delete == 0)
        )
        if existing.scalar_one_or_none():
            skill_name = f"{skill_name}_{resource_info.get('id', '0')}"

        skill_dir = upload_dir / "skills" / skill_name
        if skill_dir.exists():
            import shutil

            shutil.rmtree(skill_dir, ignore_errors=True)
        skill_dir.mkdir(parents=True, exist_ok=True)

        try:
            with zipfile.ZipFile(BytesIO(file_bytes), "r") as zf:
                zf.extractall(skill_dir)
        except zipfile.BadZipFile:
            raise ValueError("无效的 ZIP 文件")

        skill_md = skill_dir / "SKILL.md"
        if not skill_md.exists():
            for root, _dirs, files in os.walk(skill_dir):
                if "SKILL.md" in files:
                    skill_md = Path(root) / "SKILL.md"
                    break
        if not skill_md.exists():
            raise ValueError("ZIP 中未找到 SKILL.md 文件")

        skill_md.read_text(encoding="utf-8")
        description = resource_info.get("description", "")

        skill_path = f"{settings.upload_dir}/skills/{skill_name}/SKILL.md"
        skill_obj = Skill(
            name=skill_name,
            description=description,
            skill_path=skill_path,
            category=resource_info.get("category"),
            tags=resource_info.get("tags"),
            icon=resource_info.get("icon"),
            is_enabled=1,
            is_system=0,
            sort_order=0,
        )
        db.add(skill_obj)
        await db.commit()
        await db.refresh(skill_obj)

        return {
            "success": True,
            "message": f"技能「{skill_name}」导入成功",
            "type": "skill",
            "id": skill_obj.id,
            "name": skill_name,
        }

    async def _import_flow(
        self, db: AsyncSession, file_bytes: bytes, resource_info: dict
    ) -> dict:
        """导入 Flow/Agent：解析 JSON → 复用 flow_transfer_service"""
        try:
            import_data = json.loads(file_bytes.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            raise ValueError("无效的 JSON 文件")

        from app.services.flow_transfer_service import flow_transfer_service

        created, warnings = await flow_transfer_service.import_flows(db, import_data)
        message = f"导入 {len(created)} 个流程"
        if warnings:
            message += f"，{len(warnings)} 个警告"

        return {
            "success": True,
            "message": message,
            "type": resource_info.get("resource_type", "flow"),
            "created": created,
            "warnings": warnings,
        }


marketplace_import_service = MarketplaceImportService()
