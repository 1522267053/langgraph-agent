"""
通用 CRUD Service 基类模块

本模块提供了一个通用的 CRUD（增删改查）服务基类，为所有业务服务提供标准化的数据操作能力。
通过继承此基类，子类可以自动获得完整的 CRUD 功能，同时支持自定义扩展。

主要特性:
    1. 泛型支持: 使用 Python 泛型实现类型安全，支持任意模型和 Schema 类型
    2. 软删除: 内置软删除支持（is_delete 字段），查询时自动过滤已删除记录
    3. 分页查询: 提供完整的分页查询支持，包含排序和条件过滤
    4. 批量操作: 支持批量创建和批量删除操作
    5. 可扩展性: 子类可通过重写 _apply_filters 等方法实现自定义查询逻辑

使用方式:
    1. 定义模型类（继承自 DbBaseModel）
    2. 定义创建和更新的 Pydantic Schema（继承自 BaseView）
    3. 创建服务类继承 BaseService[ModelType, CreateSchemaType, UpdateSchemaType]
    4. 在构造函数中调用 super().__init__(ModelClass)

自动继承的方法:
    - get_list(): 获取列表，支持过滤和排序
    - get_by_id(): 根据ID获取单个对象
    - get_one(): 根据条件获取单个对象
    - create(): 创建新记录
    - update(): 更新记录
    - delete(): 逻辑删除记录
    - count(): 统计记录数量
    - exists(): 检查记录是否存在
    - bulk_create(): 批量创建
    - bulk_delete(): 批量删除
    - page_query(): 分页查询

可重写的钩子方法:
    - _apply_filters(): 自定义过滤逻辑
    - _build_base_query(): 自定义基础查询
    - _apply_ordering(): 自定义排序逻辑
    - _set_creator_fields(): 设置创建人信息
    - _set_modifier_fields(): 设置修改人信息
"""

from typing import Generic, TypeVar, Type, List, Optional, Any, Tuple, cast
from datetime import datetime
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import inspect, select, func, Select
from fastapi import HTTPException, status

from app.models.base_model import DbBaseModel
from app.schemas.base_schema import BaseView, PaginationParams

# 定义泛型类型变量
# ModelType: SQLAlchemy 模型类型，必须继承自 DbBaseModel
# CreateSchemaType: 创建操作的 Pydantic Schema 类型
# UpdateSchemaType: 更新操作的 Pydantic Schema 类型
ModelType = TypeVar("ModelType", bound=DbBaseModel)
CreateSchemaType = TypeVar("CreateSchemaType", bound=BaseView)
UpdateSchemaType = TypeVar("UpdateSchemaType", bound=BaseView)


