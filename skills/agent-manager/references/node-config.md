# 节点配置参考

本页解释稳定的配置语义。字段名、必填项、默认值和嵌套结构必须以实时接口为准：

```text
GET /api/ai/flow/node-types/{node_type}/config-schema
```

不要从示例推断未出现的字段，也不要把数组或对象手动序列化成字符串。

## 通用变量模型

通用变量项结构：

```json
{"name": "text", "source": "input.content", "type": "string"}
```

- `name`：当前节点中的参数名或输出名。
- `source`：输入变量或最终输出从哪里取值。
- `type`：供配置、展示和工具参数使用的数据类型。

节点输出写入 `nodes.<node_key>.<output_name>`。常用路径：

| 路径 | 含义 |
|---|---|
| `input` | 完整流程输入对象 |
| `input.content` | 输入对象字段 |
| `nodes.assistant.result` | 节点输出 |
| `variables.custom_key` | 流程全局变量 |
| `output.summary` | 当前输出对象字段 |
| `nodes.fetch.result.items[0].id` | 嵌套对象和数组索引 |

无前缀路径按 `input_variables` 映射上下文、流程输入、全局变量的顺序解析。模板字符串使用 `{{input.content}}`；`source` 字段则直接写 `input.content`，不要加花括号。

## 输入与输出

流程级 `input_schema.fields` 决定调用方可提交的字段，开始节点的 `input_variables` 决定编辑器和运行入口展示的参数。新建流程时保持两者名称和类型一致。

结束节点用 `output_variables` 组装最终结果：

```json
{
  "output_variables": [
    {"name": "summary", "type": "string", "source": "nodes.writer.result"}
  ]
}
```

若省略结束节点输出映射，系统会回传内部变量集合，不适合作为稳定 API 输出。

## 节点与连接模式

| 类型 | 用途 | 常见连接 |
|---|---|---|
| `start` / `end` | 流程入口和出口 | `default` 数据流 |
| `llm` | 对话、生成、工具编排 | 数据流；工具目标 |
| `condition` | 规则真假分支 | `true/false -> default` |
| `intent_router` | 规则和 LLM 意图分类 | intent key / `default` 分支 |
| `api` / `python` / `knowledge` | 可作执行节点，也可作工具 | `default` 或 `tools` |
| `human` | Workflow 人工检查点或人工工具 | `default` 或 `tools` |
| `card` / `loop` | Workflow 子图复用和迭代 | `default` 数据流 |
| `mcp` / `skill` / `memory` / `todo` | Agent 工具提供者 | `tools -> tools` |
| `shell` / `sub_agent` / `agenda` | Agent 工具提供者 | `tools -> tools` |

`source_handle="tools"` 的边不加入 LangGraph 执行图。MCP 节点同样不执行，只负责向 LLM 提供工具。

## LLM

关键字段：

| 字段 | 说明 |
|---|---|
| `user_prompt` | 必填消息模板；缺失时 LLM 收不到用户任务 |
| `system_prompt` | 角色、约束和工具使用策略 |
| `provider/model/api_key/base_url` | 留空时尝试注入全局默认模型 |
| `temperature/max_tokens` | 采样和单次输出限制 |
| `history_mode` | `node`、`flow` 或 `none` |
| `max_history_turns` | 注入的最大历史轮数 |
| `file_inputs` | 作为多模态附件发送的文件变量路径 |
| `output_variables` | 默认包含 `result` 和 `thinking` |
| `required_tools` | 本轮必须调用的实际工具名列表 |
| `tool_check_script` | 自定义必需工具检查脚本 |
| `max_tool_iterations` | 单轮最大工具调用轮次 |
| `approval_required_tools` | Agent 模式下执行前需要审批的完整工具名列表；空列表表示关闭审批 |

配置 `required_tools` 或 `approval_required_tools` 前先调用 connected-tools 接口，从返回的 `tools[].name` 复制准确名称。它不是节点 key。动态注册且接口未返回的工具可手动填写完整名称。

## 条件与意图路由

条件节点配置：

```json
{
  "logic": "and",
  "rules": [
    {"variable": "nodes.score.result", "operator": ">=", "value": "60"}
  ]
}
```

常用操作符包括 `==`、`!=`、`>`、`>=`、`<`、`<=`、`contains`、`not_contains`、`is_empty`、`is_not_empty`、`starts_with`、`ends_with`。同一条件节点必须同时存在 `true` 和 `false` 出边。

意图路由的 `intents[].key` 就是出边的 `source_handle`，并可额外配置 `default` 出边：

```json
{
  "input_variable": "input.question",
  "enable_rule_layer": true,
  "enable_llm_layer": true,
  "intents": [
    {
      "key": "refund",
      "description": "退款或退货",
      "examples": ["我要退款"],
      "rule": {"keywords": ["退款"], "regex_patterns": []}
    }
  ]
}
```

工具边可按意图控制运行时可见性：

