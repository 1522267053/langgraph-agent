---
name: agent-manager
description: |
  创建、管理和查询智能体(Agent)与工作流(Workflow)。适用场景：
  (1) 用户要求创建新的智能体或工作流
  (2) 用户想查看、修改或删除已有的智能体/工作流
  (3) 用户想了解可用的节点类型和配置

  **重要**：创建节点前必须先查询节点类型的 config-schema，了解必填/可选字段和默认值。
  绝不能凭记忆或猜测来构造节点配置，不同版本的字段要求可能不同。
---

# Agent & Workflow Manager

服务器：`http://127.0.0.1:8000`

## 核心规则（必须遵守）

1. **创建/更新节点前，必须先查询 config-schema**：`GET /api/ai/flow/node-types/{type}/config-schema`
2. **`base_config` 字段级合并**：只传入要修改的字段即可，未传的字段保留原值（建议更新前 `GET /api/ai/flow/{id}/detail` 了解当前配置）
3. **`output_variables` 必须是 JSON 对象数组** `[{"name","source","type"}]`，不能是字符串，否则执行时报错
4. **Python 节点输出双层包装**：返回值被包在 `{stdout, stderr, result, success}` 中，引用路径为 `nodes.<key>.result.<field>` 而非 `nodes.<key>.<field>`
5. **创建完毕后必须测试**：`POST /api/execution/stream/{id}` 验证流程能正常执行，确认输出符合预期后再告知用户完成
6. **LLM 的 provider/model/api_key/base_url 留空 `""`**：系统自动注入全局默认值

## API 速查

### 流程 CRUD

| 操作 | 接口 |
|------|------|
| 列表 | `GET /api/ai/flow/list[?flow_type=agent\|flow]` |
| 详情 | `GET /api/ai/flow/{id}/detail` |
| 创建 | `POST /api/ai/flow/create` |
| 删除 | `POST /api/ai/flow/delete/{id}` |
| 更新元数据 | `POST /api/flow/update` |

### 节点操作

| 操作 | 接口 |
|------|------|
| 批量创建 | `POST /api/ai/flow/{id}/nodes/batch` |
| 批量更新配置 | `POST /api/ai/flow/{id}/nodes/batch/config` |
| 批量删除 | `POST /api/ai/flow/{id}/nodes/batch/delete` |

### 边操作

| 操作 | 接口 |
|------|------|
| 批量创建 | `POST /api/ai/flow/{id}/edges/batch` |
| 批量删除 | `POST /api/ai/flow/{id}/edges/batch/delete` |

### 工具信息

| 操作 | 接口 |
|------|------|
| 查询LLM已连接工具 | `GET /api/ai/flow/{flow_id}/node/{node_key}/connected-tools` |

调用各工具节点的get_tool_info获取名称和描述（不实际执行），配置required_tools时先查此接口获取准确工具名。

### 执行

> **说明**：以下接口中的 `{flow_id}` 和 `{agent_id}` 均为 `POST /api/ai/flow/create` 返回的 `id`。

#### 1. 流程执行（Flow）

```
POST /api/execution/stream/{flow_id}
```

```json
{
  "input_data": {"message": "hello"},
  "files": null
}
```

- `input_data`：可选，流程输入参数（需与 flow 的 `input_schema` 匹配）
- `files`：可选，附件文件信息列表

SSE 事件：`flow_start` → `node_start` → `node_thinking/node_content` → `node_done` → ... → `flow_done/error`

#### 2. 智能体对话（Agent）

**创建会话**：

```
POST /api/agent/{agent_id}/sessions
```

无请求体，返回会话信息，其中 `id` 字段为会话 ID，后续对话需使用该 ID。

**发送消息**：

```
POST /api/agent/{agent_id}/sessions/{session_id}/chat
```

```json
{
  "content": "你好",
  "params": {}
}
```

- `content`：必填，用户消息内容
- `params`：可选，扩展参数（含文件字段等）

SSE 事件：`flow_start` → `node_start` → `node_thinking/node_content` → `node_done` → ... → `flow_done/waiting_human/error`

