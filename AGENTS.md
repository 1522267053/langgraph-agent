# AI 智能体流程编排平台开发指南

基于 LangGraph 的前后端分离智能体平台。
- **后端**: Python 3.12 | FastAPI | SQLAlchemy 2.0 (aiomysql/aiosqlite) | Pydantic v2 | LangGraph | ChromaDB
- **前端**: Vue 3 | TypeScript | Vite | Pinia | Vue Flow | Element Plus

## 构建与运行

```bash
# 后端（.venv/ 虚拟环境，poetry.toml in-project=true）
poetry install                          # 腾讯镜像源 + USTC 补充源
poetry run uvicorn main:app --reload    # 端口 8000，启动后自动打开浏览器
poetry run ruff format app/             # 格式化（ruff 0.15.x，无自定义 [tool.ruff] 配置）
poetry run ruff check app/ --fix        # 检查并修复

# 前端
cd frontend
npm install && npm run dev              # 端口 3000，/api 代理到 127.0.0.1:8000
npm run lint                            # ESLint 检查（自动修复）
npm run format                          # Prettier 格式化
```

- Python 严格 `>=3.12,<3.13`，默认 SQLite 零配置启动
- 无测试框架，修改后运行 `ruff check` + `ruff format`（后端）或 `npm run lint`（前端）
- `npm run build` 只运行 `vite build`，不含类型检查
- mypy 未安装，AGENTS.md 旧版中的 `poetry run mypy app/` 命令不可用

## 自动注册机制（核心架构）

**这是整个项目最重要的约定**：新增模块放入对应目录即可自动加载，无需手动 import。

启动顺序（`main.py` lifespan）:
1. `load_all_models()` → 扫描 `app/models/*.py`，注册 SQLAlchemy metadata
2. `load_all_handlers()` → 扫描 `app/agent_flow/node_handlers/*.py`，触发 `@NodeHandlerRegistry.register` 装饰器
3. `load_all_providers()` → 扫描 `app/agent_flow/ai_provider/*.py`，触发 `@AIProviderRegistry.register` 装饰器
4. `init_db()` → `scheduler_service.start()` → yield → 清理

路由注册在模块级（`create_app` 时）: `register_all_routers(app)` 扫描 `app/api/*.py`，每个文件必须导出 `router` 变量。

打包构建: `poetry run python scripts/build.py <版本号>` 一键完成 Nuitka 编译 + PyInstaller 打包（支持 `--skip-nuitka` 跳过编译步骤）。

打包环境（PyInstaller/Nuitka）通过 `scripts/generate_static_imports.py` 生成 `_static_imports.py` 替代文件系统扫描。

## 关键架构约定

### 数据层
- **禁止数据库外键**，关联通过应用层整数字段（如 `server_id`）处理
- **软删除**: `is_delete` 字段，所有 SELECT 自动过滤 `is_delete=1`。覆盖: `stmt.execution_options(include_deleted=True)`
- **枚举存储**: 使用 `.value`（如 `default=FlowStatus.DRAFT.value`）
- **`create_time` default**: 使用 `datetime.now`（函数引用，非调用）
- **`Flow.nodes`/`Flow.edges`**: 是 `ClassVar`，非 ORM relationship，由 Service 手动查询赋值

### Schema 层
- **所有 `*_schema.py` 中的类必须继承 `BaseView`**（含 Base/Create/Update/Condition）
- `BaseView` 不含 `is_delete` 字段
- `BaseService.update()` 使用 `exclude_unset=True`，**无法将字段更新为 `None`**
- 响应格式: `ApiResponse` 包装（code=1 成功，code=0 失败），HTTP 状态码始终 200

### 数据库会话
- `get_db()` 依赖注入: yield session，异常 rollback，**不自动 commit**
- Service 层负责 commit（`create/update/delete` 内部调用 `await db.commit()`）
- SSE 流式方法使用 `AsyncSessionLocal()` 自建会话
- **不要**将 `db.commit()` 改为 `db.flush()`

### 流程执行引擎
- **工具边**: `source_handle == "tools"` 的边不加入 LangGraph 图，仅用于工具发现
- **MCP 节点**: 不加入执行图，仅作为工具提供者
- **条件分支**: `ConditionNodeHandler` 设置 `state.variables["_condition_branch"]` = `"true"` / `"false"`
- **子流程 key**: Card 节点用 `"__"` 分隔（如 `"card_key__sub_node_key"`）
- **循环保护**: `FlowState.iteration_count` 超过 `max_iterations`（默认100）时终止
- **对话历史双写**: LangGraph checkpoint + DB 表，删除/修改 DB 消息后**必须同步清理 checkpoint**（`_cleanup_thread_checkpoint(session_id)`），否则旧消息会回填
- **system_prompt**: 不存储在 messages/checkpoint/DB 中，LLM 调用时临时拼接
- **变量解析优先级**: context（input_variables 映射）> input（state.input_data）> variables（state.variables）
- **边路由互斥分支**: 同一条件路由器的多个出边为互斥分支，逐条 `add_edge` 而非创建 `NamedBarrierValue` 屏障
- **循环嵌套禁止**: 循环内不能嵌套循环（直接或通过卡片间接），前后端均校验
- **子图**: `BaseSubgraphBuilder` 基类，`wire_edges(iteration_guard=False)` 禁用循环保护

