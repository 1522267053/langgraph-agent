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
2. **`base_config` 整体替换**：更新时必须传入完整配置，省略字段会被清空（更新前先 `GET /api/ai/flow/{id}/detail` 获取当前值）
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

无请求体，返回会话信息（含 `session_id`），后续对话需使用该 `session_id`。

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
| 支持节点 | start/end/condition/intent_router/llm + 工具节点(mcp/knowledge/skill/python/shell/memory/todo/agenda/api/media_gen/sub_agent) | 所有 18 种节点类型 |

## 创建流程（完整步骤）

```
1. POST /api/ai/flow/create          # 创建（可同时设 input_schema）
2. POST /api/ai/flow/{id}/nodes/batch    # 批量创建节点
3. POST /api/ai/flow/{id}/edges/batch    # 批量创建边
4. POST /api/ai/flow/{id}/nodes/batch/config  # 修正 end 的 output_variables
5. POST /api/execution/stream/{id}       # 执行
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
  }
}
```

`input_schema` 类型可选：`string / number / boolean / object / array / file_list`

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

## 节点配置要点

> 以下为关键要点，完整字段请查 config-schema。`output_variables` 由后端自动管理，非 end 节点不要传。

### LLM (`llm`)
- 留空字段自动注入全局值
- `user_prompt` 必填，`system_prompt` 强烈建议设置
- `max_tool_iterations` 控制工具调用上限，测试时可设为 1 隔离干扰
- 输出变量：`result`、`thinking`

### Python (`python`)
- 用直接参数签名：`def main(message): ...`，不用 `**kwargs`
- RestrictedPython 沙箱，支持 `requests` / `json` / `time` / `hashlib` 等模块，**支持网络请求**
- `timeout`: 默认 30s，5-300
- 输出变量：`result`（自动管理为 `{stdout, stderr, result, success}`）
- ⚠️ 返回值被包装，引用为 `nodes.<key>.result.<field>`
- **工具模式**：
  - `use_preset_for_tool: true` 开启预设模式：LLM 不接触代码，只提供 input_variables 定义的业务参数
  - `description`: 工具描述，LLM 据此判断何时调用
  - 关闭时：LLM 需自行编写完整的 Python 代码

### Shell (`shell`)
- `command` + `timeout`。输出：`stdout`/`stderr`/`exit_code`

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
- 每个意图 `{key, description, examples, rule: {keywords, regex_patterns}}`
- 分支边的 `source_handle` = 意图 key，未命中走 `default`
- 启用 LLM 层需有效 `provider/model/api_key`；仅规则层可省略
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

## 详细 API 参考

完整接口参数见 [references/api.md](references/api.md)。