#### 3. 人工输入恢复执行

```
POST /api/execution/human-input-stream/{execution_id}
```

```json
{
  "execution_id": 123,
  "input": "用户回复内容"
}
```

- `input`：必填，用户提交的人工输入（不能为空，不超过 10000 字符）
- `execution_id`：可选（已在 URL 路径中）

## 智能体 vs 工作流

| | 智能体 (`agent`) | 工作流 (`flow`) |
|--|-----------------|----------------|
| 结构 | start→llm→end + 工具节点 | 任意 DAG |
| LLM | 仅限 1 个 | 不限 |
| 支持节点 | start/end/condition/intent_router/llm + 工具节点(mcp/knowledge/skill/python/shell/memory/todo/agenda/api/sub_agent) | 所有节点类型 |

> **媒体文件生成**: python 工具的 main() 返回 `{"__save_file__": true, "content_base64": ..., "mime_type": ...}` 可自动保存并在聊天中预览；api 工具设置 download_file=true 下载二进制文件同样自动预览。详见 references/api.md。

## 创建流程（完整步骤）

```
1. POST /api/ai/flow/create          # 创建（可同时设 input_schema、suggested_prompts）
2. POST /api/ai/flow/{id}/nodes/batch    # 批量创建节点（含 LLM 和工具节点）
3. POST /api/ai/flow/{id}/edges/batch    # 批量创建边（工具边连到 LLM）
4. GET  /api/ai/flow/{id}/node/{llm_key}/connected-tools  # 查已连接的工具名（可选，配 required_tools 用）
5. POST /api/ai/flow/{id}/nodes/batch/config  # 修正 end 的 output_variables + LLM 的 required_tools
6. POST /api/execution/stream/{id}       # 执行
```

### 创建流程示例

```json
POST /api/ai/flow/create
{
  "name": "工作流名称",
  "flow_type": "flow",
  "input_schema": {
    "fields": [
      {"name": "message", "type": "string", "description": "用户消息", "required": true}
    ]
  },
  "suggested_prompts": ["帮我分析数据", "写一个报告"]
}
```

`input_schema` 类型可选：`string / number / boolean / object / array / file_list`

`suggested_prompts` 为字符串数组，仅 Agent（`flow_type=agent`）有效。用户进入对话页且无消息时，会以全屏欢迎页展示这些提示词，点击即可自动发送。应根据 Agent 的实际能力提供 3~8 条具体、可操作的提示，避免空泛措辞。可通过 `POST /api/flow/update` 更新此字段。

### 创建节点示例

```json
POST /api/ai/flow/{id}/nodes/batch
{
  "nodes": [
    {"node_type": "start", "node_key": "start", "node_name": "开始", "position_x": 100, "position_y": 200},
    {"node_type": "llm", "node_key": "llm", "node_name": "AI助手", "position_x": 350, "position_y": 200,
     "base_config": {"provider": "", "model": "", "api_key": "", "system_prompt": "你是...", "user_prompt": "{{input.message}}"}},
    {"node_type": "end", "node_key": "end", "node_name": "结束", "position_x": 600, "position_y": 200}
  ]
}
```

每个节点必填 `position_x` / `position_y`。建议：start(100,200)、llm(350,200)、end(600,200)

### 创建边示例

```json
POST /api/ai/flow/{id}/edges/batch
{
  "edges": [
    {"source_node_key": "start", "target_node_key": "llm", "source_handle": "default", "target_handle": "default"},
    {"source_node_key": "llm", "target_node_key": "end", "source_handle": "default", "target_handle": "default"}
  ]
}
```

### 工具节点连接到 LLM

工具节点通过 `tools→tools` 边连接：

```json
{"source_node_key": "python_1", "target_node_key": "llm", "source_handle": "tools", "target_handle": "tools"}
```

### 执行前置三条件

流程可执行必须满足：
1. **Flow 级 `input_schema`**：创建时设或后续 `POST /api/flow/update` 补
2. **End 节点 `output_variables`**：至少一个 `[{"name":"result","source":"nodes.llm.result","type":"string"}]`
3. **LLM 节点 `user_prompt`**：支持 `{{variable}}` 模板，否则 LLM 收不到消息

