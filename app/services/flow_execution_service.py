"""
流程执行记录服务模块

本模块提供流程执行记录的 CRUD 操作。流程执行记录（FlowExecution）
是每次流程运行的持久化记录，用于追踪流程执行历史和结果。

主要功能:
    1. 执行记录管理: 创建、查询、更新、删除执行记录
    2. 执行历史查询: 分页查询流程的执行历史
    3. 执行状态追踪: 记录每次执行的状态、输入、输出和错误信息

执行记录结构:
    FlowExecution 表包含:
    - id: 执行记录ID
    - flow_id: 关联的流程ID
    - status: 执行状态（PENDING/RUNNING/SUCCESS/FAILED/CANCELLED）
    - input_data: 执行输入数据（JSON）
    - output_data: 执行输出数据（JSON）
    - error_message: 错误信息（执行失败时）
    - start_time: 开始时间
    - end_time: 结束时间

与其他服务的关系:
    - FlowService: 管理流程定义
    - FlowExecutorService: 负责实际的流程执行和运行时管理
    - FlowExecutionService: 管理执行记录的持久化（本服务）

继承自 BaseService:
    自动继承以下方法:
    - get_list(): 获取执行记录列表
    - get_by_id(): 根据ID获取执行记录
    - create(): 创建执行记录
    - update(): 更新执行记录
    - delete(): 删除执行记录
    - page_query(): 分页查询执行记录
    - count(): 统计执行记录数量

使用场景:
    1. 查询流程的执行历史
    2. 获取某次执行的详细结果
    3. 统计流程执行成功率
    4. 清理过期的执行记录
"""

from app.models.flow_execution import FlowExecution
from app.services.base_service import BaseService
from app.schemas.execution_schema import FlowExecutionCreate
from app.schemas.base_schema import BaseView


class FlowExecutionService(BaseService[FlowExecution, FlowExecutionCreate, BaseView]):
    """
    流程执行记录服务类

    提供流程执行记录的 CRUD 操作。继承自 BaseService，
    自动获得完整的增删改查能力。

    泛型参数:
        FlowExecution: SQLAlchemy 模型类
        FlowExecutionCreate: 创建操作的 Schema（目前未使用特殊创建逻辑）
        BaseView: 更新操作的 Schema（使用空基类，表示不支持通用更新）

    核心功能（继承自 BaseService）:
        - get_list: 获取执行记录列表
        - get_by_id: 根据ID获取单个执行记录
        - get_one: 根据条件获取单个执行记录
        - create: 创建执行记录
        - delete: 删除执行记录
        - count: 统计执行记录数量
        - exists: 检查执行记录是否存在
        - page_query: 分页查询执行记录

    扩展建议:
        如果需要更复杂的查询功能，可以在子类中添加：
        - get_by_flow_id: 获取指定流程的所有执行记录
        - get_success_count: 获取成功执行的数量
        - get_failed_count: 获取失败执行的数量
        - cleanup_old_executions: 清理过期的执行记录

    使用示例:
        ```python
        service = FlowExecutionService()

        # 分页查询执行记录
        params = PaginationParams(page=1, page_size=10)
        executions, total = await service.page_query(db, params)

        # 获取单个执行记录
        execution = await service.get_by_id(db, execution_id=1)

        # 统计执行记录数量
        count = await service.count(db)
        ```

    Note:
        实际的流程执行由 FlowExecutorService 处理，
        本服务仅负责执行记录的数据管理。
    """

    def __init__(self):
        """
        初始化流程执行记录服务

        调用父类构造函数，绑定 FlowExecution 模型。
        """
        super().__init__(FlowExecution)


# 全局单例实例
# 使用单例模式避免重复创建服务实例
flow_execution_service = FlowExecutionService()
