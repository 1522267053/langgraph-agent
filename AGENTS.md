# AI 智能体流程编排平台开发指南

基于 LangGraph 的智能体流程编排平台，前后端分离架构。
- **后端**: Python 3.12 | FastAPI | SQLAlchemy 2.0 (aiomysql) | Pydantic v2 | LangGraph | ChromaDB
- **前端**: Vue 3 | TypeScript | Vite | Pinia | Vue Flow | Element Plus

## 构建与运行命令

### 后端

```bash
poetry install                          # 安装依赖（.venv/ 虚拟环境，poetry.toml in-project=true）
poetry run uvicorn main:app --reload    # 开发服务器（端口 8000）
poetry run ruff format app/             # 格式化代码
poetry run ruff check app/ --fix        # 检查并自动修复
poetry run mypy app/                    # 类型检查
```
- Python 严格锁定 `>=3.12,<3.13`
- Ruff 无自定义配置（无 `[tool.ruff]` 段），使用默认值
- 无测试框架，修改后请运行 `ruff check` + `ruff format` 验证
- Poetry 使用腾讯镜像源（primary）+ USTC 镜像源（supplemental）

### 前端

```bash
cd frontend
npm install && npm run dev    # 安装依赖并启动（端口 3000）
npm run build                 # 生产构建（vite build，不含 vue-tsc）
npm run lint                  # ESLint 检查（自动修复）
npm run format                # Prettier 格式化
```
- 无测试框架，修改后请运行 `npm run lint` 验证
- `/api` 请求通过 Vite 代理转发到 `http://127.0.0.1:8000`
- `npm run build` 只运行 `vite build`，不包含类型检查

## 项目结构

```
app/                          # 后端
├── config/                   # 配置、数据库（settings.py, database.py, logging_config.py, build_utils.py, version.py）
├── constants/                # 静态常量（node_types.py，21 种节点类型标签）
├── api/                      # API 路由（自动扫描注册，必须导出 router）
├── models/                   # SQLAlchemy 模型（禁止外键，自动扫描加载）
├── schemas/                  # Pydantic Schema（BaseView → XxxCreate/XxxUpdate）
├── services/                 # 业务逻辑层（BaseService 泛型 CRUD）
│   └── scheduler_service.py  #   APScheduler 定时任务（文档异步处理）
├── middleware/                # 全局异常处理器 + 认证中间件 + 安全头中间件（SecurityHeaderMiddleware）
│   └── auth_middleware.py     #   Session cookie 认证（密码哈希内存缓存，仅 /api/* 路径需认证）
├── agent_flow/               # LangGraph 流程编排
│   ├── node_handlers/        #   节点处理器（自动扫描注册）
│   │   ├── llm_tool_handler.py #   LLM 节点主入口（execute + ReAct 循环 + LlmNodeConfig）
│   │   ├── llm_factory.py    #     LLM 实例创建和工具绑定
│   │   ├── llm_message_builder.py # 消息构建（历史加载、恢复、multimodal）
│   │   ├── llm_stream.py     #     流式 LLM 调用（重试、thinking 解析）
│   │   └── llm_tool_executor.py #  工具执行（并行执行、人工交互、审批、截断）
│   ├── ai_provider/          #   AI 模型提供商（自动扫描注册）
│   ├── flow_context.py       #   FlowState 定义
│   ├── flow_event.py         #   流程执行 SSE 事件类
│   ├── message_buffer.py     #   对话消息缓冲区（加载、追加、压缩、持久化）
│   ├── variable_resolver.py  #   统一变量解析器（context > input > variables 优先级）
│   ├── handler_registry.py   #   NodeHandlerRegistry
│   ├── tool_resolver.py      #   LLM 工具解析
│   ├── tool_output_truncate.py #  工具输出统一截断（JSON 感知，阈值可配置）
│   ├── graph_builder.py      #   图构建器
│   ├── edge_router.py        #   通用边路由（wire_edges, iteration_guard）
│   ├── subgraph_builder.py   #   子图构建器基类（BaseSubgraphBuilder）
│   ├── subgraph_runner.py    #   子图流式执行器（SubgraphRunner）
│   ├── card_subgraph.py      #   卡片子图构建器
│   ├── loop_subgraph.py      #   循环体子图构建器
│   ├── execution_context.py  #   执行上下文（ContextVar, parent_path）
│   ├── mcp_manager.py        #   MCP 服务器管理
│   ├── mysql_checkpointer.py #   MySQL Checkpointer（ormsgpack + gzip）
│   ├── safe_eval.py          #   安全表达式求值
│   └── exceptions.py         #   自定义异常
└── utils/                    # 工具函数（loader.py 自动加载机制）

frontend/src/                 # 前端
├── api/                      # API 请求封装（axios, get/post/put/del）
├── components/FlowEditor/    # 流程编辑器（nodes/, config/, components/, 画布, 面板）
├── components/AgentChat/     # Agent 对话组件（ChatInput, MessageItem, SessionSidebar, MemoryPanel, TodoPanel）
├── components/common/        # 共享组件（AIMessageContent, ThinkingBlock 等）
├── composables/              # 组合式函数（useSSE, useStreamingMessage 等）
├── constants/                # 静态常量（nodeTypes, status, operators）
├── stores/                   # Pinia 状态仓库（flowStore, agentStore）
├── types/                    # TypeScript 类型定义
├── utils/                    # 工具函数（sse, flowTransform, format）
└── views/                    # 页面组件（14 个）
```

