"""
Agent Skill 服务模块

提供 Skill 的 CRUD 操作
"""

import shutil
from pathlib import Path
from typing import List, Optional
from sqlalchemy import select, or_
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.settings import settings
from app.models.skill import Skill
from app.services.base_service import BaseService
from app.schemas.skill_schema import SkillCreate, SkillUpdate
from app.utils.skill_uploader import skill_uploader


class SkillService(BaseService[Skill, SkillCreate, SkillUpdate]):
    """
    Skill 服务类

    提供技能的 CRUD 操作和查询功能
    """

    def __init__(self):
        super().__init__(Skill)

    async def delete(self, db: AsyncSession, id: int) -> None:
        skill = await self.get_by_id(db, id)
        if skill and skill.skill_path:
            skill_dir = Path(settings.get_absolute_path(skill.skill_path)).parent
            if skill_dir.exists():
                shutil.rmtree(skill_dir, ignore_errors=True)
        await super().delete(db, id)

    async def bulk_delete(self, db: AsyncSession, ids: List[int]) -> int:
        for skill_id in ids:
            skill = await self.get_by_id(db, skill_id)
            if skill and skill.skill_path:
                skill_dir = Path(settings.get_absolute_path(skill.skill_path)).parent
                if skill_dir.exists():
                    shutil.rmtree(skill_dir, ignore_errors=True)
        return await super().bulk_delete(db, ids)

    def _apply_filters(self, query, count_query, condition):
        query, count_query = super()._apply_filters(query, count_query, condition)

        if condition:
            if hasattr(condition, "name") and condition.name:
                query, count_query = self._apply_like_filter(
                    query, count_query, "name", condition.name
                )

            for field in ["category", "is_enabled"]:
                if hasattr(condition, field) and getattr(condition, field) is not None:
                    value = getattr(condition, field)
                    if query is not None:
                        query = query.where(getattr(Skill, field) == value)
                    if count_query is not None:
                        count_query = count_query.where(getattr(Skill, field) == value)

        return query, count_query

    async def get_enabled_skills(self, db: AsyncSession) -> List[Skill]:
        query = (
            select(Skill)
            .where(Skill.is_enabled == 1, Skill.is_delete == 0)
            .order_by(Skill.sort_order, Skill.create_time.desc())
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_skills_by_category(
        self, db: AsyncSession, category: str
    ) -> List[Skill]:
        query = (
            select(Skill)
            .where(
                Skill.category == category, Skill.is_enabled == 1, Skill.is_delete == 0
            )
            .order_by(Skill.sort_order, Skill.create_time.desc())
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def get_by_name(self, db: AsyncSession, name: str) -> Optional[Skill]:
        query = select(Skill).where(Skill.name == name, Skill.is_delete == 0)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def search_skills(
        self, db: AsyncSession, keyword: str, limit: int = 10
    ) -> List[Skill]:
        search_pattern = f"%{keyword}%"
        query = (
            select(Skill)
            .where(
                Skill.is_enabled == 1,
                Skill.is_delete == 0,
                or_(
                    Skill.name.like(search_pattern),
                    Skill.description.like(search_pattern),
                    Skill.tags.like(search_pattern),
                ),
            )
            .order_by(Skill.sort_order)
            .limit(limit)
        )
        result = await db.execute(query)
        return list(result.scalars().all())

    async def reload_from_file(self, db: AsyncSession, skill_id: int) -> Skill:
        """从磁盘 SKILL.md 文件重新加载 name 和 description"""
        skill = await self.get_by_id(db, skill_id)
        if not skill or not skill.skill_path:
            raise ValueError("Skill 文件路径不存在")

        file_path = settings.get_absolute_path(skill.skill_path)
        if not file_path or not file_path.exists():
            raise ValueError("Skill 文件不存在")

        skill_info = skill_uploader._parse_skill_markdown(file_path)
        if not skill_info:
            raise ValueError("SKILL.md 文件格式错误，无法解析 name 和 description")

        skill.name = skill_info["name"]
        skill.description = skill_info["description"]
        await db.commit()
        await db.refresh(skill)
        return skill

    async def bulk_reload_from_file(self, db: AsyncSession, ids: List[int]) -> dict:
        """批量从磁盘文件重新加载，单个失败不中断"""
        success_count = 0
        failed_items: List[dict] = []
        skills: List[Skill] = []

        for skill_id in ids:
            try:
                skill = await self.reload_from_file(db, skill_id)
                success_count += 1
                skills.append(skill)
            except (ValueError, Exception) as e:
                failed_items.append({"id": skill_id, "reason": str(e)})

        return {
            "success_count": success_count,
            "failed_count": len(failed_items),
            "failed_items": failed_items,
            "skills": skills,
        }


skill_service = SkillService()
