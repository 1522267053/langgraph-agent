"""
服务层模块

本包包含应用程序的所有业务服务类。服务层位于控制器和模型之间，
负责实现业务逻辑和数据访问操作。

模块结构:
    - base_service.py: 通用 CRUD 服务基类
    - flow_service.py: 流程管理服务
    - flow_execution_service.py: 流程执行记录服务
    - flow_executor_service.py: 流程执行引擎服务
    - ability_card_service.py: 能力卡片服务
    - example_serveice.py: 示例服务（开发参考）

设计原则:
    1. 单一职责: 每个服务专注于一个业务领域
    2. 可复用性: 通过 BaseService 基类复用通用 CRUD 逻辑
    3. 可测试性: 服务方法设计为纯函数，便于单元测试
    4. 异步优先: 所有数据库操作使用 async/await

使用方式:
    ```python
    from app.services.flow_service import flow_service
    from app.services.ability_card_service import ability_card_service

    # 使用服务
    flow = await flow_service.get_by_id(db, flow_id=1)
    cards = await ability_card_service.get_basic_cards(db)
    ```
"""