class BaseService(Generic[ModelType, CreateSchemaType, UpdateSchemaType]):
    """
    通用 CRUD Service 基类

    提供标准化的数据库 CRUD 操作，支持软删除、分页查询、批量操作等功能。
    使用 Python 泛型确保类型安全，子类继承后自动获得完整的 CRUD 能力。

    泛型参数:
        ModelType: SQLAlchemy 模型类型，必须继承自 DbBaseModel
        CreateSchemaType: 创建操作的 Pydantic Schema 类型，必须继承自 BaseView
        UpdateSchemaType: 更新操作的 Pydantic Schema 类型，必须继承自 BaseView

    属性:
        _model: 内部存储的模型类引用
        model: 只读属性，获取模型类

    核心方法:
        查询方法:
            - get_list: 获取列表，支持过滤和排序
            - get_by_id: 根据ID获取单个对象
            - get_one: 根据条件获取单个对象
            - count: 统计记录数量
            - exists: 检查记录是否存在
            - page_query: 分页查询

        写入方法:
            - create: 创建新记录
            - update: 更新记录（支持 Schema 或 ORM 对象）
            - delete: 逻辑删除记录
            - bulk_create: 批量创建
            - bulk_delete: 批量删除

        内部方法（可重写）:
            - _build_base_query: 构建基础查询对象
            - _build_count_query: 构建计数查询对象
            - _apply_filters: 应用查询条件
            - _apply_ordering: 应用排序逻辑
            - _execute_query: 执行查询
            - _execute_scalar_query: 执行标量查询
            - _set_creator_fields: 设置创建人信息
            - _set_modifier_fields: 设置修改人信息

    使用示例:
        ```python
        # 定义服务类
        class PolicyService(BaseService[Policy, PolicyCreate, PolicyUpdate]):
            def __init__(self):
                super().__init__(Policy)

            # 重写过滤方法实现自定义查询逻辑
            def _apply_filters(self, query, count_query, condition):
                query, count_query = super()._apply_filters(query, count_query, condition)
                # 自定义模糊搜索
                if condition and hasattr(condition, 'title') and condition.title:
                    query = query.where(Policy.title.like(f"%{condition.title}%"))
                    if count_query:
                        count_query = count_query.where(Policy.title.like(f"%{condition.title}%"))
                return query, count_query

        # 使用服务
        service = PolicyService()
        policies = await service.get_list(db, filters=condition)
        policy = await service.create(db, obj_in=policy_create)
        ```
    """

    def __init__(self, model: Type[ModelType]):
        """
        初始化 Service

        子类必须在构造函数中调用此方法，传入要操作的模型类。

        Args:
            model: SQLAlchemy 模型类，必须继承自 DbBaseModel

        Example:
            ```python
            class UserService(BaseService[User, UserCreate, UserUpdate]):
                def __init__(self):
                    super().__init__(User)
            ```
        """
        self._model = model

    @property
    def model(self) -> Type[ModelType]:
        """
        获取模型类（只读属性）

        Returns:
            Type[ModelType]: 当前服务绑定的 SQLAlchemy 模型类
        """
        return self._model

    def _build_base_query(self) -> Select:
        """
        构建基础查询对象（内部方法）

        创建一个基础的 SELECT 查询对象。子类可以重写此方法来添加默认的
        JOIN、WHERE 条件或其他查询逻辑。

        默认实现返回简单的 SELECT * FROM table 查询。

        Returns:
            Select: SQLAlchemy Select 查询对象

        Example:
            ```python
            def _build_base_query(self) -> Select:
                # 添加默认的 JOIN
                return select(self.model).options(
                    selectinload(self.model.tags)
                )
            ```
        """
        return select(self.model)

    def _build_count_query(self) -> Select:
        """
        构建计数查询对象（内部方法）

        创建一个用于统计记录数的查询对象。子类可以重写此方法来添加
        默认的 JOIN 或其他逻辑，确保计数查询与数据查询的过滤条件一致。

        Returns:
            Select: SQLAlchemy SELECT COUNT(*) 查询对象

        Example:
            ```python
            def _build_count_query(self) -> Select:
                # 如果基础查询有 JOIN，计数查询也需要相同的 JOIN
                return select(func.count()).select_from(self.model).join(
                    self.model.tags
                )
            ```
        """
        return select(func.count()).select_from(self.model)

    def _set_creator_fields(self, db_obj: DbBaseModel) -> None:
        """
        设置创建时间和创建人信息（内部方法）

        在创建新记录时自动调用，设置 create_time 等字段。
        子类可以重写此方法来添加更多创建人相关的字段设置。

        Args:
            db_obj: 数据库模型对象，将被修改

        Example:
            ```python
            def _set_creator_fields(self, db_obj: DbBaseModel) -> None:
                super()._set_creator_fields(db_obj)
                # 添加创建人ID（假设从上下文获取）
                if hasattr(db_obj, 'created_by'):
                    setattr(db_obj, 'created_by', get_current_user_id())
            ```
        """
        if hasattr(db_obj, "create_time"):
            setattr(db_obj, "create_time", datetime.now())

    def _set_modifier_fields(self, db_obj: DbBaseModel) -> None:
        """
        设置修改时间和修改人信息（内部方法）

        在更新或删除记录时自动调用，设置 modify_time 等字段。
        子类可以重写此方法来添加更多修改人相关的字段设置。

        Args:
            db_obj: 数据库模型对象，将被修改

        Example:
            ```python
            def _set_modifier_fields(self, db_obj: DbBaseModel) -> None:
                super()._set_modifier_fields(db_obj)
                # 添加修改人ID（假设从上下文获取）
                if hasattr(db_obj, 'modified_by'):
                    setattr(db_obj, 'modified_by', get_current_user_id())
            ```
        """
        if hasattr(db_obj, "modify_time"):
            setattr(db_obj, "modify_time", datetime.now())

    async def _execute_query(
        self, db: AsyncSession, query: Select, include_deleted: bool = False
    ) -> List[ModelType]:
        """
        执行查询并返回结果列表（内部方法）

        执行给定的 SQLAlchemy 查询对象，返回模型对象列表。
        通过 execution_options 控制是否包含已删除记录。

        Args:
            db: 数据库异步会话
            query: SQLAlchemy Select 查询对象
            include_deleted: 是否包含已删除的记录（is_delete=1 的记录）
                           默认为 False，即不包含

        Returns:
            List[ModelType]: 查询结果列表，可能为空列表

        Note:
            include_deleted 参数通过 execution_options 传递，
            需要配合数据库模型的事件监听器使用，自动过滤 is_delete=1 的记录
        """
        result = await db.execute(
            query, execution_options={"include_deleted": include_deleted}
        )
        return list(result.scalars().all())

    async def _execute_scalar_query(
        self, db: AsyncSession, query: Select, include_deleted: bool = False
    ) -> Any:
        """
        执行标量查询（内部方法）

        执行返回单个值的查询（如 COUNT、MAX、MIN 等聚合函数）。

        Args:
            db: 数据库异步会话
            query: SQLAlchemy Select 查询对象，预期返回单个值
            include_deleted: 是否包含已删除的记录，默认 False

        Returns:
            Any: 查询返回的标量值

        Example:
            ```python
            count = await self._execute_scalar_query(db, count_query)
            max_id = await self._execute_scalar_query(db, select(func.max(Model.id)))
            ```
        """
        result = await db.execute(
            query, execution_options={"include_deleted": include_deleted}
        )
        return result.scalar_one()

    async def get_list(
        self,
        db: AsyncSession,
        filters: Optional[DbBaseModel] = None,
        order_by: Optional[str] = None,
        include_deleted: bool = False,
    ) -> List[ModelType]:
        """
        获取列表

        根据过滤条件查询记录列表，支持排序。

        Args:
            db: 数据库异步会话
            filters: 过滤条件对象，字段值非 None/空字符串时作为等值条件
                   默认实现仅支持等值匹配，可通过重写 _apply_filters 支持更多
            order_by: 排序字段，支持升序和降序
                    - 升序: "field_name"
                    - 降序: "-field_name"（字段名前加减号）
            include_deleted: 是否包含已删除的记录，默认 False

        Returns:
            List[ModelType]: 模型对象列表，无匹配时返回空列表

        Example:
            ```python
            # 获取所有记录，按创建时间降序
            items = await service.get_list(db, order_by="-create_time")

            # 获取特定条件的记录
            filters = Model(name="test", status=1)
            items = await service.get_list(db, filters=filters, order_by="name")

            # 包含已删除的记录
            all_items = await service.get_list(db, include_deleted=True)
            ```
        """
        query = self._build_base_query()

        query_result, _ = self._apply_filters(query, None, filters)
        if query_result is not None:
            query = query_result

        query = self._apply_ordering(query, order_by)

        return await self._execute_query(db, query, include_deleted)

    def _apply_ordering(self, query: Select, order_by: Optional[str] = None) -> Select:
        """
        应用排序逻辑（内部方法）

        根据排序字段字符串应用 ORDER BY 子句。支持升序和降序排序。
        子类可以重写此方法实现更复杂的排序逻辑（如多字段排序）。

        Args:
            query: 查询对象
            order_by: 排序字段字符串
                    - 升序: "field_name"
                    - 降序: "-field_name"
                    - None: 不排序

        Returns:
            Select: 应用排序后的查询对象

        Example:
            ```python
            # 重写支持多字段排序
            def _apply_ordering(self, query, order_by):
                if order_by == "priority_date":
                    return query.order_by(
                        self.model.priority.desc(),
                        self.model.create_time.desc()
                    )
                return super()._apply_ordering(query, order_by)
            ```
        """
        if order_by:
            if order_by.startswith("-"):
                field = order_by[1:]
                if hasattr(self.model, field):
                    query = query.order_by(getattr(self.model, field).desc())
            else:
                if hasattr(self.model, order_by):
                    query = query.order_by(getattr(self.model, order_by))
        return query

    async def get_by_id(
        self,
        db: AsyncSession,
        id: int,
        raise_not_found: bool = True,
        include_deleted: bool = False,
    ) -> Optional[ModelType]:
        """
        根据ID获取单个对象

        通过主键 ID 查询单条记录。

        Args:
            db: 数据库异步会话
            id: 记录的主键ID
            raise_not_found: 记录不存在时是否抛出 HTTPException
                           - True: 抛出 404 异常（默认）
                           - False: 返回 None
            include_deleted: 是否包含已删除的记录，默认 False

        Returns:
            Optional[ModelType]: 找到时返回模型对象，否则返回 None（raise_not_found=False）

        Raises:
            HTTPException: 当 raise_not_found=True 且记录不存在时，返回 404 错误

        Example:
            ```python
            # 获取记录，不存在时抛出 404
            item = await service.get_by_id(db, id=1)

            # 获取记录，不存在时返回 None
            item = await service.get_by_id(db, id=1, raise_not_found=False)
            if item:
                print(f"找到: {item.name}")
            ```
        """
        query = self._build_base_query().where(self.model.id == id)

        result = await db.execute(
            query, execution_options={"include_deleted": include_deleted}
        )
        obj = result.scalar_one_or_none()

        if not obj and raise_not_found:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"{self.model.__name__} ID {id} 不存在",
            )

        return obj

    async def get_one(
        self,
        db: AsyncSession,
        filters: Optional[DbBaseModel] = None,
        include_deleted: bool = False,
    ) -> Optional[ModelType]:
        """
        根据条件获取单个对象

        根据过滤条件查询第一条匹配的记录。

        Args:
            db: 数据库异步会话
            filters: 过滤条件对象，非 None 字段作为等值条件
            include_deleted: 是否包含已删除的记录，默认 False

        Returns:
            Optional[ModelType]: 找到时返回模型对象，否则返回 None

        Example:
            ```python
            # 根据名称查找
            user = await service.get_one(db, filters=User(username="admin"))
            ```
        """
        query = self._build_base_query()
        query_result, _ = self._apply_filters(query, None, filters)
        if query_result is not None:
            query = query_result

        result = await db.execute(
            query, execution_options={"include_deleted": include_deleted}
        )
        return result.scalar_one_or_none()

    async def create(self, db: AsyncSession, obj_in: CreateSchemaType) -> ModelType:
        """
        创建新记录

        根据传入的 Schema 对象创建新的数据库记录。
        自动设置 create_time 和 is_delete 字段。

        Args:
            db: 数据库异步会话
            obj_in: 创建数据 Schema 对象，包含要创建的字段值

        Returns:
            ModelType: 创建成功后的模型对象（包含数据库生成的字段如 ID）

        Example:
            ```python
            user_create = UserCreate(username="test", email="test@example.com")
            user = await service.create(db, obj_in=user_create)
            print(f"创建成功，ID: {user.id}")
            ```
        """
        db_obj = obj_in.to_model(self.model)

        self._set_creator_fields(db_obj)

        if hasattr(db_obj, "is_delete"):
            setattr(db_obj, "is_delete", 0)

        db.add(db_obj)
        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def delete(self, db: AsyncSession, id: int) -> None:
        """
        逻辑删除记录

        将记录的 is_delete 字段设置为 1，实现软删除。
        如果模型没有 is_delete 字段，则执行物理删除。

        Args:
            db: 数据库异步会话
            id: 记录ID

        Raises:
            HTTPException: 记录不存在时抛出 404 错误

        Note:
            - 软删除后，记录仍存在于数据库，但会被查询过滤器自动排除
            - 软删除会更新 modify_time 字段

        Example:
            ```python
            await service.delete(db, id=1)  # 软删除 ID 为 1 的记录
            ```
        """
        db_obj = cast(DbBaseModel, await self.get_by_id(db, id))

        if hasattr(db_obj, "is_delete"):
            setattr(db_obj, "is_delete", 1)

            self._set_modifier_fields(db_obj)

            await db.commit()
        else:
            await db.delete(db_obj)
            await db.commit()

    async def count(
        self,
        db: AsyncSession,
        filters: Optional[DbBaseModel] = None,
        include_deleted: bool = False,
    ) -> int:
        """
        统计记录数量

        根据过滤条件统计符合条件的记录总数。

        Args:
            db: 数据库异步会话
            filters: 过滤条件对象，非 None 字段作为等值条件
            include_deleted: 是否包含已删除的记录，默认 False

        Returns:
            int: 符合条件的记录数量

        Example:
            ```python
            # 统计所有记录
            total = await service.count(db)

            # 统计特定条件的记录
            active_count = await service.count(db, filters=User(status=1))
            ```
        """
        count_query = self._build_count_query()

        _, count_result = self._apply_filters(None, count_query, filters)
        if count_result is not None:
            count_query = count_result

        return await self._execute_scalar_query(db, count_query, include_deleted)

    async def exists(
        self, db: AsyncSession, id: int, include_deleted: bool = False
    ) -> bool:
        """
        检查记录是否存在

        快速检查指定 ID 的记录是否存在。

        Args:
            db: 数据库异步会话
            id: 记录ID
            include_deleted: 是否包含已删除的记录，默认 False

        Returns:
            bool: 记录存在返回 True，否则返回 False

        Example:
            ```python
            if await service.exists(db, id=1):
                print("记录存在")
            ```
        """
        obj = await self.get_by_id(
            db, id, raise_not_found=False, include_deleted=include_deleted
        )
        return obj is not None

    async def bulk_create(
        self, db: AsyncSession, objs_in: List[CreateSchemaType]
    ) -> List[ModelType]:
        """
        批量创建记录

        一次性创建多条记录，比循环调用 create() 更高效。
        在单个事务中完成所有插入操作。

        Args:
            db: 数据库异步会话
            objs_in: 创建数据 Schema 对象列表

        Returns:
            List[ModelType]: 创建成功的模型对象列表（包含数据库生成的字段）

        Example:
            ```python
            users_data = [
                UserCreate(username="user1"),
                UserCreate(username="user2"),
                UserCreate(username="user3"),
            ]
            users = await service.bulk_create(db, objs_in=users_data)
            ```
        """
        db_objs = []
        for obj_in in objs_in:
            db_obj = obj_in.to_model(self.model)

            self._set_creator_fields(db_obj)

            if hasattr(db_obj, "is_delete"):
                setattr(db_obj, "is_delete", 0)

            db_objs.append(db_obj)

        db.add_all(db_objs)
        await db.commit()

        for db_obj in db_objs:
            await db.refresh(db_obj)

        return db_objs

    async def bulk_delete(self, db: AsyncSession, ids: List[int]) -> int:
        """
        批量逻辑删除记录

        将多条记录的 is_delete 字段设置为 1。
        如果模型没有 is_delete 字段，则执行物理删除。

        Args:
            db: 数据库异步会话
            ids: 要删除的记录ID列表

        Returns:
            int: 实际删除的记录数量（仅计算存在的记录）

        Example:
            ```python
            deleted_count = await service.bulk_delete(db, ids=[1, 2, 3])
            print(f"删除了 {deleted_count} 条记录")
            ```
        """
        query = select(self.model).where(getattr(self.model, "id").in_(ids))

        result = await db.execute(query)
        objs = result.scalars().all()

        if hasattr(self.model, "is_delete"):
            for obj in objs:
                setattr(obj, "is_delete", 1)

                self._set_modifier_fields(obj)
        else:
            for obj in objs:
                await db.delete(obj)

        await db.commit()
        return len(objs)

    async def update(
        self, db: AsyncSession, obj_in: UpdateSchemaType | ModelType
    ) -> ModelType:
        """
        更新记录

        根据请求体或 ORM 对象中的 ID 更新记录。
        支持两种输入方式：Pydantic Schema 或已存在的 ORM 对象。

        Args:
            db: 数据库异步会话
            obj_in: 更新数据，可以是：
                   - UpdateSchemaType: Pydantic Schema，必须包含 id 字段
                   - ModelType: ORM 模型对象，必须包含有效的 id 属性

        Returns:
            ModelType: 更新后的模型对象

        Raises:
            HTTPException:
                - 400: 请求体或模型对象缺少有效的 id 字段
                - 404: 指定 ID 的记录不存在

        Example:
            ```python
            # 方式1: 使用 Schema 更新
            user_update = UserUpdate(id=1, username="new_name")
            user = await service.update(db, obj_in=user_update)

            # 方式2: 使用 ORM 对象更新
            user = await service.get_by_id(db, id=1)
            user.username = "new_name"
            user = await service.update(db, obj_in=user)
            ```
        """
        db_obj: ModelType

        if isinstance(obj_in, self.model):
            db_obj = obj_in
            if not hasattr(db_obj, "id") or db_obj.id is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="模型对象必须包含有效的id字段",
                )
        else:
            schema_obj = cast(UpdateSchemaType, obj_in)
            obj_data = schema_obj.model_dump()
            if "id" not in obj_data or obj_data["id"] is None:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="请求体中必须包含有效的id字段",
                )

            obj_id = obj_data["id"]

            update_data = schema_obj.model_dump(exclude={"id"}, exclude_unset=True)

            db_obj_result = await self.get_by_id(db, obj_id, raise_not_found=True)
            db_obj = cast(ModelType, db_obj_result)
            for field, value in update_data.items():
                setattr(db_obj, field, value)

        self._set_modifier_fields(cast(DbBaseModel, db_obj))

        await db.commit()
        await db.refresh(db_obj)
        return db_obj

    async def page_query(
        self, db: AsyncSession, query_params: PaginationParams[BaseView]
    ) -> Tuple[List[ModelType], int]:
        """
        分页查询

        执行分页查询，返回记录列表和总数。
        支持条件过滤、排序和分页。

        Args:
            db: 数据库异步会话
            query_params: 分页查询参数对象，包含：
                - page: 当前页码（从1开始）
                - page_size: 每页大小（0 表示不分页，返回所有数据）
                - condition: 查询条件 Schema
                - order_by: 排序字段名
                - is_asc: 是否升序排序

        Returns:
            Tuple[List[ModelType], int]: (记录列表, 总数)

        Example:
            ```python
            # 分页查询
            params = PaginationParams(
                page=1,
                page_size=10,
                condition=UserQuery(status=1),
                order_by="create_time",
                is_asc=False
            )
            items, total = await service.page_query(db, query_params=params)
            print(f"共 {total} 条，当前页 {len(items)} 条")

            # 不分页，返回所有数据
            params = PaginationParams(page=1, page_size=0)
            items, total = await service.page_query(db, query_params=params)
            ```
        """
        query = self._build_base_query()
        count_query = self._build_count_query()

        condition = (
            query_params.condition.to_model(self.model)
            if query_params.condition
            else None
        )

        query_result, count_result = self._apply_filters(query, count_query, condition)
        if query_result is not None:
            query = query_result
        if count_result is not None:
            count_query = count_result

        if query_params.order_by and hasattr(self.model, query_params.order_by):
            order_field = getattr(self.model, query_params.order_by)
            if query_params.is_asc:
                query = query.order_by(order_field.asc())
            else:
                query = query.order_by(order_field.desc())
        else:
            if hasattr(self.model, "create_time"):
                query = query.order_by(self.model.create_time.desc())

        total = 0
        if query_params.page_size != 0:
            total = await self._execute_scalar_query(db, count_query)
            if total == 0:
                return [], 0
            query = query.offset(query_params.skip).limit(query_params.limit)

        items = await self._execute_query(db, query)

        if query_params.page_size == 0:
            total = len(items)

        return items, total

    def _apply_like_filter(
        self,
        query: Optional[Select],
        count_query: Optional[Select],
        field_name: str,
        value: str,
    ) -> Tuple[Optional[Select], Optional[Select]]:
        """
        应用模糊搜索过滤条件（工具方法）

        Args:
            query: 数据查询对象
            count_query: 计数查询对象
            field_name: 字段名
            value: 搜索值

        Returns:
            Tuple[Optional[Select], Optional[Select]]: 处理后的查询对象
        """
        if not value:
            return query, count_query

        if not hasattr(self.model, field_name):
            return query, count_query

        pattern = f"%{value}%"
        field = getattr(self.model, field_name)

        if query is not None:
            query = query.where(field.like(pattern))
        if count_query is not None:
            count_query = count_query.where(field.like(pattern))

        return query, count_query

    def _apply_filters(
        self,
        query: Optional[Select],
        count_query: Optional[Select],
        condition: Optional[DbBaseModel],
    ) -> Tuple[Optional[Select], Optional[Select]]:
        """
        应用查询条件（内部方法）

        将条件对象的非空字段作为等值条件应用到查询中。
        子类应重写此方法来实现自定义的过滤逻辑（如模糊搜索、范围查询等）。

        默认行为:
            - 遍历 condition 对象的所有列
            - 跳过值为 None 或空字符串的字段
            - 对其他字段应用等值条件（field == value）

        Args:
            query: 数据查询对象，可以为 None
            count_query: 计数查询对象，可以为 None
            condition: 查询条件对象，字段对应模型的列

        Returns:
            Tuple[Optional[Select], Optional[Select]]: (处理后的数据查询, 处理后的计数查询)

        Example:
            ```python
            # 重写以支持更多过滤类型
            def _apply_filters(self, query, count_query, condition):
                # 先调用父类方法应用基础等值过滤
                query, count_query = super()._apply_filters(query, count_query, condition)

                if not condition:
                    return query, count_query

                # 模糊搜索（使用工具方法）
                if hasattr(condition, 'name') and condition.name:
                    query, count_query = self._apply_like_filter(
                        query, count_query, 'name', condition.name
                    )

                return query, count_query
            ```
        """
        if condition:
            mapper = inspect(condition.__class__)
            result = {}
            for column in mapper.columns:
                value = getattr(condition, column.key, None)
                if value is None or value == "":
                    continue
                result[column.key] = value

            condition_data = result
            for field, value in condition_data.items():
                if hasattr(self.model, field):
                    model_field = getattr(self.model, field)
                    if query is not None:
                        query = query.where(model_field == value)
                    if count_query is not None:
                        count_query = count_query.where(model_field == value)

        return query, count_query
