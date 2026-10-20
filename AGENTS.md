# AI 智能体流程编排平台开发指南

基于 LangGraph 的前后端分离智能体平台。
- **后端**: Python 3.12 | FastAPI | SQLAlchemy 2.0 (aiosqlite) | Pydantic v2 | LangGraph | ChromaDB
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
npm install && npm run dev              # 端口 3010，/api 代理到 127.0.0.1:8000
npm run lint                            # ESLint 检查（自动修复）
npm run format                          # Prettier 格式化
```

- Python 严格 `>=3.12,<3.13`，默认 SQLite 零配置启动
- 修改后运行 `ruff check` + `ruff format`（后端）或 `npm run lint`（前端）
- `npm run build` 只运行 `vite build`，不含类型检查
- mypy 未安装，AGENTS.md 旧版中的 `poetry run mypy app/` 命令不可用

## 测试（pytest）

```bash
poetry run pytest            # 跑 tests/ 全部用例
poetry run pytest -v         # 显示每条用例结果
poetry run pytest tests/test_tool_approval_audit.py   # 只跑单个文件
```

- 框架：pytest 8.x + pytest-asyncio（dev 依赖组），配置在 `pyproject.toml` 的 `[tool.pytest.ini_options]`
- **隔离机制（勿破坏）**：`tests/conftest.py` 在导入任何 app 模块**之前**设置 `DATABASE_URL` 指向 `temp/pytest_test.db`——settings 是模块级单例、engine 导入时即创建，环境变量设置晚了会指向真实 `data/` 库
- **单事件循环**：`asyncio_default_test_loop_scope = "session"`——SQLAlchemy 异步引擎连接池不能跨 loop 复用，所有测试共用一个 loop（与 uvicorn 运行时一致）；新增测试直接写 `async def test_xxx()` 即可（asyncio_mode=auto），不要自行 `asyncio.run()`
- 新增测试放 `tests/test_*.py`；涉及 DB 的用例依赖 conftest 的表清理夹具（autouse），无需自行清理
- 修改后端代码后必须跑：`ruff check` + `ruff format` + `pytest`

## Git 提交规范（强制）

**所有提交必须遵守 Conventional Commits。AI 生成 commit message 时，必须严格按下列规则，不得自由发挥。**

### 格式

```
<type>(<scope>): <subject>
                                    # 空行
