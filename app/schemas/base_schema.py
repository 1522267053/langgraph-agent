"""
通用 Schema 基类
提供标准的 Pydantic 模式定义
"""

from datetime import datetime
from typing import Optional, Generic, TypeVar, Sequence
from pydantic import BaseModel, ConfigDict, Field, PlainSerializer
from typing_extensions import Self, Annotated

from app.models.base_model import DbBaseModel


# 定义泛型类型变量
T = TypeVar("T", covariant=True)

M = TypeVar("M", bound=DbBaseModel)


def _format_datetime(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S") if dt else ""


ChinaDateTime = Annotated[datetime, PlainSerializer(_format_datetime, return_type=str)]


class BaseView(BaseModel):
    """
    通用模式定义
    """

    id: Optional[int] = Field(None, description="ID")
    creator_id: Optional[int] = Field(None, description="创建人ID")
    creator_type: Optional[int] = Field(
        None, description="创建人类型：1=管理员，2=用户"
    )
    creator_name: Optional[str] = Field(None, description="创建人名称")
    create_time: Optional[ChinaDateTime] = Field(None, description="创建时间")
    modifier_id: Optional[int] = Field(None, description="修改人ID")
    modifier_type: Optional[int] = Field(
        None, description="修改人类型：1=管理员，2=用户"
    )
    modifier_name: Optional[str] = Field(None, description="修改人名称")
    modify_time: Optional[ChinaDateTime] = Field(None, description="修改时间")
    model_config = ConfigDict(from_attributes=True, validate_assignment=True)

    def to_model(self, model_class: type[M]) -> M:
        """
        将当前对象转换为模型对象

        Args:
            model_class: 目标模型类（如 Policy）

        Returns:
            转换后的模型对象实例

        Example:
            >>> policy_schema = PolicyCreate(title="测试", content="内容")
            >>> policy_model = policy_schema.to_model(Policy)
        """
        # 使用 model_dump 获取字典数据
        data = self.model_dump()

        # 获取模型类的所有字段名
        model_fields = set(model_class.__mapper__.attrs.keys())

        # 过滤掉不在模型中的字段
        # JSON 列传入 None 时 SQLite 会存为 "null" 字符串，需要跳过
        from sqlalchemy import JSON as SA_JSON

        json_columns = {
            attr.key
            for attr in model_class.__mapper__.attrs
            if hasattr(attr, "columns")
            and any(isinstance(col.type, SA_JSON) for col in attr.columns)
        }

        filtered_data = {}
        for k, v in data.items():
            if k not in model_fields:
                continue
            if v is None and k in json_columns:
                continue
            filtered_data[k] = v

        # 使用过滤后的字典数据创建模型实例
        return model_class(**filtered_data)

    @classmethod
    def model_to_view(cls, model_instance: DbBaseModel) -> Self:
        """
        将数据库模型对象转换为 View Schema 对象

        Args:
            model_instance: 数据库模型实例（如 Policy 实例）

        Returns:
            转换后的 View Schema 对象

        Example:
            >>> policy_model = session.query(Policy).first()
            >>> policy_view = PolicyBase.model_to_view(policy_model)
        """
        # 获取 Schema 中定义的字段名
        schema_fields = cls.model_fields.keys()

        # 获取模型实例的所有属性（包括关联属性）
        model_attrs = set(model_instance.__mapper__.attrs.keys())

        # 构建数据字典，只包含 Schema 中定义且模型中存在的字段
        data = {}
        for field_name in schema_fields:
            if field_name in model_attrs and hasattr(model_instance, field_name):
                data[field_name] = getattr(model_instance, field_name)

        # 使用字典数据创建 View Schema 实例
        return cls.model_validate(data)

    @classmethod
    def model_to_view_batch(cls, model_instances: Sequence[DbBaseModel]) -> list[Self]:
        """
        批量将数据库模型对象转换为 View Schema 对象

        Args:
            model_instances: 数据库模型实例列表（如 Policy 实例列表）

        Returns:
            转换后的 View Schema 对象列表

        Example:
            >>> policy_models = session.query(Policy).all()
            >>> policy_views = PolicyBase.model_to_view_batch(policy_models)
        """
        return [cls.model_to_view(model_instance) for model_instance in model_instances]


class PaginationParams(BaseModel, Generic[T]):
    """
    分页参数
    """

    page: int = Field(default=1, ge=1, description="页码,从1开始")
    page_size: int = Field(default=10, ge=0, le=100, description="每页数量,最大100")
    order_by: Optional[str] = Field(default="", description="排序字段")
    is_asc: bool = Field(default=True, description="是否升序: true为升序, false为降序")
    condition: Optional[T] = Field(default=None, description="查询条件")

    @property
    def skip(self) -> int:
        """计算跳过的记录数"""
        return (self.page - 1) * self.page_size

    @property
    def limit(self) -> int:
        """获取每页数量"""
        return self.page_size

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "page": 1,
                "page_size": 10,
                "order_by": "create_time",
                "is_asc": False,
                "condition": {},
            }
        }
    )


class PaginatedResponse(BaseModel, Generic[T]):
    """
    分页响应模板(泛型)
    """

    total: int = Field(..., description="总记录数")
    page: int = Field(..., description="当前页码")
    page_size: int = Field(..., description="每页数量")
    total_pages: int = Field(..., description="总页数")
    items: list[T] = Field(default_factory=list, description="数据列表")

    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "total": 100,
                "page": 1,
                "page_size": 10,
                "total_pages": 10,
                "items": [],
            }
        }
    )

    @classmethod
    def create(cls, items: list[T], total: int, page: int, page_size: int):
        """创建分页响应"""
        total_pages = (total + page_size - 1) // page_size if page_size > 0 else 0
        return cls(
            total=total,
            page=page,
            page_size=page_size,
            total_pages=total_pages,
            items=items,
        )


class ApiResponse(BaseModel, Generic[T]):
    """
    通用API响应体(泛型)
    格式: {"code": 1, "data": {}, "msg": ""}
    """

    code: int = Field(default=1, description="响应状态码, 1表示成功")
    msg: str = Field(default="success", description="响应消息")
    data: Optional[T] = Field(default=None, description="响应数据")

    model_config = ConfigDict(
        json_schema_extra={"example": {"code": 1, "msg": "success", "data": None}}
    )

    @classmethod
    def success(cls, data: Optional[T] = None, msg: str = "操作成功"):
        """成功响应"""
        return cls(code=1, msg=msg, data=data)

    @classmethod
    def error(cls, msg: str = "操作失败", code: int = 0):
        """错误响应"""
        return cls(code=code, msg=msg, data=None)