## 自动注册机制（loader.py）

启动顺序（`main.py` lifespan + 模块级代码）:

1. **模块级**: `register_exception_handlers(app)` + `register_all_routers(app)`
2. **lifespan**: `load_all_models()` → `load_all_handlers()` → `load_all_providers()` → `init_db()` → `scheduler_service.start()` → yield → `mcp_tool_manager.clear_all_cache()` + `scheduler_service.shutdown()` + `close_db()`

| 扫描目录 | 触发方式 |
|---------|---------|
| `app/models/*.py` | SQLAlchemy metadata 注册 |
| `app/agent_flow/node_handlers/*.py` | `@register` / `@register_factory` 装饰器 |
| `app/agent_flow/ai_provider/*.py` | `@AIProviderRegistry.register` 装饰器 |
| `app/api/*.py` | 导出的 `router` 变量 → `include_router` |

新文件放入对应目录即可自动加载，无需手动 import。

## 后端代码规范

### 导入顺序

```python
# 1. 标准库  2. 第三方库  3. 本地模块
from typing import Optional
from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession
from app.config.database import get_db
from app.models.flow import Flow
from app.schemas.flow_schema import FlowCreate
from app.services.flow_service import flow_service
```
- `app.agent_flow.*` 导入与本地模块交错放置（非严格在最后），循环依赖用 `TYPE_CHECKING` 守卫

### 命名约定

| 类型 | 规范 | 示例 |
|------|------|------|
| 类 | PascalCase | `FlowService`, `FlowApi`, `FlowStatus` |
| 函数/方法/变量 | snake_case | `get_by_id`, `flow_id` |
| 常量 | UPPER_SNAKE | `MAX_HUMAN_INTERACTIONS = 100` |
| 私有方法 | _snake_case | `_register_routes`, `_apply_filters` |
| 单例实例 | snake_case | `flow_service`, `settings`, `variable_resolver` |
| 枚举访问 | .value | `FlowStatus.DRAFT.value`（数据库存储） |
| 模型文件 | snake_case | `flow_node.py`, `mcp_server.py` |
| Schema 文件 | xxx_schema.py | `flow_schema.py`, `mcp_server_schema.py` |
| Service 文件 | xxx_service.py | `flow_service.py` |
| API 文件 | xxx_api.py | `flow_api.py` |

### 注释规范

- 所有方法（含私有方法）必须有 docstring，简洁说明用途和关键行为
- 核心逻辑处加行内注释（如条件分支、特殊处理、状态流转）
- 模块级常量加说明注释
- 不同逻辑区块用 `# ---- 区块名 ----` 分隔
- 不写废话注释（如 `# 获取用户` 放在 `get_user()` 上方）

### 数据模型

```python
class Flow(DbBaseModel):
    __tablename__ = "flow"
    name: Mapped[str] = mapped_column(String(255), nullable=False, comment="流程名称")
    status: Mapped[int] = mapped_column(SmallInteger, default=FlowStatus.DRAFT.value, comment="状态")
    # 禁止外键，每个列必须有 comment
```
- 基类 `DbBaseModel` 提供: `id`(Integer PK), `creator_id/type/name`, `create_time`,
  `modifier_id/type/name`, `modify_time`, `is_delete`(SmallInteger, default=0)
- 软删除自动过滤: 所有 SELECT 默认排除 `is_delete=1`（`do_orm_execute` 事件监听）
- 覆盖软删除: `stmt.execution_options(include_deleted=True)`
- `create_time` default 使用 `datetime.now`（函数引用，非调用），SQLAlchemy 在插入时调用

### Schema