<body>                              # 可选
```

### type（必填，小写）

| type | 用途 | 示例 |
|------|------|------|
| `feat` | 新功能 | 新增节点 / 新增接口 |
| `fix` | **Bug 修复**（行为与预期不符） | 修复分页未过滤软删除 |
| `refactor` | 重构（不改外部行为） | 提取公共方法、改名 |
| `docs` | 文档变更 | 更新 README / AGENTS.md |
| `style` | **代码格式化**（ruff/prettier/拼写） | 格式化、改缩进 |
| `chore` | 构建/依赖/配置等杂项 | 升级依赖、改 .env.example |
| `perf` | 性能优化 | 减少 N+1 查询 |
| `test` | 测试相关 | （本项目暂无测试框架） |
| `revert` | 回滚 | revert 某次提交 |

### scope（可选，小写）

模块或范围，紧贴 type 写在括号内，如 `feat(llm):`、`fix(python):`、`docs(skill):`、`chore(db):`、`refactor(webhook):`。无明确范围时省略括号。

### subject 规则（最重要）

1. **冒号后必须有一个空格**：`feat: xxx` ✅ / `feat:xxx` ❌
2. **必须具体描述"改了什么"**，禁止"修改代码""修改样式""更新逻辑""修改 xxx"等无信息量措辞。读者不看 diff 也应能明白改动主旨。
3. **以动词开头**：新增 / 修复 / 重构 / 移除 / 优化 / 调整 / 统一 / 提取 / 升级。
4. **中文为主**（与历史风格一致），专有名词/标识符/参数名保留英文（如 `schedule_type=once`、`is_delete`）。
5. **不以句号结尾**，**不加 emoji**，首字母不大写（中文不适用）。
6. 控制在 50 个字符以内；包含多项改动时概括整体主旨，具体内容写入 body。

### body（可选）

用于解释**为什么**（动机/背景/破坏性影响），也可在一次提交包含多项改动时分条说明具体内容。不要简单复述 subject，每行不超过 72 字符。破坏性变更以 `BREAKING CHANGE:` 开头。

### 多内容提交

允许一次提交包含多个改动内容，无需按功能、修复、格式化或重构强制拆分。`type`、`scope` 和 subject 以主要改动为准，其余内容在 body 中分条说明；subject 仍须具体，禁止使用"多项修改"等无信息量措辞。

### 正反示例（取自本项目历史）

✅ **好**（具体、有信息量）：
- `feat: 定时任务支持单次执行模式（schedule_type=once，到点执行后自动禁用）`
- `fix(llm): 必需工具未调用时自动提醒重试`
- `fix(python): __save_file__ 保存后将 preview_url/mime_type 提升到顶层`
- `refactor: 将 webhook 全线重命名为 ws-gateway（WebSocket 网关）`
- `docs(skill): Seedance 技能新增 [附件文件] 路径解析规则`
- `style: ruff 格式化 app/agent_flow 目录`

❌ **坏**（必须避免）：
- `fix:修改代码` — 无信息量，看不出改了什么
- `fix:修改样式` / `fix:修改build.spec打包规则` — 描述模糊
- `fix:代码格式化` / `fix:格式化代码` — **格式化应用 `style:`**，且冒号后无空格
- `fix:xxx` — 冒号后无空格
- `统一消息 role 为 LangChain 标准` — **缺少 type 前缀**
- `feat: 修复登录bug` — 修复用 `fix:`，不是 `feat:`

### 提交前自检清单

- [ ] 有合法的 type 前缀（feat/fix/refactor/docs/style/chore/perf/revert）
- [ ] 冒号后有且仅有一个空格（`feat: xxx`）
- [ ] subject 具体，没有"修改代码/更新逻辑"这类词
- [ ] 修复 Bug 用 `fix:`，新增功能用 `feat:`，格式化用 `style:`
- [ ] 无 emoji、无末尾句号
- [ ] 多项改动可一次提交；type/scope/subject 以主要改动为准，其余内容写入 body

## 开发协作流程（个人项目裁剪版）

本项目需求**单源**（全部来自开发者本人），因此**省略需求台账 xlsx**——不要照搬企业项目的台账流程，那会产生空转文档。需求直接进 S2/S3，状态由 git 提交历史与 memory 承载。

| 阶段 | 执行者 | 动作 | 产物 |
|------|--------|------|------|
| **S0 立项定规则** | 人（可与 Agent 讨论） | 维护本文件；规则膨胀时主动瘦身 | `AGENTS.md`（+ `frontend/AGENTS.md`） |
| **S1 需求登记** | **人** | 口述/文字直发，**无台账** | — |
| **S2 方案设计** | Agent 起草 → 人拍板 | 复杂功能才写详设；拆 Wave | `docs/<主题>.md`（按需） |
| **S3 开发执行** | Dev Agent | 见下方 8 步 | 代码（**留工作区**）+ 断言脚本 + 开发报告 |
| **S4 测试验证** | Agent 自验 / QA Agent | 实跑 + 抓证据 | 提交信息中写明验证结论；重大功能另出 `docs/` 报告 |
| **S5 闭环沉淀** | Agent 起草 → **人授权提交** | 教训回写本文件 + memory + 提交 | 本文件新纪律条目、memory |

**S0 瘦身纪律**：本文件曾在 2026-06-25 主动精简 557→154 行（细节外移到 `skills/*/references/`）。判据是**规则是否需要「同时生效」**——安全红线/架构约定/唯一真源必须留在本文件一次读全；操作手册类外移。当前约 330 行属健康区间，**超过 450 行时必须外移**（细节移到 `docs/` 或技能 `references/`，本文件只留索引链接）。

**S3 开发执行 8 步**：

1. `load_skill("dev-expert")` → 按路由表识别任务类型 → 加载对应 reference
2. **通读本文件全文**（含「关键架构约定」「BaseToolKit 统一契约」等强制项）
3. `memory_search` 检索项目历史决策/教训/口径
4. `todowrite` 拆 3-7 个子任务
5. 读懂现状再动手（禁止瞎猜）
6. 改码用局部补丁，禁止无必要全文重写；**同一文件禁止并行编辑**（会互相覆盖）
7. 自验：断言脚本 / e2e 实跑 + `ruff check` + `ruff format`（前端另跑 `npm run lint`）
8. 汇报：改动文件清单 + 验证证据（命令/退出码/关键输出）+ 已知限制 + **未验证项**

**S3 出口门禁**：☐ 已通读本文件 ☐ 已 `memory_search` 历史教训 ☐ 未引入第二份逻辑（唯一真源已复用）☐ 启动期断言未被破坏（`_validate_registry_consistency`）☐ ruff / lint 通过且不高于基线告警数 ☐ 断言脚本全绿 ☐ 验证脚本未碰生产库 ☐ **未 commit**

**S3 硬纪律**：

- **确定性归代码，语义性归 LLM**：权限校验、路径校验、白名单、索引名一致性属确定性，**必须落在代码里**，严禁只写在提示词或技能文档里让 LLM 自觉。
- **验证脚本涉及 DB 写入时第一行就建临时库**（`tempfile` + `create_async_engine`），绝不 import 生产 engine 造数——`data/langgraph_agent.db` 是运行时真实数据（含技能、记忆、会话）。
- **造数必须复制真实结构**：手写字段/列序会让基线全绿而真实数据必挂。
- **改前端源码后必须 `cd frontend && npm run build`**：FastAPI 托管的是 `frontend/dist`，不 build 不生效（构建约 57s，需硬刷新）。

### 实测翻车 → 规则回写（强制闭环）

**返工是设计特性不是失败**：本项目 `feat` 后紧接 `fix` 占 44%，`fix` 占全部提交 50%。原因是需求锚定真实运行环境（真实浏览器 CDP 行为、真实 MCP 服务器响应、真实打包产物、真实模型网关超时），写代码时无法预知。

因此每次真机翻车后**必须回写本文件**，否则同一个坑会踩第二次。回写判据：

- 排查耗时 ≥40 分钟的坑 → **必须**回写
- 「静态检查全绿但实跑翻车」的坑 → **必须**回写
- 第三方库/协议的隐式行为（如 DrissionPage `listen.wait(count=1)` 返回单个 DataPacket 非列表、`console.messages` 是 drain 语义）→ **必须**回写
- 一次性笔误 → 不回写（避免本文件膨胀）

固定格式：

```markdown
- **<结论式纪律，一句话可执行>**（YYYY-MM-DD 实测修复）：
  原实现是 <X> → 真实现象是 <Y> → 根因是 <Z> → 修复口径是 <W>。
  **通用纪律：凡<同类场景>一律<正确做法>，禁止<错误做法>**
```

本文件已有的实测沉淀范例（保持此风格）：浏览器节点章节的「`listen.wait(count=1)` 返回单个 DataPacket 非列表、`console.messages` 是 drain 语义、`listen.start()` 空参数不重置过滤——三个坑均已封装在 drain/collect 辅助方法内」；工具按需加载章节的「**索引名一致性（强制）**：不一致会导致工具双份绑定且延迟域执行失败」。

**S5 另需同步**：① `memory_save`（decision=5/hot、lesson=4-5、knowledge=4/warm、event=2-3/cold），**未 commit 的改动必须在记忆标题标注「（未commit）」**；② 新增节点类型必须同步 3 处声明（见「添加新模块清单」），启动期断言会捕获脱节；③ 新增工具集必须过 `assert_consistency(kit, node)` 逐字节校验；④ 过期文档标「历史快照」而非删除。

> 完整六阶段流程、需求台账列定义、详设与实测报告模板见 dev-expert 技能 `references/dev-workflow.md` 与 `references/collaboration-templates.md`。企业交付项目（多需求来源）必须启用 S1 台账，裁剪规则见上述文档。

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
- **所有 `*_schema.py` 中的类必须继承 `BaseView`**（含 Base/Create/Update/Condition）；**优先考虑 BaseView**，当 ORM 模型无 `creator_*` / `modifier_*` 字段或与 `ChinaDateTime` 序列化格式冲突时（如时间字段需 ISO 8601 而非 `2026-09-05 10:03:28`），可继承 `BaseModel` 并自行实现 `model_to_view()` 静态方法
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
- **结构化输出**: LLM 节点 `json_output_enabled` 开启后由 `tools/structured_output.py` 注入 `structured_output` 虚拟工具；自动并入必需工具清单（门控/重试与 `required_tools` 同一套），输出变量自动追加 `structured_output`

### LLM 节点模块化（4 个子模块，单向依赖）
- `llm_factory.py`: LLM 实例创建和工具绑定
- `llm_message_builder.py`: 消息构建全流程（历史加载、恢复、压缩判断）
- `llm_stream.py`: 流式调用 + 重试 + thinking 解析
- `llm_tool_executor.py`: 工具执行（并行、人工交互、审批、截断）
- **thinking 空白防御（强制）**（2026-10-16 实测修复）：网关会下发空白占位 chunk（`reasoning_content=""`/`"\n\n"`），原「键存在」判断原样转发 → 前端流式建空思考块、空白 thinking 落库、历史回注污染 LLM 上下文。修复口径：转发（llm_stream）/落库（extract_thinking）/回注（get_history）/前端建段四层一律真值+trim 判断，块内空白保留（换行排版）。**通用纪律：凡流式 chunk 展示与落库一律真值+trim 判断，禁止键存在判断；SQLite 判空白必须 `TRIM(x, char(9)||char(10)||char(13)||' ')`——默认 TRIM 只剥空格，纯换行统计会假阴性**

### 工具按需加载（deferred_tools.py，工具膨胀场景）
- **机制/策略分离**: `ToolProvider` 协议（list_meta/search/materialize）对所有工具族通用；策略由三层优先级决定：节点 base_config.lazy_load > **handler.defer_policy** > 默认 "bind"
- **族策略（defer_policy）**: 内置族默认 `"bind"`（shell/ssh/memory/todo/agenda/api/skill/sub_agent/question 等——高频核心能力，bind 的 token 成本低、调用零往返）；**mcp/knowledge/browser 为 `"auto"`**（外部服务器与多节点弹性膨胀源，参与阈值判定）。auto 族想强制全量配 lazy_load=false；bind 族想延迟配 lazy_load=true
- **auto 阈值决策**: 仅 defer_policy=="auto" 的族参与计数——延迟候选 > `DEFERRED_TOOL_THRESHOLD`(20) 启用延迟域，≤20 回退全量注册（阈值不被内置工具数量污染）；节点配置 true/false 强制覆盖。判定数据零建连（MCP 走 McpToolCache）。**候选数按唯一工具计**（provider 粒度见下，同一 server 被多节点引用只算一份）
- **provider 粒度（强制）**: 一 provider = 一个「工具列表」，**不等于一个节点**——MCP 是「一 server 一 provider」（`provider_key=mcp__{server_name}`，与工具名前缀 `mcp__{server_name}__` 同族、共用 `mcp__` 命名空间（provider key 无收尾 `__`，工具名有）；无名回退 `mcp__{server_id}`，重名加 `#{id}` 消歧并告警）；knowledge/browser 是「一节点一 provider」（基类默认 `{类名}:{node_key}`）。登记入口统一为 `handler.get_providers(node, writer, session_id)`（返回列表），基类默认实现 = `[get_provider(node)]`。同 `provider_key` 多节点引用由登记方合并，族可实现 `absorb(node, cfg)` 合并语义（MCP：审批规则**并集**，任一引用节点要求审批即审批，归因到命中的 node_key）
- **延迟域审批依赖透传（强制）**: `get_providers`/`get_provider` 的 `writer`/`session_id` 必须由调用方透传已注入实例值；provider 内部构造工具闭包时**禁止 new 空的 handler**（`McpNodeHandler()` 的 `_session_id=0`/`_writer=None` 会被审批钩子首行 `return None` 静默放行，`tool_execute` 即审批后门）
- **lazy_load 冲突取绑定优先**: 某 server 被任一 `lazy_load=false` 节点引用时，该 server 整体不进延迟域（宁可工具留在 bound，也不能因 provider 合并从模型可见工具里消失）
- **索引名一致性（强制）**: 延迟域索引的工具名必须与 bound 路径逐字节一致（MCP 需带 `mcp__{server_name}__` 前缀，同 mcp_manager）——bound 剔除与 materialize 定位都按名匹配，不一致会导致工具双份绑定且延迟域执行失败
- **基类默认实现**: `BaseNodeHandler.get_provider` = get_tool_info（索引，零建连）+ get_tool（物化，取工具对象，按 node 缓存）；物化需建连/审批的族重写之（如 `McpToolProvider`），需一节点多 provider 的族改重写 `get_providers`
- **三元元工具**: `tool_info_list`（总览）/ `tool_search_tool_regex`（查 schema）/ `tool_execute`（执行，arguments 为 JSON 字符串）；物化在 tool_execute coroutine 内部，`handle_tool_calls` 零改动
- **前缀缓存不变量（强制）**: 延迟域决策在 setup_tool_handlers 内一次性冻结；三元工具 description 纯静态；物化只进执行器永不回写 bound tools；违反即破坏 LLM 前缀缓存
- **get_tool_info 双职责约束**: 兼任前端展示与延迟域索引，**必须保持同步、零建连、纯静态**；需要动态索引的族重写 get_provider 返回自定义 provider
- **回退语义**: auto 判定未过阈值时全量注册（lazy 节点工具已在全量路径收集，无需重载）；索引全空 + 强制 lazy 时告警

### agent_flow/tools/ 目录（LLM 工具实现）
- **定位**: 存放以 `StructuredTool` 形式提供给 LLM 的工具实现，handler 只负责注册与依赖注入；与 node_handlers 内拆分子模块（llm_*）不同——tools 是独立工具实体，handler 是图的编排单元
- **不参与自动扫描**：`tools/` 不在 loader 三个扫描目录内，全部显式 import
- **现有成员**: `common.py`（文件类工具共享原语：路径校验/编码读取/大小上限）、`base_abs_tool.py`（BaseToolKit 统一契约，见下）、`file_read.py`（FileReadService：媒体多模态注入 + xlsx/docx 转文本 + 行/字符分段读取）、`structured_output.py`（StructuredOutputService：JSON 结构化输出虚拟工具，字段树递归构建 Pydantic 模型）、`{shell,file,ssh,browser,todo,agenda,question,knowledge,memory}_toolkit.py`（九个节点工具集）
- **依赖方向（强制单向）**: `tools/* → app/utils/*`，`node_handlers/* → tools/*`，禁止反向或 tools 之间互引
- **新工具入目录规则**: 一个工具集一个文件（继承 `BaseToolKit`）；共享原语进 `common.py`；工具返回模型友好的 dict 协议（error/truncated_hint/media_type 等），不返回裸字符串
- **编码探测**: `common.detect_and_read` 探测顺序 UTF-16 BOM（FF FE/FE FF，PowerShell 重定向产物）→ utf-8-sig → GBK；二进制拦截对带 UTF-16 BOM 的文本白名单放行
- **Windows 子进程注意**: 工具内如需创建子进程，必须带 `_SUBPROCESS_WINDOW_FLAGS`（shell_handler.py 定义），否则 GUI/托盘宿主下每次调用弹出黑框

### BaseToolKit 统一契约（tools/base_abs_tool.py，强制）
- **两个入口**：`build_tools()`（无参签名，运行时依赖一律构造函数注入）+ `tool_info(node)`（classmethod，零建连元数据 `[{name, description}]`）；`tool_names(node)` 从 tool_info 派生
- **tool_info 是 classmethod**：延迟域索引与前端展示都是零建连场景，不为拿元数据构造 Kit 实例
- **描述单源（强制）**：tool_info 的 description 必须与 build_tools 产出的 StructuredTool description **逐字节一致**——从同一模块级常量/构建函数派生，禁止手抄短句；含运行时值的描述用 `_build_xxx_description(cfg)` 构建函数（tool_info 与 build_tools 共用同一函数）；`assert_consistency(kit, node)` 提供逐字节校验（e2e 已覆盖全部 9 个 Kit）
- **handler 收敛**：`get_tool` = 组装 Kit（注入回调）+ `build_tools()`；`get_tool_info` = 一行委托 `XxxToolKit.tool_info(node)`
- **豁免**：动态预设工具（python/api/sub_agent/flow_tool，name/schema 运行时生成）留 handler 构建器；skill（运行时拼技能清单描述）、mcp（DB 缓存即源）不走 tool_info 单源；FileReadService 单工具服务保留 `build_tool` 单数形态（已补 tool_info）

### 四节点工具集（tools/*_toolkit.py）
- shell（5 工具 + `_background_tasks` 后台任务表，REST API 与 Agent 共享）/ file（6 文件工具）/ ssh（5 工具）/ browser（14 工具 + `browser_session_manager` 会话单例，Flow 与 Agent 共享）
- **两个模块级单例必须保持**：`_background_tasks`、`browser_session_manager`——REST API 直接 import，改名即断
- **审批回调注入**：tools 禁止 import node_handlers，审批以 `approval_cb(tool_name, tool_args, content_for_pattern)` 回调注入（三节点统一闭包模式）；未配置白名单/正则时零开销
- **浏览器节点（DrissionPage 4.1.1.4，CDP）**：双形态——Flow 主链单步动作（flow_action + flow_args）或 Agent 14 工具集；会话按 flow_id 缓存，空闲 600s 自动回收；**Flow 模式连接配置（connect_mode/browser_path/user_data_path/headless/debug_port）仅 flow_action="start" 节点生效**（start 是唯一建会话入口 get_or_create，非 start 动作走 reuse、缺会话报错引导，不隐式首建；Agent 工具路径不受影响，仍幂等建会话）；launch 模式 `set_local_port(socket 探测)` + 显式数据目录（`auto_port()` 与 `set_user_data_path` 互斥）；`run_js` 只认 `return` 语句形态；`ele()` 裸字符串按 DP 自有语法解释（纯字符串=文本匹配、#开头=id），标准 CSS 必须带 `css:` 前缀（`_normalize_locator` 自动归一化兜底）；snapshot 走 CDP `Accessibility.getFullAXTree` 真 a11y 树，uid 锚定 `data-agent-ref` 属性；console/network 调试收集采用 chrome-devtools-mcp 同款「常驻收集+按导航分段」（建会话即自动开启，network 默认 Document/XHR/Fetch，缓冲保留最近 3 次导航，DP 无公开导航事件用惰性 tab.url 比对封段；`listen.wait(count=1)` 返回单个 DataPacket 非列表、`console.messages` 是 drain 语义、`listen.start()` 空参数不重置过滤——三个坑均已封装在 drain/collect 辅助方法内）
- **浏览器动作元数据后端单源**：Flow 模式动作下拉 label（`FLOW_ACTION_LABELS`）+ 参数 schema（`FLOW_ACTION_PARAMS`）唯一事实源在 `BrowserNodeHandler`，经 `GET /api/flow/browser-actions` 下发（`flow_api.py` 注册在 `/get/{id}/...` 动态路由之前）；前端 `BrowserConfig.vue` onMounted 动态拉取渲染（失败兜底=不渲染参数表单），**禁止在前端再硬编码参数副本**。新增动作只改后端两表 + 重跑 ruff

### 工具输出截断
- 入口: `smart_truncate_output(result, prefix="tool_output")`，返回 JSON 字符串
- 阈值: `.env` 中 `TOOL_OUTPUT_MAX_LINES`/`TOOL_OUTPUT_MAX_BYTES`
- 所有工具（Shell、Sub-Agent、MCP、Knowledge、Memory 等）在构造 `ToolMessage` 前统一截断

### 沙箱（软件层，2026-10-20 新增）
- **唯一真源（强制）**：
  - opaque 策略解析只在 `tools/sandbox_policy.py`（`resolve_opaque_policy`：节点显式配置 > auto 按域分流 Agent=ask / Flow=off）
  - 命令解析只在 `tools/shell_command_parser.py`（`scan_command`：cmd/sh 双方言尽力解析，9 类 opaque 分类对齐 opencode）
  - 命令三态检查只在 `tools/shell_toolkit.check_command`（valid/blocked/opaque；`validate_command` 为二态兼容包装，opaque 视为放行——旧调用方语义不变）
  - 写路径三态检查只在 `tools/common.check_writable_path`（allowed/blocked/outside；`validate_writable_path` 为二态兼容包装）
- **策略配置**：LLM 节点 `sandbox_opaque_policy`（auto/ask/deny/off，默认 auto），经 `setup_tool_handlers` 注入 `handler._sandbox_policy`（仿 `_media_caps` 模式），shell/ssh/file 三 toolkit 闭包消费；opaque 命令与白名单外写入按策略 ask（走现有审批回调）/deny（拒绝+引导文案）/off（不拦截）
- **「不拦截」语义（重要）**：auto 在 Flow 域与显式 off 都只是**不弹审批直接执行** opaque 命令/白名单外写入，黑名单与硬拦截仍然生效——「放行」仅指跳过审批确认，不是跳过安全检查；Flow 定时/无人值守场景建议保持 auto，Flow 中 LLM 动态执行命令的建议显式改 ask
- **可写白名单**：默认 = 会话工作目录 + Agent 工作目录 + temp 目录；追加项走 `.env` 的 `SANDBOX_WRITABLE_ROOTS`（逗号/分号分隔，相对路径基于项目根）
- **硬拦截（不分域、不可配置，任何策略下都拒绝）**：系统关键路径（FORBIDDEN_PATH_PATTERNS）、data/ 数据目录、家目录敏感子目录（`.ssh/.aws/.docker/.gnupg/.netrc/.kube/.config/gcloud`，对齐 openclaw BLOCKED_HOME_SUBPATHS）
- **python 沙箱 open 收口**：`python_handler._sandbox_open` 替换裸 `open`——写模式（w/a/x/+）路径必须落在可写白名单内，读模式不限制；ALLOWED_MODULES 单一事实源不动
- **黑名单始终生效**：DANGEROUS_PATTERNS 整串正则兜底扫描对 scanned 与 opaque 一视同仁，危险命令即使解析失败也先硬拦截（优先于 opaque 判定）
- **Flow 直跑语义**：Flow 主图 shell 节点直跑路径默认不拦截（直跑命令是用户在画布上静态编写、可见可审，opaque 风险源是 LLM 动态生成的命令）；LLM 节点显式配置 ask/deny 可对 Flow 收紧

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

密码来源: DB（`global_config.login_password_hash`）> `.env`（`LOGIN_PASSWORD`，仅 DB 未配置时兜底）。登录保护必须启用、不可关闭：`/setup` 初始化时用户名和密码必填，`/settings` 仅可修改、置空报"登录保护不可关闭"（`global_config_service.update_config`）。

Session: cookie `auth_session`，httponly + samesite=lax，7 天有效，内容为 `base64(timestamp:hmac_sha256(timestamp, password_hash))`。

豁免路径: `/api/auth/*`、`/api/config/check`、`/api/config/init`、`/api/config/providers`、`/api/health`

## 三层记忆架构（概要）

记忆节点以 Agent 为维度管理，三层自动升降级:

| 层级 | 加载方式 | 升温 | 降温 |
|------|---------|------|------|
| hot | 按分类拆分注入：`system_prompt_categories`（默认 profile/preference/instruction）随 system_prompt，其余分类随消息层单一记忆索引（`get_memory_reminder`） | — | 25天未访问 → warm |
| warm | `memory_search` 向量检索；最新若干条以 `\| 温` 标记行并入消息层记忆索引（带预览，热前温后） | access_count≥5 → hot | 35天未访问 → cold |
| cold | 同 warm | access_count≥5 → warm | — |

热记忆上限（默认25条）采用「预警+拒绝」：保存后达 20~25 条返回 warning，本次保存将导致超过 25 条时整批拒绝（`success=False` + `action=consolidate`）——LLM 收到提示后调 `memory_consolidate` 自主整理（对齐 Letta 的 agent 自管记忆模式，无隐藏内部 LLM 调用）：先 `memory_list(tier=hot)` 看全量，再逐条输出 keep/demote/delete/merge 决策；demote 降为温记忆（信息不丢）、merge 重写为热（importance/keywords 继承原记忆），任一 id 无效整批拒绝。importance 越高衰减越慢（加成 `(importance-1)*1` 天）。热索引注入位置遵循"静态进 system_prompt、动态进消息层"原则，动态记忆变化只失效 reminder 之后的尾部前缀缓存；消息层索引头部含记忆总数与展示计数、节标题说明展示规则（索引≠全量）；`last_access_time` 刷新带 60s 防抖（`MEMORY_ACCESS_REFRESH_INTERVAL_SECONDS`）。

详见 `app/models/memory.py`、`app/services/memory_service.py`、`app/agent_flow/node_handlers/memory_handler.py`。

## 添加新模块清单

### 后端新 CRUD 模块
1. `app/models/<resource>.py` — 继承 `DbBaseModel`，禁止外键，每列加 `comment`
2. `app/schemas/<resource>_schema.py` — 继承 `BaseView` → Create/Update/Condition
3. `app/services/<resource>_service.py` — 继承 `BaseService`，导出 `xxx_service` 单例
4. `app/api/<resource>_api.py` — 继承 `BaseApi`，导出 `router`

### 后端新节点类型

新增节点类型需要在 **3 处**同步声明（启动期断言捕获脱节）：

1. `app/models/flow_node.py` — `NodeType` 枚举添加一行
2. `app/constants/node_types.py` — `NODE_REGISTRY` 添加对应 `NodeMeta` 条目
   - **必填字段**：`label` / `source` / `target` / `flow` / `agent` / `agent_tool` / `agent_unique` / `tool_only`
   - 8 个白名单/集合（`BASIC_NODE_TYPES` / `AGENT_ALLOWED_NODE_TYPES` / `AGENT_TOOL_NODE_TYPES` / `AGENT_UNIQUE_NODE_TYPES` / `TOOL_ONLY_NODE_TYPES` / `NODE_SOURCE_HANDLES` / `NODE_TARGET_HANDLES` / `NODE_TYPE_LABELS`）**自动派生**，无需手写
3. `app/agent_flow/node_handlers/<type>_handler.py` — `@NodeHandlerRegistry.register("type")` + `allow_multiple_tool_connections()`（仅工具节点需要）

启动期校验：导入 `app.models.flow_node` 时触发 `_validate_registry_consistency()`，捕获 NodeType ↔ NODE_REGISTRY 任何一边新增/遗漏，立即 `RuntimeError`。

### 前端新节点类型（组件自动发现）
1. `frontend/src/types/flow.ts` — 添加 `CardNodeType` 联合类型成员
2. `frontend/src/components/FlowEditor/nodeRegistry.ts` — 添加 entry
3. `frontend/src/components/FlowEditor/nodes/<Type>Node.vue` — 放入目录即自动注册
4. `frontend/src/components/FlowEditor/config/<Type>Config.vue` — 放入目录即自动注册

## 参考

- 前端详细规范见 `frontend/AGENTS.md`
- 完整环境变量见 `.env.example`
