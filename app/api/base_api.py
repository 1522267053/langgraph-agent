"""
通用 API 路由基类

提供标准 CRUD 路由的基类，子类继承后自动获得完整的 CRUD 能力。
支持可选路由和批量操作。
"""

from typing import TypeVar, Generic, Optional, get_args, Sequence
from fastapi import APIRouter, Body, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from app.config.database import get_db
from app.services.base_service import BaseService
from app.schemas.base_schema import (
    BaseView,
    PaginatedResponse,
    ApiResponse,
    PaginationParams,
)

M = TypeVar("M")
V = TypeVar("V", bound=BaseView)
Q = TypeVar("Q", bound=BaseView)
C = TypeVar("C", bound=BaseView)
U = TypeVar("U", bound=BaseView)


class RouteConfig:
    """路由配置"""

    def __init__(
        self,
        enable_page: bool = True,
        enable_get: bool = True,
        enable_create: bool = True,
        enable_update: bool = True,
        enable_delete: bool = True,
        enable_batch_delete: bool = True,
        enable_batch_create: bool = False,
        enable_batch_update: bool = False,
    ):
        self.enable_page = enable_page
        self.enable_get = enable_get
        self.enable_create = enable_create
        self.enable_update = enable_update
        self.enable_delete = enable_delete
        self.enable_batch_delete = enable_batch_delete
        self.enable_batch_create = enable_batch_create
        self.enable_batch_update = enable_batch_update