```python
class FlowBase(BaseView):                # 继承 BaseView（含 id, creator_*, *_time 公共字段）
    name: Optional[str] = Field(None, description="流程名称")

class FlowCreate(FlowBase):
    name: str = Field(..., description="流程名称")  # 必填覆盖

class FlowUpdate(FlowBase):
    pass  # 全部可选（继承 BaseView）

class FlowCondition(BaseModel):           # 查询条件 schema（不继承 BaseView）
    name: Optional[str] = None
```
- `BaseView.model_config = ConfigDict(from_attributes=True, validate_assignment=True)`
- `BaseView.to_model(ModelClass)`: Schema→Model，只保留 Model 中存在的字段
- `BaseView.model_to_view(instance)`: Model→Schema，只保留 Schema 中定义的字段
- `BaseView` 不含 `is_delete` 字段
- 通用 schema: `PaginationParams[T]`, `PaginatedResponse[T]`, `ApiResponse[T]`（code=1 成功，code=0 失败）
- `BaseService.update()` 使用 `exclude_unset=True`，无法将字段更新为 `None`

### 服务层

```python
class FlowService(BaseService[Flow, FlowCreate, FlowUpdate]):
    def __init__(self):
        super().__init__(Flow)

    def _apply_filters(self, query, count_query, condition):
        query, count_query = super()._apply_filters(query, count_query, condition)
        if condition and hasattr(condition, "name") and condition.name:
            query, count_query = self._apply_like_filter(query, count_query, "name", condition.name)
        return query, count_query

flow_service = FlowService()  # 全局单例
```
- `BaseService[ModelType, CreateSchemaType, UpdateSchemaType]` 泛型绑定
- 提供: `get_list`, `get_by_id`(404), `get_one`, `create`, `update`, `delete`(软删除),
  `count`, `exists`, `bulk_create`, `bulk_delete`, `page_query`
- 可重写钩子: `_build_base_query`, `_apply_filters`, `_apply_ordering`, `_set_creator_fields`, `_set_modifier_fields`
- 便利方法: `_apply_like_filter(query, count_query, field_name, value)`

### 数据库会话管理

- `get_db()` 依赖注入: yield session，异常时 rollback，**不自动 commit**
- Service 层负责 commit: `BaseService` 的 `create/update/delete` 内部调用 `await db.commit()`
- SSE 流式方法（`flow_executor_service`/`agent_executor_service`）使用 `AsyncSessionLocal()` 自建会话，内含多处 commit
- **不要**在接收 `get_db()` session 的方法中将 `db.commit()` 改为 `db.flush()`

### API 路由

```python
class FlowApi(BaseApi[Flow, FlowBase, FlowBase, FlowCreate, FlowUpdate]):
    def __init__(self):
        super().__init__(service=flow_service, router_prefix="/api/flow", router_tags=["流程"],
                         route_config=RouteConfig(enable_get=False))

    async def delete(self, db: AsyncSession, id: int) -> None:  # 可重写 CRUD 方法
        await flow_service.delete(db, id)

flow_api = FlowApi()
router = flow_api.router  # 必须导出 router，用于自动注册
```
- `BaseApi[M, V, Q, C, U]` 泛型参数: Model, View, Query, Create, Update
- `RouteConfig`: `enable_page`(T), `enable_get`(T), `enable_create`(T), `enable_update`(T),
  `enable_delete`(T), `enable_batch_delete`(T), `enable_batch_create`(F), `enable_batch_update`(F)
- 自动路由: POST `/page`, GET `/get/{id}`, POST `/create`, POST `/update`,
  GET `/delete/{id}`, POST `/deleteBatch`
- 自定义路由在 `__init__` 中通过 `self.router.get/post` 注册

### 响应与错误处理

```python
return ApiResponse.success(data=item, msg="查询成功")  # 成功（code=1）
return ApiResponse.error(msg="未找到数据")            # 业务错误（code=0）
raise HTTPException(status_code=404, detail="不存在")  # HTTP异常
```
- 全局异常中间件: **所有异常转为 HTTP 200** + `ApiResponse.error()`
- 异常层级: `StarletteHTTPException`→`RequestValidationError`→`ValidationError`→`FlowValidationError`(400)
  →`NodeExecutionError`→`ToolExecutionException`→`MaxIterationsExceededException`→`SQLAlchemyError`→`Exception`(500)
- debug 模式(`settings.debug=True`)返回完整错误信息，生产模式返回通用消息

### 节点处理器

