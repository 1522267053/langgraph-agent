"""
关于我们相关数据模型
"""

from typing import Optional
from fastapi.exceptions import RequestValidationError
from pydantic import Field, field_validator
from app.schemas.base_schema import BaseView


class ExampleBase(BaseView):
    """样例基础模型"""

    title: Optional[str] = Field(..., description="标题")

    @field_validator("title")
    @classmethod
    def validate_title(cls, v: Optional[str]) -> Optional[str]:
        """校验参数方式(必须按照这个来校验参数)"""
        if v is None:
            return v
        if len(v) > 20:
            raise RequestValidationError("标题不能超过20个字符")
        return v


class ExampleCreate(ExampleBase):
    """创建样例"""

    pass


class ExampleUpdate(ExampleBase):
    """更新样例"""

    pass