## 边连接规则

| source_handle | target_handle | 用途 |
|---|---|---|
| `default` | `default` | 标准数据流 |
| `tools` | `tools` | 工具边（目标必须是 LLM） |
| `true` / `false` | `default` | condition 分支 |
| `<intent_key>` | `default` | intent_router 分支 |

### 节点连接方式矩阵

| 节点 | default 边 | tools 边 | 说明 |
|------|:-:|:-:|------|
| start | ✅输出 | ❌ | 数据源 |
| end | ✅输入 | ❌ | 数据汇聚 |
| llm | ✅双向 | ✅接收 | **唯一可接收 tools 的节点** |
| python/api/knowledge | ✅双向 | ✅ | 数据+工具两种模式 |
| condition/loop/card/human/intent_router | ✅双向 | ❌ | 分支/子流程控制 |
| **shell/mcp/skill/memory/todo/agenda/sub_agent** | ❌ | ✅**仅输出** | **纯工具节点，禁止 default 边** |

## 变量引用路径

| 路径 | 含义 |
|------|------|
| `input.<name>` | 用户输入参数 |
| `nodes.<key>.<var>` | 节点输出变量 |
| `nodes.<key>.result.<field>` | **Python 节点**的自定义字段（注意多一层 `.result`） |
| `nodes.<loop>.input_<field>` | Loop 传入子节点的参数 |
| `nodes.<loop>.res` | Loop 聚合所有迭代的子 End 输出（数组） |
| `variables.loop_index` | Loop 当前迭代序号（0起始） |
| `variables.loop_count` | Loop 总迭代次数 |

❌ `start.message`（错误）→ ✅ `input.message`（正确）

### 变量引用语法区分

| 语法 | 适用场景 | 示例 |
|------|---------|------|
| `{{variable}}` 双花括号 | LLM 的 `system_prompt`/`user_prompt`、API 的 `api_url`/`body` 等模板文本 | `"user_prompt": "{{input.message}}"` |
| 裸路径（不加花括号） | `output_variables.source`、`input_variables.source`、`condition.rules.variable`、`loop.input_mappings.source` | `{"source": "input.message"}` |

## 节点配置要点

> 以下为关键要点，完整字段请查 config-schema。`output_variables` 由后端自动管理，非 end 节点不要传。

### LLM (`llm`)
- 留空字段自动注入全局值
- `user_prompt` 必填，`system_prompt` 强烈建议设置
- `max_tool_iterations` 控制工具调用上限，测试时可设为 1 隔离干扰
- **`file_inputs`（文件输入）**：上游节点文件变量路径列表，如 `["nodes.python_1.result", "nodes.api_1.downloaded_file"]`。解析后的文件自动作为多模态附件（图片/音频/视频/PDF）发送给模型，而非 JSON 序列化进 prompt 文本。兼容多种上游格式（Python `__save_file__` 的嵌套 `result`、API `downloaded_file`、流程输入 `file_list`）。**仅 Flow 模式可用，Agent 模式自动隐藏**
  - 典型场景：Python 节点生成图片 → LLM 节点 `file_inputs: ["nodes.python_1.result"]` → 模型看到图片而非 JSON
  - 变量值为文件数组时自动展开为多个附件
- 输出变量：`result`、`thinking`、`called_tools`（本次对话新调用的工具名列表）
- **必需工具检查**：LLM 本轮未调用指定工具时，自动注入提醒消息让 LLM 重试。仅检查本次 ReAct 循环新调用的工具（内存收集，不查 DB 历史），重试在 LLM 内部完成，用户只看到最终回复（无多段回复问题）。两种模式二选一：
  - **简单模式** `required_tools`: 必需工具名列表，如 `["send_wecom_message"]`，工具名精确匹配。**配置前先查 `GET /api/ai/flow/{id}/node/{node_key}/connected-tools`** 获取该 LLM 当前已连接的所有工具名
  - **高级模式** `tool_check_script`: 自定义检查脚本（留空走简单模式），复用 RestrictedPython 沙箱，签名 `def main(called_tools, last_result): return {"need_retry": bool, "hint": str}`，可写任意判断逻辑
  - `required_tools_max_retries`: 最大提醒重试次数，默认 2
  - `required_tools_hint`: 提醒消息模板，`{{tools}}` 占位符替换为缺失工具名（留空用默认模板）

