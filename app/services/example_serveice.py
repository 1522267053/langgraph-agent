"""
样例服务模块

本模块是一个示例服务实现，展示了如何基于 BaseService 创建业务服务。
可以作为创建新服务的参考模板。

用途:
    1. 代码示例: 展示服务类的标准结构和写法
    2. 测试参考: 可用于单元测试的模拟数据
    3. 开发模板: 新服务可以复制此文件进行修改

创建新服务的步骤:
    1. 复制本文件并重命名（如 xxx_service.py）
    2. 修改导入的模型类和 Schema 类
    3. 修改服务类名和泛型参数
    4. 根据需要添加自定义方法
    5. 创建全局单例实例

继承自 BaseService:
    自动继承以下方法:
    - get_list(): 获取列表
    - get_by_id(): 根据ID获取
    - get_one(): 根据条件获取单个对象
    - create(): 创建
    - update(): 更新
    - delete(): 删除（逻辑删除）
    - count(): 统计数量
    - exists(): 检查是否存在
    - bulk_create(): 批量创建
    - bulk_delete(): 批量删除
    - page_query(): 分页查询
"""

from app.models.example import Example
from app.services.base_service import BaseService
from app.schemas.example_schema import ExampleCreate, ExampleUpdate


class ExampleService(BaseService[Example, ExampleCreate, ExampleUpdate]):
    """
    样例服务类

    这是一个示例服务实现，展示了如何继承 BaseService 创建业务服务。
    通过继承 BaseService，自动获得完整的 CRUD 能力。

    泛型参数:
        Example: SQLAlchemy 模型类，定义数据表结构
        ExampleCreate: 创建操作的 Pydantic Schema
        ExampleUpdate: 更新操作的 Pydantic Schema

    自动继承的方法:
        查询方法:
            - get_list(db, filters, order_by, include_deleted): 获取列表
            - get_by_id(db, id, raise_not_found, include_deleted): 根据ID获取
            - get_one(db, filters, include_deleted): 根据条件获取单个对象
            - count(db, filters, include_deleted): 统计数量
            - exists(db, id, include_deleted): 检查是否存在
            - page_query(db, query_params): 分页查询

        写入方法:
            - create(db, obj_in): 创建新记录
            - update(db, obj_in): 更新记录
            - delete(db, id): 逻辑删除记录
            - bulk_create(db, objs_in): 批量创建
            - bulk_delete(db, ids): 批量删除

    扩展方式:
        如果需要添加自定义业务逻辑，可以在子类中定义新方法：

        ```python
        class ExampleService(BaseService[Example, ExampleCreate, ExampleUpdate]):
            def __init__(self):
                super().__init__(Example)

            async def get_by_name(self, db: AsyncSession, name: str) -> Optional[Example]:
                '''根据名称查询'''
                return await self.get_one(db, filters=Example(name=name))

            async def search(self, db: AsyncSession, keyword: str) -> List[Example]:
                '''模糊搜索'''
                # 需要重写 _apply_filters 方法支持模糊查询
                pass
        ```

    使用示例:
        ```python
        # 获取全局实例
        from app.services.example_serveice import example_service

        # 创建记录
        example = await example_service.create(db, ExampleCreate(
            name="示例",
            description="这是一个示例"
        ))

        # 查询单条
        example = await example_service.get_by_id(db, id=1)

        # 分页查询
        params = PaginationParams(page=1, page_size=10)
        items, total = await example_service.page_query(db, params)

        # 更新记录
        example = await example_service.update(db, ExampleUpdate(
            id=1,
            name="新名称"
        ))

        # 删除记录
        await example_service.delete(db, id=1)
        ```
    """

    def __init__(self):
        """
        初始化样例服务

        调用父类构造函数，绑定 Example 模型。
        所有继承的 CRUD 方法将针对 Example 表操作。
        """
        super().__init__(Example)


# 全局单例实例
# 使用单例模式避免重复创建服务实例
# 在其他模块中通过导入此实例来使用服务
example_service = ExampleService()