class BaseApi(Generic[M, V, Q, C, U]):
    """
    通用 API 路由基类

    自动提供标准的 CRUD 路由：
    - POST /page - 分页查询（可选）
    - GET /get/{id} - 获取详情（可选）
    - POST /create - 创建（可选）
    - POST /update - 更新（可选）
    - GET /delete/{id} - 删除（可选）
    - POST /deleteBatch - 批量删除（可选）
    - POST /batch-create - 批量创建（可选）
    - POST /batch-update - 批量更新（可选）

    泛型参数:
        M: SQLAlchemy 模型类型
        V: 视图 Schema 类型（用于返回数据）
        Q: 查询条件 Schema 类型（用于分页查询）
        C: 创建 Schema 类型
        U: 更新 Schema 类型

    使用示例:
        ```python
        # 完整 CRUD
        class FlowApi(BaseApi[Flow, FlowBase, FlowBase, FlowCreate, FlowUpdate]):
            def __init__(self):
                super().__init__(
                    service=flow_service,
                    router_prefix="/api/flow",
                    router_tags=["流程管理"]
                )

        # 自定义 CRUD 逻辑
        class FlowNodeApi(BaseApi[FlowNode, FlowNodeBase, FlowNodeBase, FlowNodeCreate, FlowNodeUpdate]):
            def __init__(self):
                super().__init__(
                    service=flow_service,
                    router_prefix="/api/flow-node",
                    router_tags=["流程节点"],
                    route_config=RouteConfig(enable_page=False, enable_get=False)
                )

            async def create(self, db, data):
                # 自定义校验 + 创建逻辑
                if data.node_type == "human":
                    raise ValueError("不支持人类回答节点")
                return await flow_service.create_node(db, data)
        ```
    """

    model_class: type
    view_class: type
    query_class: type
    create_class: type
    update_class: type

    def __init_subclass__(cls, **kwargs):
        """子类初始化时自动提取泛型参数"""
        super().__init_subclass__(**kwargs)
        args = get_args(cls.__orig_bases__[0])  # type: ignore
        cls.model_class = args[0]
        cls.view_class = args[1]
        cls.query_class = args[2]
        cls.create_class = args[3]
        cls.update_class = args[4]

    def __init__(
        self,
        service: BaseService,
        router_prefix: str,
        router_tags: Sequence[str],
        route_config: Optional[RouteConfig] = None,
    ):
        self.service = service
        self.router = APIRouter(prefix=router_prefix, tags=list(router_tags))
        self.route_config = route_config or RouteConfig()
        self._register_routes()

    async def create(self, db: AsyncSession, data: C) -> M:
        """创建 - 子类可重写"""
        return await self.service.create(db, data)

    async def update(self, db: AsyncSession, data: U) -> Optional[M]:
        """更新 - 子类可重写"""
        return await self.service.update(db, data)

    async def delete(self, db: AsyncSession, id: int) -> None:
        """删除 - 子类可重写"""
        await self.service.delete(db, id)

    async def batch_create(self, db: AsyncSession, data_list: list[C]) -> None:
        """批量创建 - 子类可重写"""
        await self.service.bulk_create(db, data_list)

    async def batch_update(self, db: AsyncSession, data_list: list[U]) -> None:
        """批量更新 - 子类可重写"""
        for data in data_list:
            await self.service.update(db, data)

    async def batch_delete(self, db: AsyncSession, ids: list[int]) -> None:
        """批量删除 - 子类可重写"""
        await self.service.bulk_delete(db, ids)

    def _register_routes(self):
        """注册标准 CRUD 路由"""
        config = self.route_config

        if config.enable_page:
            self._register_page_route()

        if config.enable_get:
            self._register_get_route()

        if config.enable_create:
            self._register_create_route()

        if config.enable_update:
            self._register_update_route()

        if config.enable_delete:
            self._register_delete_route()

        if config.enable_batch_delete:
            self._register_batch_delete_route()

        if config.enable_batch_create:
            self._register_batch_create_route()

        if config.enable_batch_update:
            self._register_batch_update_route()

    def _register_page_route(self):
        """注册分页查询路由"""

        @self.router.post(
            "/page", response_model=ApiResponse[PaginatedResponse[self.view_class]]
        )
        async def page_query(
            query_params: PaginationParams[self.query_class],  # type: ignore
            db: AsyncSession = Depends(get_db),
        ):
            """分页查询"""
            items, total = await self.service.page_query(db, query_params)
            views = self.view_class.model_to_view_batch(items)
            paginated_data = PaginatedResponse.create(
                items=views,
                total=total,
                page=query_params.page,
                page_size=query_params.page_size,
            )
            return ApiResponse.success(data=paginated_data, msg="查询成功")

    def _register_get_route(self):
        """注册获取详情路由"""

        @self.router.get("/get/{id}", response_model=ApiResponse[self.view_class])
        async def get_by_id(id: int, db: AsyncSession = Depends(get_db)):
            """获取详情"""
            item = await self.service.get_by_id(db, id, raise_not_found=False)
            if item is None:
                return ApiResponse.error(msg="未找到数据")
            return ApiResponse.success(
                data=self.view_class.model_to_view(item), msg="查询成功"
            )

    def _register_create_route(self):
        """注册创建路由"""

        @self.router.post("/create", response_model=ApiResponse[self.view_class])
        async def create(data: self.create_class, db: AsyncSession = Depends(get_db)):  # type: ignore
            """创建"""
            new_item = await self.create(db, data)
            return ApiResponse.success(
                data=self.view_class.model_to_view(new_item), msg="创建成功"
            )

    def _register_update_route(self):
        """注册更新路由"""

        @self.router.post("/update", response_model=ApiResponse)
        async def update(data: self.update_class, db: AsyncSession = Depends(get_db)):  # type: ignore
            """更新"""
            await self.update(db, data)
            return ApiResponse.success(msg="更新成功")

    def _register_delete_route(self):
        """注册删除路由"""

        @self.router.get("/delete/{id}", response_model=ApiResponse)
        async def delete(id: int, db: AsyncSession = Depends(get_db)):
            """删除"""
            await self.delete(db, id)
            return ApiResponse.success(msg="删除成功")

    def _register_batch_delete_route(self):
        """注册批量删除路由"""

        @self.router.post("/deleteBatch", response_model=ApiResponse)
        async def delete_batch(
            ids: list[int] = Body(..., embed=True), db: AsyncSession = Depends(get_db)
        ):
            """批量删除"""
            await self.batch_delete(db, ids)
            return ApiResponse.success(msg="删除成功")

    def _register_batch_create_route(self):
        """注册批量创建路由"""

        @self.router.post("/batch-create", response_model=ApiResponse)
        async def batch_create(
            data_list: list[self.create_class],  # type: ignore
            db: AsyncSession = Depends(get_db),
        ):  # type: ignore
            """批量创建"""
            if not data_list:
                return ApiResponse.success(msg="无数据")

            await self.batch_create(db, data_list)
            return ApiResponse.success(msg="创建成功")

    def _register_batch_update_route(self):
        """注册批量更新路由"""

        @self.router.post("/batch-update", response_model=ApiResponse)
        async def batch_update(
            data_list: list[self.update_class],  # type: ignore
            db: AsyncSession = Depends(get_db),
        ):  # type: ignore
            """批量更新"""
            if not data_list:
                return ApiResponse.success(msg="无数据")

            await self.batch_update(db, data_list)
            return ApiResponse.success(msg="更新成功")