```python
@NodeHandlerRegistry.register("my_node")        # 直接注册
class MyNodeHandler(BaseNodeHandler):
    async def execute(self, node: FlowNode, state: FlowState, config: Optional[RunnableConfig] = None,
                      *, writer: Optional[StreamWriter] = None) -> FlowState | dict:
        node_config = node.base_config or {}
        value = self._resolve_variable("input.question", state)
        state.set_variable("result", output_value)
        return state

    async def get_tool(self, node: FlowNode):  # 可选：为 LLM 提供工具
        return [Tool(name="...", description="...", args_schema=...)]

    async def get_system_prompt_hint(self, node: FlowNode) -> Optional[str]:  # 可选：注入 system_prompt
        return "## 提示内容\n..."
```
- LLM 节点使用 `@NodeHandlerRegistry.register_factory("llm")` 工厂注册（需要依赖注入）
- 工具节点通过 `source_handle="tools"` 边连接到 LLM 节点
- `BaseNodeHandler` 提供: `_resolve_variable`, `_render_template`, `_variable_exists`, `_get_nested_value`
- 可选重写: `get_input_content`, `get_output_content`, `get_tool_config`, `get_system_prompt_hint`
- `get_system_prompt_hint` 支持同步和异步两种模式，`LlmToolNodeHandler` 在收集提示时自动适配
- `allow_multiple_tool_connections()`: 控制同一类型工具节点是否允许重复连接到同一 LLM，默认 `True`
  - 返回 `False` 的类型（如 `skill`、`memory`）在保存边时会校验，同一 LLM 只能连接一个该类型节点
  - 适用于使用固定工具名的节点，避免多实例导致工具名冲突
  - `NodeHandlerRegistry.get_singleton_tool_types()` 自动收集所有返回 `False` 的类型
- `get_default_config()`: 从 `ConfigClass` 自动生成默认配置字典（`model_construct().model_dump()`），供 AI 创建节点和前端新建节点时使用
- `get_config_schema()`: 从 `ConfigClass` 的 JSON Schema 提取字段描述，供前端 `/api/ai/flow/config-schemas` 接口返回
  - **Pydantic v2 注意**: `default_factory` 字段在 `model_json_schema()` 中不含 `"default"` 键，`_schema_from_pydantic` 已通过回查 `model_fields[name].default_factory()` 补偿。新增 `default_factory` 字段无需额外处理

## 添加新模块清单

### 后端新 CRUD 模块
1. `app/models/<resource>.py` — 模型（继承 `DbBaseModel`，禁止外键，每列加 `comment`）
2. `app/schemas/<resource>_schema.py` — Schema（`BaseView` → `Create/Update/Condition`）
3. `app/services/<resource>_service.py` — 服务（继承 `BaseService`，导出 `xxx_service` 单例）
4. `app/api/<resource>_api.py` — 路由（继承 `BaseApi`，导出 `router`）

### 后端新节点类型
1. `app/models/flow_node.py` — 添加 `NodeType` 枚举值 + `BASIC_NODE_TYPES`
2. `app/constants/node_types.py` — 添加节点类型中文标签
3. `app/agent_flow/node_handlers/<type>_handler.py` — `@NodeHandlerRegistry.register("type")`
4. `frontend/src/constants/nodeTypes.ts` — 节点配置
5. `frontend/src/components/FlowEditor/nodes/<Type>Node.vue` — 节点组件
6. `frontend/src/components/FlowEditor/config/<Type>Config.vue` — 配置组件
7. `frontend/src/components/FlowEditor/nodes/index.ts` — `markRaw()` 注册

## 关键注意事项

### 后端