### Python (`python`)
- 用直接参数签名：`def main(message): ...`，不用 `**kwargs`
- RestrictedPython 沙箱，支持 `requests` / `json` / `time` / `hashlib` 等模块，**支持网络请求**
- **RestrictedPython 禁止语法**：
  - ❌ 禁止访问 `__dunder__` 属性（如 `__name__`、`__class__`），需要类型名时用 `{dict: "dict", list: "list", str: "str", ...}` 字典查表替代 `type(x).__name__`
  - ❌ 禁止 `eval`/`exec`/`compile`/`globals`/`locals`
  - ❌ 禁止属性名以 `_` 开头的访问
  - ✅ 允许 `import`（白名单模块）、`f-string`、列表推导、`try/except`
- `timeout`: 默认 30s，5-300
- 输出变量：`result`（自动管理为 `{stdout, stderr, result, success}`）
- ⚠️ 返回值被包装，引用为 `nodes.<key>.result.<field>`
- **数据流模式（default 边）必须配置 `input_variables`**：函数参数不会自动注入，需显式映射上游变量
  ```json
  "input_variables": [{"name": "input_data", "source": "input.input_data", "type": "string"}]
  ```
  参数名（`input_data`）必须与 `main(input_data)` 签名一致；`source` 是变量路径（不加 `{{}}`）
- **工具模式**：
  - `use_preset_for_tool: true` 开启预设模式：LLM 不接触代码，只提供 input_variables 定义的业务参数
  - `description`: 工具描述，LLM 据此判断何时调用
  - 关闭时：LLM 需自行编写完整的 Python 代码

### Shell (`shell`)
- `command` + `timeout`。输出：`stdout`/`stderr`/`exit_code`
- **工具模式**：`shell_executor`（执行命令）+ `file_read`/`text_editor`/`file_write`（文件读写改）+ `file_search`（按内容搜索）+ `list_files`（按文件名 glob 匹配）+ 后台任务管理工具

### Knowledge (`knowledge`)
- `knowledge_base_id` + `knowledge_base_name` + `top_k`。输出：`result`

### API (`api`)
- `api_url`/`method`/`headers`/`body`，支持 `{{var}}` 模板
- 输出：`body`/`status_code`/`headers`，下载模式输出 `downloaded_file`
- **工具模式**：
  - `use_preset_for_tool: true` 开启预设模式：LLM 不接触 URL/Headers/Body，只提供 input_variables 定义的业务参数；模板 `{{var}}` 自动用 LLM 传入值渲染
  - `description`: 工具描述，LLM 据此判断何时调用
  - 关闭时：通用 `api_call_tool`，LLM 需自行提供完整 URL/Method/Headers/Body

### MCP / Skill / Memory / Todo / Agenda
- MCP：`mcp_server_ids` | Skill：`skill_ids` | Memory/Todo/Agenda：无需配置，连到 LLM 即可

### Condition (`condition`)
- `logic`: `and`/`or`，`rules`: `[{variable, operator, value}]`
- ⚠️ 用 `rules` 字段，`conditions` 已废弃

### Intent Router (`intent_router`)
- 两级级联：**规则层**（keywords + regex，按 intents 顺序短路）→ **LLM 层**（语义分类）
- **`input_variable`**：要路由的文本变量路径，默认 `input.question`。**大多数流程输入字段名为 `message`，必须显式设置为 `input.message`**，否则路由器收不到文本、所有规则和 LLM 分类均失效
- 每个意图 `{key, description, examples, rule: {keywords, regex_patterns}}`
- 分支边的 `source_handle` = 意图 key，未命中走 `default`
- 启用 LLM 层需有效 `provider/model/api_key`（留空自动使用全局默认配置）；仅规则层可省略
- 多分支可汇聚同一 end 节点
- 输出：`intent`/`raw_response`/`metadata`
- 路由结果写入两个变量：`variables._intent_route`（通用）+ `variables._intent_route_{node_key}`（节点级）

