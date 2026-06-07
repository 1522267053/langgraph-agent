"""
Agent Skill Schema

定义 Skill 的 Pydantic Schema，用于 API 请求和响应
"""

from typing import Optional, List
from pydantic import ConfigDict, Field, BaseModel

from app.schemas.base_schema import BaseView


class SkillBase(BaseView):
    """Skill 基础 Schema"""

    name: str = Field(..., description="Skill 名称")
    description: str = Field(..., description="描述（LLM 用于判断何时调用）")
    skill_path: Optional[str] = Field(default=None, description="SKILL.md 文件相对路径")
    category: Optional[str] = Field(default=None, description="分类")
    tags: Optional[str] = Field(default=None, description="标签")
    icon: Optional[str] = Field(default=None, description="图标名称")
    is_enabled: int = Field(default=1, description="是否启用")
    sort_order: int = Field(default=0, description="排序顺序")


class SkillCreate(BaseView):
    """创建 Skill Schema（保留用于 BaseService 泛型兼容）"""

    name: str = Field(..., description="Skill 名称")
    description: str = Field(..., description="描述")


class SkillUpdate(BaseView):
    """更新 Skill Schema"""

    id: int = Field(..., description="Skill ID")
    category: Optional[str] = Field(None, description="分类")
    tags: Optional[str] = Field(None, description="标签")
    icon: Optional[str] = Field(None, description="图标名称")
    is_enabled: Optional[int] = Field(None, description="是否启用")
    sort_order: Optional[int] = Field(None, description="排序顺序")

    model_config = ConfigDict(from_attributes=True)


class SkillQuery(BaseView):
    """查询条件 Schema"""

    name: Optional[str] = Field(None, description="名称（模糊搜索）")
    category: Optional[str] = Field(None, description="分类")
    is_enabled: Optional[int] = Field(None, description="是否启用")

    model_config = ConfigDict(from_attributes=True)


class SkillBatchUploadResult(BaseModel):
    """批量上传结果 Schema"""

    success_count: int = Field(..., description="成功数量")
    failed_count: int = Field(..., description="失败数量")
    failed_items: List[dict] = Field(default_factory=list, description="失败项列表")
    skills: List[SkillBase] = Field(
        default_factory=list, description="成功上传的 Skill 列表"
    )