1. **枚举默认值**: 使用 `.value` 访问枚举值（`default=FlowStatus.DRAFT.value`）
2. **软删除**: 查询自动过滤 `is_delete=1`，无需手动加条件
3. **自动注册**: 模型、处理器、AI Provider、路由放入对应目录即可自动加载
4. **数据库操作**: 必须 `async/await`，通过 `get_db()` 依赖注入获取 session
5. **响应格式**: 统一 `ApiResponse` 包装（code=1 成功，code=0 失败），HTTP 状态码始终 200
6. **外键**: 禁止数据库外键，关联通过应用层 `server_id` 整数字段处理
7. **system_prompt**: 不存储在 messages/checkpoint/DB 中，LLM 调用时临时拼接 `SystemMessage`
8. **条件分支**: `ConditionNodeHandler` 设置 `state.variables["_condition_branch"]` = `"true"` / `"false"`
9. **子流程展开**: Card 节点的子流程用 `"__"` 分隔 key（如 `"card_key__sub_node_key"`）
10. **MCP 节点**: 不加入执行图，仅作为工具提供者（通过 `source_handle="tools"` 边）
11. **工具边**: `source_handle == "tools"` 的边不加入 LangGraph 图，仅用于工具发现
12. **循环保护**: `FlowState.iteration_count` 超过 `max_iterations`(默认100) 时终止
13. **对话历史**: 双写 — LangGraph checkpoint + DB 表（`ConversationMessage`/`AgentMessage`）
14. **关联数据**: `Flow.nodes`/`Flow.edges` 是 `ClassVar`（非 ORM relationship），由 Service 手动查询赋值
15. **知识库 API**: 使用 `APIRouter()` 包装实现额外路由嵌套层级
16. **记忆节点**: 仅 Agent 模式可用（Flow 模式 `NodePanel` 过滤掉），通过 `source_handle="tools"` 连接 LLM 节点
17. **LLM 配置注入**: `LlmToolNodeHandler` 在工具收集阶段会将 `model/api_key/base_url` 注入到 `handler._llm_config`（记忆节点 AI 总结需要）
18. **记忆搜索范围**: `memory_search` 默认搜索 warm/cold 层级，hot 层已通过 `get_system_prompt_hint` 全量注入；可指定 `tier='hot'` 搜索热记忆详细内容；`memory_get` 可通过 ID 精确获取记忆完整内容
19. **工具节点单实例约束**: `skill`、`memory` 等使用固定工具名的节点，同一 LLM 只能连接一个该类型节点（`allow_multiple_tool_connections()=False`），保存边时在 `flow_edge_api` 和 `ai_flow_api` 校验
20. **知识库系统提示词**: `KnowledgeNodeHandler` 实现 `get_system_prompt_hint`，作为工具连接到 LLM 时自动注入知识库名称、简介和使用规则
21. **文档异步处理**: 上传只存文件立即返回，APScheduler 定时任务轮询处理（解析→分段→向量化），`processing_status` 状态流转（0待处理→1处理中→2已完成→3失败）
22. **循环嵌套禁止**: 循环节点内不能嵌套另一个循环节点（直接或通过能力卡片间接），前端 NodePanel 在循环子视图中隐藏循环节点，后端 `flow_node_api` 的 `create/update/batch_create/batch_update` 四个路径均校验
23. **循环嵌套跨 flow 检查**: `_check_nested_loop` 查询所有 card 节点的 `base_config.ref_flow_id`，找到引用当前 flow 的 card 后检查其 node_key 祖先是否有 loop
24. **卡片循环嵌套检查**: `_check_card_loop_nesting` 当 card 节点在 loop 内时，检查其 `ref_flow_id` 对应的 flow 是否含 loop 节点
25. **子图基础设施**: `BaseSubgraphBuilder`（3 个可重写钩子：`_collect_nodes`、`_prepare_node`、`_get_event_meta`）、`SubgraphRunner.stream()`（NodeExecution CRUD）、`edge_router.wire_edges(iteration_guard=False)`（子图用 False 禁用循环保护）
26. **边路由互斥分支处理**: `wire_edges()` 默认对同一目标的多个入边创建 `NamedBarrierValue` 屏障（等待所有前驱完成）。当所有入边源来自同一个条件路由器（condition/intent_router/表达式条件边）时为互斥分支，此时逐条 `add_edge` 而非创建屏障，避免屏障永远无法满足导致流程卡死
27. **子视图节点面板**: 循环子视图中隐藏循环节点但显示条件节点，卡片子视图中所有节点均可使用
28. **能力卡片子视图添加**: `addFlowCardNode` 接受可选 `parentId` 参数，在子视图中自动加 `parentId__` 前缀到节点 id
29. **MySQL Checkpointer**: ormsgpack 序列化 + gzip 压缩，自动检测 gzip 魔数解压
30. **删除历史必须同步清理 checkpoint**: 对话历史采用双写机制（LangGraph checkpoint + DB），删除/修改 DB 消息后必须同步清理 checkpoint，否则下次对话时 checkpoint 中的旧消息会通过增量写入重新回填 DB。统一使用 `_cleanup_thread_checkpoint(session_id)` 方法清理
31. **变量解析优先级**: `variable_resolver.py` 统一解析变量，无前缀时按 context（input_variables 映射）> input（state.input_data）> variables（state.variables）顺序查找
32. **消息缓冲区**: `message_buffer.py` 管理对话消息的完整生命周期（加载历史→追加→压缩→持久化到 DB），SSE 流式方法中自动管理
33. **工具输出统一截断**: `tool_output_truncate.py` 提供所有工具结果的 JSON 感知截断，阈值通过 `.env` 配置（`TOOL_OUTPUT_MAX_LINES`/`TOOL_OUTPUT_MAX_BYTES`）
    - **统一输出为 JSON 字符串**: 所有工具输出在截断层统一为 JSON 字符串，LLM 始终收到结构完整、可解析的 JSON
    - **str 输入处理**: 先尝试 `json.loads()` 解析为 dict，成功则走 JSON 感知截断；解析失败则退化为纯文本截断
    - **dict 类型截断**: 保留 JSON 结构完整，只截断大字段值（str 字段独立截断、list 字段保留前 N 项），超限时保存完整内容到临时文件
    - **截断入口**: `smart_truncate_output(result, prefix="tool_output")` → 返回截断后的字符串
    - **截断时机**: `llm_tool_executor.py` 在 `handle_tool_calls()` 中构造 `ToolMessage` 前统一调用，所有工具（Shell、Sub-Agent、MCP、Knowledge、Memory 等）一致处理
    - **Shell 工具**: 内部通过 `_apply_shell_output_truncation()` 预截断（stdout/stderr 作为 dict 独立字段被分别截断），LLM 层兜底截断通常不会再次触发