### 工具边意图过滤

工具边（`source_handle="tools"`）可通过 `condition` 字段控制工具在不同意图下的可见性：

```json
{
  "source_node_key": "knowledge_1",
  "target_node_key": "llm",
  "source_handle": "tools",
  "target_handle": "tools",
  "condition": {
    "intent_filters": {
      "intent_router": ["pre_sales", "after_sales"]
    },
    "filter_logic": "and"
  }
}
```

- **`intent_filters`**：`{路由器节点key: [意图key列表]}`，同一路由器内多个 key 为 OR 关系
- **`filter_logic`**：`"and"`（默认）或 `"or"`，控制多路由器间的关系
- `condition` 为 `null` 或不含 `intent_filters` → 工具始终启用
- 后端读取 `variables._intent_route_{router_key}` 进行匹配
- 典型场景：意图路由 → 不同知识库/API 按意图自动切换

### Loop（内联子节点）
- 子节点 key 带 `{loop_key}__` 前缀（双下划线），在**同一 flow** 内创建
- 子节点必须含 start 和 end
- `input_mappings`: `{card_field/name, source, type}` 两种格式等效
- ⚠️ **`input_mappings` 的 `source` 必须在执行路径上可达**：如果引用的变量来自另一条 intent/condition 分支上的节点，而该节点未被执行，则变量不存在，loop 会报错"源变量不存在"。应使用 `input.message` 等全局可达的变量
- 子 End 输出变量名固定用 `res`：`{"name":"res","source":"nodes.loop__python.result","type":"..."}`
- Loop 聚合为数组：`nodes.<loop>.res`
- 禁止嵌套 loop

### Card（引用外部流程）
- `ref_flow_id` 是**独立顶层字段**，仅创建时可设，`batch/config` 不能改
- 修改需删节点重建
- 子流程需已保存且含完整 start→节点→end

### Human
- 执行后通过 `interrupt()` 暂停，返回 `waiting_human` 事件（含 `execution_id`/`node_key`/`question`）
- 通过 resume 接口提交人工输入恢复执行

### Sub Agent（子Agent）`sub_agent`
- **仅 Agent 模式可用**，将已发布的 Agent 作为工具提供给父 Agent 的 LLM 调用
- **纯工具节点**：只有 `tools` 输出 handle，通过 `source_handle="tools"` 边连接到 LLM
- **配置**：`agent_id`（引用的已发布 Agent ID）
- **约束**：
  - 引用的 Agent 必须已发布（`status=1`）且 `description` 非空（用作工具描述）
  - 禁止递归：引用的 Agent 内不能包含 `sub_agent` 节点（含通过 card 间接引用）
  - 同一 LLM 可连接多个 sub_agent 节点
- **执行模式**：阻塞等待子 Agent 完成并返回结果
  - 执行期间每 20s 通过 `sub_agent_progress` 心跳事件保持 SSE 连接
  - 结果超过 500 行或 10KB 时自动截断，写入临时文件，LLM 可通过 `read_agent_file` 工具读取
- **工具审批转发**：子 Agent 的工具审批取决于其自身 LLM 节点的 `require_tool_approval` 配置
  - 审批事件通过父 SSE 流转发到前端，前端显示 "子Agent「xxx」请求执行以下工具"
  - 前端审批/拒绝直接调用子 Agent 自己的端点：`POST /api/agent/{sub_agent_id}/sessions/{sub_session_id}/tool_approval`
- **工具名称**：`ask_{sanitized_agent_name}`，LLM 通过此工具将任务委派给子 Agent
- **会话保留**：子 Agent 的会话保留在其聊天页面，标题为 `[子Agent调用] {task[:40]}`
- **取消传播**：父 Agent 被取消时自动中断子 Agent

## 已修复 Bug 记录

