"""
流程模板 API 路由

提供内置模板列表查询和从模板创建流程功能。
"""

from typing import Optional

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.schemas.base_schema import ApiResponse
from app.services.flow_template_service import get_templates, create_from_template


class CreateFromTemplateRequest(BaseModel):
    """从模板创建请求"""

    template_id: str = Field(..., description="模板标识")
    name: str = Field(..., description="流程名称")
    description: Optional[str] = Field(None, description="流程描述")


class FlowTemplateApi:
    """流程模板 API"""

    def __init__(self):
        self.router = APIRouter(prefix="/api/flow", tags=["流程模板"])
        self._register_routes()

    def _register_routes(self):
        @self.router.get(
            "/templates",
            summary="获取流程模板列表",
        )
        async def list_templates(
            flow_type: Optional[str] = None,
        ):
            """获取可用的流程/智能体模板列表"""
            templates = get_templates(flow_type)
            return ApiResponse.success(data=templates, msg="查询成功")

        @self.router.post(
            "/create-from-template",
            summary="从模板创建流程",
        )
        async def create_from_template_api(
            data: CreateFromTemplateRequest,
            db: AsyncSession = Depends(get_db),
        ):
            """从指定模板创建流程，返回流程ID"""
            try:
                flow_id = await create_from_template(
                    db, data.template_id, data.name, data.description
                )
                return ApiResponse.success(
                    data={"id": flow_id},
                    msg="创建成功",
                )
            except ValueError as e:
                return ApiResponse.error(msg=str(e))


flow_template_api = FlowTemplateApi()
router = flow_template_api.router