34. **LLM 节点模块化拆分**: `LlmToolNodeHandler` 主入口约 410 行，职责拆分到 4 个子模块（高内聚低耦合，回调传参模式）：
    - `llm_factory.py`: `create_llm()` + `prepare_llm()` — LLM 实例创建和工具绑定
    - `llm_message_builder.py`: `build_initial_messages()` + `validate_tool_pairs()` + `inject_resume_if_needed()` + `append_user_message()` + `load_history_from_db()` + `should_auto_compress()` — 消息构建全流程
    - `llm_stream.py`: `stream_llm_response()` + `parse_content_blocks()` — 流式调用 + 重试 + thinking 解析
    - `llm_tool_executor.py`: `setup_tool_handlers()` + `handle_tool_calls()` + `execute_tool()` + `handle_human_interaction()` + `reject_remaining_tools()` — 工具执行全流程
    - 依赖方向单向（无循环依赖）: `llm_tool_handler.py` → 四个子模块，子模块不 import 主入口

### 前端

1. **路径别名**: `@/` 代表 `src/`
2. **路由模式**: `createWebHashHistory()`（hash 模式），全部懒加载
3. **类型导入**: 使用 `import type` 导入纯类型
4. **Vue Flow 节点**: 必须用 `markRaw()` 包装，handles 的 `id` 字段用于命名连接点
5. **Element Plus 组件**: 通过 `unplugin-vue-components` 按需自动导入，**不需要**手动 import
6. **Element Plus 图标**: 每个组件文件显式 import，**不在 main.ts 全局注册**
7. **Element Plus 功能 API CSS**: `ElMessage`/`ElMessageBox` 等的样式需在 `main.ts` 手动导入
8. **SSE 流式**: 使用原生 `fetch` + `ReadableStream`（POST-based），**非 EventSource**
9. **共享组件**: `common/` 下有 `AIMessageContent`、`ThinkingBlock`、`ExecutionResultContent`、`FlowInputForm`、`FilePickerDialog` 可复用
10. **API 数据属性**: 使用 snake_case 与后端保持一致（`flow_type`、`node_key`、`is_enabled`）
11. **FlowEdit.vue**: 同时用于流程和智能体的创建/编辑（根据路由区分）
12. **Pinia Store**: 组合式 API 风格 `defineStore(id, () => {})`，错误通过 `ElMessage.error()` 显示
13. **Handle 颜色约定**: green=输入, blue=输出, orange=工具, red=假分支
14. **三种节点 Handle 模式**: 标准 I/O、工具启用型（LLM，有橙色工具输入）、纯工具提供者（MCP/Skill/Memory，只有绿色工具输出）
15. **文件选择**: 所有需要选择文件的场景必须使用 `FilePickerDialog` 组件（`common/FilePickerDialog.vue`），**禁止**使用 `FileUpload` 组件。`FilePickerDialog` 通过弹窗从已有文件中选择，支持 `accept`（文件类型过滤，后端 SQL 过滤）、`maxSize`（MB 限制，前端校验）、`multiple`（单/多选，由开始节点配置控制）。已选文件展示为 closable `el-tag` 标签。使用场景：`FlowInputForm`、`ChatInput` 参数面板等
16. **App.vue 全屏页**: `isEditorPage` 是 `ref(true)` + `watch(route.name)`，默认 `true` 避免侧边栏闪烁。SetupWizard、Login、FlowEdit 等页面全屏渲染无侧边栏。路由名必须在列表中才能生效。
17. **Prettier 配置**: 不使用分号 | 单引号 | 2空格缩进 | 100字符行宽 | 无 trailing 逗号 | 单参数箭头函数省略括号
18. **ESLint 规则**: `@typescript-eslint/no-unused-vars` error（`_` 前缀忽略）、`@typescript-eslint/no-explicit-any` warn、`vue/multi-word-component-names` off、`no-console`/`no-debugger` off

## 认证系统

基于 session cookie 的简单密码认证，非 JWT。

