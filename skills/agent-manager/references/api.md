# AI Flow API 参考

Base URL: `http://127.0.0.1:8000` | 响应: `{code:1, msg, data}` 成功 / `{code:0, msg}` 失败

## 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ai/flow/create` | 创建流程（可同时设 input_schema） |
| POST | `/api/flow/update` | 更新流程元数据（含 input_schema） |
| POST | `/api/ai/flow/delete/{id}` | 删除流程 |
| GET | `/api/ai/flow/list[?flow_type=agent\|flow&keyword=]` | 查询列表 |
| GET | `/api/ai/flow/{id}/detail` | 详情（节点+边+Mermaid图） |
| GET | `/api/ai/flow/node-types` | 所有节点类型 |
| GET | `/api/ai/flow/node-types/{type}/config-schema` | 节点配置字段 |
| POST | `/api/ai/flow/{id}/nodes/batch` | 批量创建节点（node_key 省略则自动生成） |
| POST | `/api/ai/flow/{id}/nodes/batch/config` | 批量更新配置（base_config 整体替换） |
| POST | `/api/ai/flow/{id}/nodes/batch/delete` | 批量删除节点（级联删边） |
| POST | `/api/ai/flow/{id}/edges/batch` | 批量创建边 |
| POST | `/api/ai/flow/{id}/edges/batch/delete` | 批量删除边 |
| POST | `/api/execution/stream/{id}` | 执行 Flow（SSE） |
| POST | `/api/agent/{id}/sessions` | 创建 Agent 会话 |
| POST | `/api/agent/{id}/sessions/{sid}/chat` | Agent 聊天（SSE） |

## 执行接口详情

### POST /api/execution/stream/{flow_id}

```json
{"input_data": {"message": "hello"}, "files": [{"id": 1, "original_name": "doc.pdf"}]}
```

SSE 事件: `flow_start` → `node_start` → `node_thinking` / `node_content` / `node_done` → ... → `flow_done`

### POST /api/agent/{id}/sessions/{sid}/chat

```json
{"content": "用户消息", "params": {}}
```

---

## 节点配置字段参考

> 创建/更新节点前务必调用 `GET /api/ai/flow/node-types/{type}/config-schema` 确认最新字段。
> 以下为各节点核心字段摘要，`input_variables` / `output_variable` 等通用字段省略。

### start

| 字段 | 类型 | 说明 |
|------|------|------|
| `inputFields` | array | 输入字段定义，元素: `{name, type, description, required, accept?, multiple?, max_size?}` |

### end

| 字段 | 类型 | 说明 |
|------|------|------|
| `output_variables` | string | **注意：后端存储为字符串**，传入对象数组 JSON：`[{"name","source","type"}]` |

### condition

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `logic` | string | `"and"` | `and` / `or` |
| `rules` | array | `[]` | `[{variable, operator, value}]` |

`operator`: `==` `!=` `>` `>=` `<` `<=` `contains` `not_contains` `starts_with` `ends_with` `is_empty` `is_not_empty`

⚠️ 用 `rules` 字段，`conditions` 已废弃。

### intent_router

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `enable_rule_layer` | bool | `true` | 规则层（keywords + regex） |
| `enable_llm_layer` | bool | `true` | LLM 层（规则未命中时） |
| `case_sensitive` | bool | `false` | |
| `input_variable` | string | `"input.question"` | 待分类文本路径 |
| `confidence_threshold` | number | `0.6` | LLM 层置信度阈值 |
| `temperature` | number | `0.1` | LLM 层温度 |
| `max_tokens` | number | `200` | LLM 层 max tokens |
| `provider/model/api_key/base_url` | string | — | 留空走全局默认 |
| `intents` | array | `[]` | `[{key, description, examples, rule:{keywords,regex_patterns}}]` |

分类逻辑：规则层按 intents 顺序短路 → LLM 层 → `default` 分支。
分支边 `source_handle` = 意图 key，未命中走 `default`。
输出：`intent` / `raw_response` / `metadata`

### llm

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `provider` | string | — | 留空走全局默认 |
| `model` | string | — | |
| `api_key` | string | — | 留空走全局默认 |
| `base_url` | string | `""` | |
| `system_prompt` | string | `""` | 支持 `{{var}}` 模板 |
| `user_prompt` | string | `""` | 支持 `{{var}}`，省略时用 `input.message` |
| `temperature` | number | `0.7` | 0-2 |
| `max_tokens` | number | `8192` | 256-128000 |
| `max_tool_iterations` | number | `20` | 1-100 |
| `history_mode` | string | `"node"` | `node` / `flow` / `none` |
| `max_history_turns` | number | `10` | 1-100 |
| `capabilities` | object | 全 false | `{image, video, audio, pdf, xlsx}` |

输出：`{output_variable}` (文本响应) / `{thinking_variable}` (思考过程)

### python

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `code` | string | `""` | 必须定义 `main()` 函数 |
| `timeout` | number | `30` | 5-300 |
| `use_preset_for_tool` | bool | `false` | 工具预设模式：true 时 LLM 不接触代码，只提供 input_variables 参数 |
| `description` | string | `""` | 工具描述，预设模式下 LLM 据此判断何时调用该工具 |