```json
{
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

`intent_filters` 的 key 是意图路由节点 key，值是允许的 intent key。同一路由器内为 OR，多路由器之间由 `filter_logic` 的 `and` 或 `or` 控制；省略过滤条件时工具始终可见。connected-tools 返回结构连接，实际调用时才按当前意图过滤。

## API 节点

执行模式常用字段为 `api_url`、`method`、`headers`、`body`、`content_type`、`form_fields` 和 `file_config`。URL、请求头和请求体支持变量模板。

- 默认输出：`body`、`status_code`、`headers`。
- `use_preset_for_tool=true` 时，LLM 只提供 `input_variables` 定义的业务参数，不能改预设 URL、Headers 和 Body。
- `use_preset_for_tool=false` 时提供通用 API 工具，由 LLM 提供 URL、方法、请求体和上传文件。
- `description` 应明确工具用途、输入约束和副作用。
- 下载响应文件时使用实时 Schema 中的下载配置，结果会进入文件管理。
- 凭据应放在受控配置中，不要写进提示词或用户可见输出。

## Python 节点

代码必须定义 `main`，形参与 `input_variables[].name` 对应：

```python
def main(text):
    return {"normalized": text.strip()}
```

数据流模式必须为每个形参配置 `input_variables.source`，参数不会仅因同名自动从上游注入。工具模式下，`use_preset_for_tool=true` 隐藏代码并只向 LLM 暴露业务参数；为 `false` 时由 LLM 提供待执行代码。

运行在 RestrictedPython 白名单环境中，不支持任意模块和危险语法。节点输出不是 `main` 的裸返回值，而是执行包装：

```json
{
  "stdout": "",
  "stderr": "",
  "result": {"normalized": "..."},
  "success": true
}
```

默认节点路径为 `nodes.<python_key>.result`，读取 `main` 返回内容时通常继续访问 `.result`，例如 `nodes.normalize.result.result.normalized`。

生成文件时，`main` 可返回：

```json
{
  "__save_file__": true,
  "content_base64": "...",
  "mime_type": "image/png",
  "filename": "chart.png"
}
```

保存后 `preview_url`、`mime_type`、`file_name`、`download_url` 会提升到执行包装顶层。

## Shell 工具

Shell 节点通过工具边提供 `shell_executor`、后台任务控制和文件操作工具。节点配置包含默认 `command`、`timeout`、`async_wait` 等字段；每次调用 `shell_executor` 还可传 `workdir` 指定工作目录。

- `workdir` 省略时使用 Agent 工作目录。
- 相对路径基于 Agent 工作目录解析。
- 每次调用都是独立进程，`cd` 不会改变后续调用的目录。
- 命令受危险模式和路径边界校验，不要尝试绕过。
- 长时间命令可能转为后台任务，使用状态、输入和取消工具继续管理。

## 卡片

`card` 通过节点顶层的 `ref_flow_id` 引用另一个 Workflow，不能把它放进 `base_config`。配置字段：

```json
{
  "input_mappings": [
    {"card_field": "content", "source": "input.text"}
  ],
  "output_mappings": [
    {"card_field": "summary", "target_variable": "nodes.card_1.summary"}
  ]
}
```

运行时子节点 key 使用 `card_key__sub_node_key`。被引用流程必须有明确的 start 输入和 end 输出；卡片嵌套会递归展开。

## 循环

`loop_mode` 支持：

| 模式 | 关键字段 |
|---|---|
| `count` | `max_count` |
| `condition` | `condition_expression`、`max_count` |
| `for_each` | `for_each_source`、`concurrency` |

循环体节点位于同一 Flow，key 必须以 `<loop_key>__` 开头，并包含 start 和 end。循环体可读取 `loop_index`、`loop_count`、`loop_item`；输入由 `input_mappings` 映射，输出通过循环节点自己的 `nodes.<loop_key>.<name>` 聚合。循环内禁止直接或经卡片间接嵌套另一个循环。

## 人工节点

Workflow 中的 `human` 节点使用 `prompt` 或 `review_prompt` 暂停执行，默认输出 `feedback`。恢复方式见 [API 参考](api.md)。作为 LLM 工具时暴露 `request_human_help`。

## 资源工具

| 类型 | 关键配置 | 说明 |
|---|---|---|
| `mcp` | `mcp_server_ids` | 加载所选 MCP 服务的工具 |
| `skill` | `skill_ids` | 提供 `load_skill`，按需加载 Skill 文档 |
| `knowledge` | `knowledge_base_id`、`top_k` | 提供检索、导航和知识沉淀工具 |
| `memory` | 实时 Schema 中的容量、衰减和整理参数 | 提供 save/search/list/get/delete 五类操作 |
| `todo` | 通常使用默认配置 | 提供 `todowrite`、`todoread` |
| `agenda` | 通常使用默认配置 | 提供日程创建、查询、更新和删除 |
| `sub_agent` | `agent_id` | 委派任务，详见 [子 Agent](sub-agent.md) |

同一 LLM 最多连接一个 `skill` 和一个 `memory` 节点；其他限制由批量边接口实时校验。

## 配置检查

1. Schema 中的必填字段都有值，类型保持为原生 JSON 类型。
2. 每个输入 `source` 都能从流程输入或上游节点解析。
3. 每个 end 输出都使用完整的 `nodes.<key>.<name>` 路径。
4. 工具边为 `tools -> tools`，数据边为 `default -> default`。
5. `required_tools` 与 connected-tools 返回的实际名称一致。
6. Python 包装、卡片 `ref_flow_id` 和循环路径已按上述规则处理。