### 密码来源优先级
DB（`global_config` 表 `login_password_hash`）> `.env` 的 `LOGIN_PASSWORD`

### 关键文件
| 文件 | 角色 |
|------|------|
| `app/middleware/auth_middleware.py` | ASGI 中间件，每请求校验 cookie，仅拦截 `/api/*` |
| `app/api/auth_api.py` | `/api/auth/check`、`/api/auth/login`、`/api/auth/logout` |
| `app/services/global_config_service.py` | 密码 sha256 哈希、存储、校验 |
| `frontend/src/views/Login.vue` | 登录页（记住密码/自动登录） |
| `frontend/src/views/SetupWizard.vue` | 初始化时可设密码（可选） |
| `frontend/src/views/Settings.vue` | 可修改密码、关闭登录保护 |
| `frontend/src/api/auth.ts` | `authApi.check()` / `login()` / `logout()` |
| `frontend/src/router/index.ts` | `beforeEach` 检查 auth 状态，未登录重定向 `/login?redirect=...` |
| `frontend/src/api/index.ts` | axios 响应拦截器处理 401 跳转登录页 |

### Session 机制
- **Cookie**: `auth_session`，httponly + samesite=lax，7 天有效
- **内容**: `base64(timestamp:hmac_sha256(timestamp, password_hash))`
- **验证**: 每次请求用内存缓存的 `password_hash` 重新计算 HMAC 比对
- **密码哈希缓存**: 启动时从 DB 加载一次，常驻内存，密码变更时 `invalidate_password_cache()` 清空重载
- **豁免路径**: `/api/auth/*`、`/api/config/check`、`/api/config/init`、`/api/config/providers`、`/api/health`
- **非 `/api/*`**: 静态文件/前端页面/Docs 不经过认证检查

### Schema 字段
- `InitConfigRequest.login_password: Optional[str]` — 初始化时可选
- `UpdateConfigRequest.login_password: Optional[str]` — 空字符串 `""` 清除密码
- `GlobalConfigResponse.has_password: bool` — 是否已配置密码

## 三层记忆架构

记忆节点实现基于 Claude Code Self-Healing Memory 理念的三层记忆系统，以 Agent 为维度管理。

### 层级定义

| 层级 | 加载方式 | 内容特征 | 限制 |
|------|---------|---------|------|
| **hot（热）** | 每次对话自动注入 system_prompt | 紧凑指针索引（`[id] title ★★★`），按 category 分组 | 可配置行数/字节数（默认200行/25KB） |
| **warm（温）** | LLM 通过 `memory_search` 向量检索 | 结构化详细记忆 | 最多返回 N 条（可配置） |
| **cold（冷）** | 同 warm，通过 `memory_search` 检索 | 低优先级记忆，可被自动升温 | — |

### 数据流

```
LLM 对话开始
  ↓
system_prompt 自动注入 [热记忆索引]（get_system_prompt_hint 异步获取）
  ↓
LLM 判断需要回忆 → memory_search → 向量检索 warm + cold（可指定 tier='hot' 搜索热记忆）→ access_count++, last_access_time 更新
  ↓
access_count 达阈值（默认5）→ 自动 cold→warm→warm→hot（升温后重置计数）
  ↓
LLM 保存记忆 → memory_save → importance=5→hot, 3-4→warm, 1-2→cold
  ↓
LLM 查看热记忆详情 → memory_get → 按 ID 精确获取完整内容
  ↓
hot 数量超过 consolidate_threshold（默认50）→ 自动调用 LLM 全量总结整理
  ↓
LLM 清理记忆 → memory_delete
```

### 记忆模型（app/models/memory.py）

```python
class MemoryType(str, Enum):
    HOT = "hot"    # 热：常驻 system_prompt
    WARM = "warm"  # 温：按需向量检索
    COLD = "cold"  # 冷：低优先级

class Memory(DbBaseModel):
    agent_id: int              # 所属 Agent
    memory_type: str           # hot/warm/cold，默认 cold
    category: str              # decision/preference/lesson/relation/event/task/other
    title: str                 # 标题/摘要
    content: str               # 详细内容
    keywords: Optional[str]    # 关键词（逗号分隔）
    importance: int            # 重要程度 1-5
    access_count: int          # 访问次数（用于自动升温判断）
    peak_tier: str             # 记忆达到过的最高层级（用于衰减后快速升温）
    vector_id: Optional[str]   # ChromaDB 向量 ID
    last_access_time: Optional[datetime]  # 最后访问时间（用于衰减判断）
```

### 记忆服务（app/services/memory_service.py）