⚠️ 返回值被包装为 `{stdout, stderr, result, success}`，引用为 `nodes.<key>.result.<field>`。
RestrictedPython 沙箱，支持 `requests`/`json`/`time`/`hashlib`/`openpyxl` 等模块。**支持网络请求**。

**工具模式行为**：
- `use_preset_for_tool: true` → 工具名 = 节点名称(小写下划线)，参数 = input_variables，LLM 看不到代码
- `use_preset_for_tool: false` → 通用 `python_executor`，LLM 自行编写完整代码

### shell

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `command` | string | `""` | 支持 `{{var}}` 模板 |
| `timeout` | number | `30` | 5-300 |

输出：`{stdout, stderr, return_code, success, command}`

### knowledge

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `knowledge_base_id` | number | — | 知识库 ID |
| `knowledge_base_name` | string | `""` | 显示名称 |
| `top_k` | number | `5` | 1-20 |
| `score_threshold` | number | `0.5` | 最低相似度 |
| `description` | string | `""` | 用于系统提示注入 |

工具模式：7 个工具（search/title_search/get_paragraphs/adjacent/title_lookup/save_insight/delete_insight）

### human

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `assist_prompt` | string | 预设 | 工具模式下引导 LLM 何时请求帮助 |
| `review_prompt` | string | `""` | 检查点模式下给用户的提示 |

工具模式：`request_human_help` 工具，触发 `interrupt()` 暂停流程。

### api

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `api_url` | string | `""` | 支持 `{{var}}` 模板 |
| `method` | string | `"GET"` | GET/POST/PUT/DELETE |
| `headers` | string | `""` | JSON 字符串，支持 `{{var}}` 模板 |
| `body` | string | `""` | JSON 字符串，支持 `{{var}}` 模板 |
| `content_type` | string | `"application/json"` | 请求内容类型 |
| `file_config` | object | — | `{upload_fields, download}` 文件上传/下载配置 |
| `use_preset_for_tool` | bool | `false` | 工具预设模式：true 时 LLM 不接触 URL/Method/Headers/Body，只提供 input_variables 参数 |
| `description` | string | `""` | 工具描述，预设模式下 LLM 据此判断何时调用该工具 |

输出：`{status_code, headers, data, success}`

**工具模式行为**：
- `use_preset_for_tool: true` → 工具名 = 节点名称(小写下划线)，参数 = input_variables，模板 `{{var}}` 自动用 LLM 传入值渲染；LLM 看不到 URL/Method/Headers/Body
- `use_preset_for_tool: false` → 通用 `api_call_tool`，LLM 自行提供完整 URL/Method/Headers/Body

### mcp / skill / memory / todo

| 节点 | 核心配置 | 工具模式说明 |
|------|---------|-------------|
| mcp | `mcp_server_ids: []` | 返回 MCP 服务器所有工具 |
| skill | `skill_ids: []` | `load_skill` 工具（同一 LLM 仅限 1 个） |
| memory | `max_results/default_importance/default_category` 等 | 4 工具: save/search/list/delete；三层 hot/warm/cold |
| todo | 无需配置 | `todowrite` / `todoread` 工具 |

memory 分类: `decision/preference/lesson/relation/event/task/other`

### loop

| 字段 | 类型 | 默认 | 说明 |
|------|------|------|------|
| `loop_mode` | string | `"count"` | `count` / `condition` / `for_each` |
| `max_count` | number | `10` | count 模式最大次数 |
| `condition_expression` | string | `""` | condition 模式表达式 |
| `for_each_source` | string | `""` | for_each 模式数组路径 |
| `break_on_error` | bool | `true` | |
| `concurrency` | number | `1` | 1=串行 |
| `input_mappings` | array | `[]` | `{card_field/name, source, type}` 两种格式等效 |

⚠️ Loop 使用**内联子节点**（key 带 `{loop_key}__` 前缀），**不需要 `ref_flow_id`**。
自动注入：`variables.loop_index`（0起始）/ `variables.loop_count` / `loop_item`（for_each）
禁止嵌套 loop。子 End 输出变量名固定 `res`，聚合为数组 `nodes.<loop>.res`。

### card

| 字段 | 类型 | 说明 |
|------|------|------|
| `input_mappings` | array | `{card_field, source}` — source 从父流程解析 |
| `output_mappings` | array | `{card_field, target_variable}` |

⚠️ `ref_flow_id` 是**独立顶层字段**，仅创建时可设，`batch/config` 不能改，修改需删节点重建。

### media_gen

| 字段 | 类型 | 说明 |
|------|------|------|
| `media_type` | string | `image` / `audio` / `video` |
| `image/audio/video` | object | `{enabled, provider, model, api_key, base_url, params}` |

启用为工具时提供 `generate_{type}_{nodeKey}` 工具。

---

## 边 Handle 配对

| source_handle | target_handle | 说明 |
|---|---|---|
| `default` | `default` | 标准数据流 |
| `tools` | `tools` | 工具边，目标必须 LLM |
| `true` / `false` | `default` | condition 分支（必须同时有 true+false） |
| `<intent_key>` | `default` | intent_router 分支（`default` 为兜底） |

## Agent 约束

- 仅 1 个 LLM 节点
- 允许: condition, intent_router
- 禁止: loop, card, human