### 节点处理器
- LLM 节点使用 `@NodeHandlerRegistry.register_factory("llm")` 工厂注册
- 其他节点使用 `@NodeHandlerRegistry.register("type")` 直接注册
- `allow_multiple_tool_connections()` 返回 `False` 的类型（`skill`、`memory`）同一 LLM 只能连接一个实例
- `get_system_prompt_hint` 支持同步和异步两种模式
- `get_default_config()` 从 `ConfigClass` 自动生成默认配置；`get_config_schema()` 提取 JSON Schema 字段描述
- **Pydantic v2 注意**: `default_factory` 字段在 `model_json_schema()` 中不含 `"default"` 键，`_schema_from_pydantic` 已补偿处理

### LLM 节点模块化（4 个子模块，单向依赖）
- `llm_factory.py`: LLM 实例创建和工具绑定
- `llm_message_builder.py`: 消息构建全流程（历史加载、恢复、压缩判断）
- `llm_stream.py`: 流式调用 + 重试 + thinking 解析
- `llm_tool_executor.py`: 工具执行（并行、人工交互、审批、截断）

### 工具输出截断
- 入口: `smart_truncate_output(result, prefix="tool_output")`，返回 JSON 字符串
- 阈值: `.env` 中 `TOOL_OUTPUT_MAX_LINES`/`TOOL_OUTPUT_MAX_BYTES`
- 所有工具（Shell、Sub-Agent、MCP、Knowledge、Memory 等）在构造 `ToolMessage` 前统一截断

### 前端关键约定
- **路径别名**: `@/` → `src/`
- **路由**: hash 模式（`createWebHashHistory`），全部懒加载
- **Vue Flow 节点**: 必须用 `markRaw()` 包装
- **Element Plus 组件**: `unplugin-vue-components` 按需自动导入，**不需要手动 import**
- **Element Plus 图标**: 每个组件文件显式 import，不在 `main.ts` 全局注册
- **SSE**: 原生 `fetch` + `ReadableStream`（POST-based），非 EventSource
- **API 属性**: 使用 snake_case（`flow_type`、`node_key`）
- **文件选择**: 必须使用 `FilePickerDialog`，禁止 `FileUpload`
- **Handle 颜色**: green=输入, blue=输出, orange=工具, red=假分支
- **Prettier**: 无分号 | 单引号 | 2空格缩进 | 100字符行宽 | 无 trailing 逗号
- **ESLint**: `no-unused-vars` error（`_` 前缀忽略）、`no-explicit-any` warn

## 认证系统

基于 session cookie 的简单密码认证（非 JWT），仅拦截 `/api/*` 路径。

密码来源: DB（`global_config.login_password_hash`）> `.env`（`LOGIN_PASSWORD`）

Session: cookie `auth_session`，httponly + samesite=lax，7 天有效，内容为 `base64(timestamp:hmac_sha256(timestamp, password_hash))`。

豁免路径: `/api/auth/*`、`/api/config/check`、`/api/config/init`、`/api/config/providers`、`/api/health`

## 三层记忆架构（概要）

记忆节点以 Agent 为维度管理，三层自动升降级:

| 层级 | 加载方式 | 升温 | 降温 |
|------|---------|------|------|
| hot | 每次对话注入 system_prompt（紧凑索引） | — | 30天未访问 → warm |
| warm | `memory_search` 向量检索 | access_count≥5 → hot | 60天未访问 → cold |
| cold | 同 warm | access_count≥5 → warm | — |

热记忆超限（默认50条）自动 AI 总结整理。importance 越高衰减越慢（加成 `(importance-1)*10` 天）。

详见 `app/models/memory.py`、`app/services/memory_service.py`、`app/agent_flow/node_handlers/memory_handler.py`。

## 添加新模块清单

### 后端新 CRUD 模块
1. `app/models/<resource>.py` — 继承 `DbBaseModel`，禁止外键，每列加 `comment`
2. `app/schemas/<resource>_schema.py` — 继承 `BaseView` → Create/Update/Condition
3. `app/services/<resource>_service.py` — 继承 `BaseService`，导出 `xxx_service` 单例
4. `app/api/<resource>_api.py` — 继承 `BaseApi`，导出 `router`

### 后端新节点类型
1. `app/models/flow_node.py` — 添加 `NodeType` 枚举值 + `BASIC_NODE_TYPES`
2. `app/constants/node_types.py` — 添加中文标签
3. `app/agent_flow/node_handlers/<type>_handler.py` — `@NodeHandlerRegistry.register("type")`

### 前端新节点类型（组件自动发现）
1. `frontend/src/types/flow.ts` — 添加 `CardNodeType` 联合类型成员
2. `frontend/src/components/FlowEditor/nodeRegistry.ts` — 添加 entry
3. `frontend/src/components/FlowEditor/nodes/<Type>Node.vue` — 放入目录即自动注册
4. `frontend/src/components/FlowEditor/config/<Type>Config.vue` — 放入目录即自动注册

## 参考

- 前端详细规范见 `frontend/AGENTS.md`
- 完整环境变量见 `.env.example`