| 方法 | 用途 |
|------|------|
| `save_memory()` | 保存记忆并自动向量化 |
| `search()` | 向量语义搜索（支持 tier/category 过滤） |
| `get_recent()` | 获取最近记忆列表 |
| `get_hot_index()` | 获取热记忆索引文本（含截断保护） |
| `build_memory_index()` | 格式化热记忆为紧凑索引（按 category 分组） |
| `truncate_index()` | 双重截断（行数+字节）+ WARNING 提示 |
| `increment_access()` | 访问计数+1，达阈值自动升温 |
| `promote_memory()` | 手动提升记忆层级 |
| `demote_memory()` | 将热记忆降级为温记忆 |
| `decay_stale_memories()` | 自动降温：hot→warm, warm→cold（基于 last_access_time + importance 加成），30s 内同 agent 不重复执行 |
| `consolidate_hot_memories()` | AI 总结整理：软删除旧热记忆 → 批量保存新热记忆 → 重新向量化 |
| `infer_tier()` | 根据 importance 推断层级（≥5→hot, 3-4→warm, 1-2→cold） |

### 记忆工具列表

| 工具 | 用途 | 说明 |
|------|------|------|
| `memory_save` | 批量保存记忆 | tier 由 importance 自动推断，也可手动指定 |
| `memory_search` | 向量搜索记忆 | 默认搜索 warm/cold，搜索命中后自动 increment_access；指定 tier='hot' 可搜索热记忆 |
| `memory_list` | 列出最近记忆 | 支持按 tier/category 过滤 |
| `memory_get` | 通过 ID 精确获取记忆完整内容 | 适用于从索引中看到感兴趣的记忆后，按 ID 获取详细信息 |
| `memory_delete` | 批量删除 | 逗号分隔 ID 列表 |

### 热记忆索引格式

```markdown
## 记忆索引

### 偏好（preference）
- [1] 用户偏好简洁回复 ★★★★★
- [5] 使用 Python 3.12+ 语法 ★★★★

### 决策（decision）
- [3] 项目使用 FastAPI 框架 ★★★★★
```

- 按 category 分组，组内按 importance 降序
- 超限时截断并追加 `> WARNING: 记忆索引过大，仅部分加载。`
- 通过 `async get_system_prompt_hint()` 注入 LLM system_prompt

### AI 总结整理（consolidate）

当 `memory_save` 保存热记忆后，满足以下任一条件时自动触发：
1. 热记忆总数超过 `consolidate_threshold`
2. 距上次整理超过 `consolidate_interval_days` 天且热记忆数量 > 0

1. 获取所有热记忆完整内容
2. 构建 prompt（合并重复、压缩冗余、保留重要信息）
3. 调用 LLM（复用 Agent 的 model/api_key/base_url）进行全量总结
4. 软删除旧热记忆 + 保存新热记忆 + 重新向量化
5. 失败时降级为截断模式，不阻塞保存

### 记忆衰减（decay）

`memory_save` 和 `memory_search` 执行前自动检查，基于 `last_access_time` 衰减（无访问记录时退化为 `modify_time`）：
- hot → warm：超过 `hot_decay_days` 天未被访问（默认 30 天）
- warm → cold：超过 `warm_decay_days` 天未被访问（默认 60 天）
- importance 加成：`(importance - 1) * 10` 天，importance=5 的 hot 记忆需 70 天才降级
- 衰减后 `access_count` 重置为 0

### 记忆节点配置（base_config）

| 配置项 | 默认值 | 说明 |
|--------|--------|------|
| `max_results` | 5 | search/list 工具最大返回数 |
| `default_importance` | 3 | save 工具默认重要程度 |
| `default_category` | `event` | save 工具默认分类 |
| `max_index_lines` | 200 | 热记忆索引最大行数 |
| `max_index_bytes` | 25000 | 热记忆索引最大字节数 |
| `auto_promote_threshold` | 5 | 自动升温阈值（access_count） |
| `consolidate_threshold` | 50 | 热记忆超过此数量触发 AI 总结 |
| `hot_decay_days` | 30 | 热记忆衰减天数（last_access_time 超过此天数降为 warm） |
| `warm_decay_days` | 60 | 温记忆衰减天数（last_access_time 超过此天数降为 cold） |
| `consolidate_interval_days` | 7 | 时间触发整理间隔（0=仅按数量触发） |

温度管理完全自动：
- **升温**：搜索命中 access_count++，达阈值自动 cold→warm→hot
- **降温**：久未访问的记忆按 last_access_time 自动 hot→warm→cold，importance 越高衰减越慢（加成 `(importance-1)*10` 天）
- **整理**：热记忆超限或距上次整理超时（consolidate_interval_days），自动 AI 总结
- 无需 LLM 手动干预
