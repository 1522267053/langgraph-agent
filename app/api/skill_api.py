"""
Agent Skill API 路由

处理 Skill 相关的路由定义
"""

import asyncio

from fastapi import Depends, File, UploadFile
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.config.settings import settings
from app.api.base_api import BaseApi
from app.agent_flow.exceptions import FlowValidationError
from app.models.skill import Skill
from app.services.skill_service import skill_service
from app.utils.skill_uploader import skill_uploader
from app.schemas.skill_schema import (
    SkillBase,
    SkillCreate,
    SkillUpdate,
    SkillQuery,
    SkillBatchUploadResult,
)
from app.schemas.base_schema import ApiResponse


class SkillApi(BaseApi[Skill, SkillBase, SkillQuery, SkillCreate, SkillUpdate]):
    """Skill API"""

    def __init__(self):
        super().__init__(
            service=skill_service, router_prefix="/api/skill", router_tags=["Skill管理"]
        )
        self._register_custom_routes()

    async def delete(self, db: AsyncSession, id: int) -> None:
        skill = await skill_service.get_by_id(db, id)
        if skill and skill.is_system == 1:
            raise FlowValidationError("系统预置 Skill 不允许删除")
        await skill_service.delete(db, id)

    async def batch_delete(self, db: AsyncSession, ids: list[int]) -> None:
        for sid in ids:
            skill = await skill_service.get_by_id(db, sid)
            if skill and skill.is_system == 1:
                raise FlowValidationError(f"系统预置 Skill「{skill.name}」不允许删除")
        await skill_service.bulk_delete(db, ids)

    def _register_custom_routes(self):
        """注册自定义路由"""

        @self.router.get(
            "/list",
            response_model=ApiResponse[list[SkillBase]],
            summary="获取所有启用的Skill",
        )
        async def get_enabled_skills(db: AsyncSession = Depends(get_db)):
            """获取所有启用的 Skill 列表"""
            skills = await skill_service.get_enabled_skills(db)
            views = SkillBase.model_to_view_batch(skills)
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.get(
            "/search", response_model=ApiResponse[list[SkillBase]], summary="搜索Skill"
        )
        async def search_skills(
            keyword: str, limit: int = 10, db: AsyncSession = Depends(get_db)
        ):
            """搜索 Skill"""
            skills = await skill_service.search_skills(db, keyword, limit)
            views = SkillBase.model_to_view_batch(skills)
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.get(
            "/category/{category}",
            response_model=ApiResponse[list[SkillBase]],
            summary="按分类获取Skill",
        )
        async def get_by_category(category: str, db: AsyncSession = Depends(get_db)):
            """按分类获取 Skill 列表"""
            skills = await skill_service.get_skills_by_category(db, category)
            views = SkillBase.model_to_view_batch(skills)
            return ApiResponse.success(data=views, msg="查询成功")

        @self.router.post(
            "/upload",
            response_model=ApiResponse[SkillBase],
            summary="上传Skill ZIP文件",
        )
        async def upload_skill_zip(
            file: UploadFile = File(...), db: AsyncSession = Depends(get_db)
        ):
            """上传 Skill ZIP 文件"""
            result = await skill_uploader.handle_upload_zip(file, db)
            return ApiResponse.success(
                data=SkillBase.model_validate(result), msg="上传成功"
            )

        @self.router.post(
            "/upload_batch",
            response_model=ApiResponse[SkillBatchUploadResult],
            summary="批量上传Skill ZIP文件",
        )
        async def upload_skill_zip_batch(
            files: list[UploadFile] = File(...),
            db: AsyncSession = Depends(get_db),
        ):
            """批量上传 Skill ZIP 文件，单个失败不影响其他"""
            result = await skill_uploader.handle_batch_upload(files, db)
            skills_view = [SkillBase.model_validate(s) for s in result["skills"]]
            batch_result = SkillBatchUploadResult(
                success_count=result["success_count"],
                failed_count=result["failed_count"],
                failed_items=result["failed_items"],
                skills=skills_view,
            )
            return ApiResponse.success(data=batch_result, msg="批量上传完成")

        @self.router.get(
            "/{skill_id}/content",
            response_model=ApiResponse[str],
            summary="获取Skill的SKILL.md文件内容",
        )
        async def get_skill_content(skill_id: int, db: AsyncSession = Depends(get_db)):
            """获取 Skill 对应的 SKILL.md 文件内容"""
            skill = await skill_service.get_by_id(db, skill_id)
            if not skill or not skill.skill_path:
                return ApiResponse.error(msg="Skill 文件路径不存在")

            file_path = settings.get_absolute_path(skill.skill_path)
            if not file_path or not file_path.exists():
                return ApiResponse.error(msg="Skill 文件不存在")

            content = await asyncio.to_thread(file_path.read_text, encoding="utf-8")
            return ApiResponse.success(data=content, msg="查询成功")

        @self.router.post(
            "/{skill_id}/reload",
            response_model=ApiResponse[SkillBase],
            summary="重新加载Skill文件信息",
        )
        async def reload_skill(skill_id: int, db: AsyncSession = Depends(get_db)):
            """从磁盘 SKILL.md 文件重新加载 name 和 description"""
            try:
                skill = await skill_service.reload_from_file(db, skill_id)
                return ApiResponse.success(
                    data=SkillBase.model_validate(skill), msg="重新加载成功"
                )
            except ValueError as e:
                raise FlowValidationError(str(e))

        @self.router.post(
            "/reloadBatch",
            response_model=ApiResponse[SkillBatchUploadResult],
            summary="批量重新加载Skill文件信息",
        )
        async def batch_reload_skills(
            ids: list[int], db: AsyncSession = Depends(get_db)
        ):
            """批量从磁盘文件重新加载，单个失败不中断"""
            result = await skill_service.bulk_reload_from_file(db, ids)
            batch_result = SkillBatchUploadResult(
                success_count=result["success_count"],
                failed_count=result["failed_count"],
                failed_items=result["failed_items"],
                skills=[SkillBase.model_validate(s) for s in result["skills"]],
            )
            return ApiResponse.success(data=batch_result, msg="批量重新加载完成")


skill_api = SkillApi()
router = skill_api.router