| # | 问题 | 根因 |
|---|------|------|
| #1 | LLM 执行后 `llm_result`/`thinking` 不保存 | `_run_react_loop` 缺少 `state.set_node_variable()` |
| #2 | intent_router 无法 batch 创建 | Pydantic 验证列表缺少该类型 |
| #3 | knowledge 空字符串报错 | 空字符串未转 `None`（需重启生效） |
| #4 | card 执行报 `'str' object has no attribute 'get'` | 子 end 的 `output_variables` 存为字符串 |
| #5 | intent_router 分支边被 Pydantic 拦截 | `validate_handle` 已改为动态校验 |
| #6 | Python 节点报 `__name__ is an invalid attribute` | RestrictedPython 禁止 dunder 属性，需用类型字典替代 `type(x).__name__` |
| #7 | Python 节点报 `main() missing required positional argument` | 数据流模式下未配置 `input_variables`，参数无法注入 |
| #8 | intent_router 所有规则不匹配、走 default | `input_variable` 默认 `input.question`，与流程输入字段名（通常为 `message`）不匹配 |
| #9 | loop 报 `源变量 'nodes.xxx.yyy' 不存在` | `input_mappings.source` 引用了另一条分支上的节点输出，该节点未执行 |

## Agent 运行时自我更新约束

Agent 在对话执行过程中**能否修改自身流程图**，受以下机制约束。

### 核心机制：每轮重建，运行时冻结

- LangGraph 图在**每次对话开始**时从 DB 的 `flow_node` / `flow_edge` 表一次性编译（`graph_builder.build()`），运行中不可变
- 即使运行时改了 DB，也**不影响当前这一轮**的执行，改动在**下一轮对话**重建图时自动生效
- 因此"自我更新"本质是：Agent 调接口写入 DB → 告知用户下条消息生效 → 下一轮自动带上新结构

### 默认无自更新能力

**没有任何节点工具直接暴露 `flow_node` / `flow_edge` 的写操作**。LLM 工具集只覆盖 shell/python/HTTP/知识检索/记忆/todo/子Agent/MCP 等运行时能力，不包含流程自编辑。

### 可选实现：通过 API 节点间接自更新

给 Agent 配一个 `use_preset_for_tool=false` 的 **API 节点**，LLM 即可调用本机 `/api/ai/flow/*` 系列接口修改自身流程：

- **本机回环免认证**：`auth_middleware` 对 `127.0.0.1` / `::1` 请求直接放行（注释明确为"AI 工具调用本平台 API"设计），无需 session cookie
- **改动永久持久化**：直接写入 `flow_node` / `flow_edge` 表，该 Agent 以后所有会话都带上新结构
- **下一轮生效**：本轮已编译的图无法注入新工具，需等下次对话重建

### 约束清单

| 约束 | 说明 |
|------|------|
| **生效时机** | 下一轮对话生效。本轮调 `attach` 后告知用户"已添加，下条消息生效" |
| **白名单** | 新增节点类型必须在 `AGENT_ALLOWED_NODE_TYPES` 内（start/end/llm/condition/intent_router + 工具节点）。违反会被 `batch_add_nodes` 拒绝 |
| **唯一性** | `start` / `end` / `llm` 各只能有 1 个（`AGENT_UNIQUE_NODE_TYPES`）。`knowledge` 等工具节点不受此限，可加多个 |
| **Shell 节点** | 禁止写项目 `data/` 目录（数据库/向量库所在），无法直接改 DB 文件 |
| **Python 节点** | RestrictedPython 沙箱，白名单不含 `app.services`，无法 import 操作 DB |
| **Sub Agent** | 子 Agent 是独立流程，**不能**触达父 Agent 内存中的已编译图 |
| **循环嵌套** | loop 内禁止嵌套 loop（含通过 card 间接），前后端均校验 |

> ⚠️ `flow_id` 需通过 system_prompt 注入或让 LLM 调 `/api/ai/flow/list` 按名称匹配（后者有重名歧义）。建议在 LLM 的 system_prompt 中写明当前流程 ID。

## 详细 API 参考

完整接口参数见 [references/api.md](references/api.md)。
