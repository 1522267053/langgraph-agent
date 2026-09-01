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
- `source`：输入变量或最终输出从哪里取值（裸路径，不加花括号）。
- `type`：供配置、展示和工具参数使用的数据类型(string/number/boolean/object/array/file_list)。

### 变量来源（路径全集）

任何变量都来自以下五类之一。**写 `source` 字段用裸路径，写 `{{模板}}` 用同一路径加双花括号**。

| 来源 | 路径格式（裸） | 含义 | 示例 |
|---|---|---|---|
| 流程输入对象 | `input.<field>` 或 `input` | 流程入口 `input_schema` 声明的字段；`input` 本身是完整对象 | `input.message`、`input.file_list`、`input` |
| 上游节点输出 | `nodes.<node_key>.<output_name>` | 任意上游节点的输出项。node_key 用创建时给的稳定 key（不要用显示名） | `nodes.llm_dev.result`、`nodes.normalize.result.result.text`、`nodes.fetch.result.items[0].id` |
| 当前节点输出 | `output.<field>` | 仅在结束节点的 `output_variables` 里写，用于组装最终结果 | `output.summary` |
| 流程全局变量 | `variables.<key>` | 通过 set_variable 等节点设置的全局变量 | `variables.user_id`、`variables.env` |
| 上下文 / 裸名 | `<bare_name>` | 没有前缀的路径，按 `input_variables` 映射 → 流程输入 → 全局变量 的顺序解析 | `message`、`file_list` |

**嵌套路径**：支持 `.` 进入对象/字典，`[N]` 进入数组索引，混合使用。例如：

- `nodes.api_dev.result.body.items[0].id`
- `nodes.python_x.result.result.users[2].email`

### 两种语法的对照

| 用途 | 字段 | 写法 | 例子 |
|---|---|---|---|
| `input_variables`、`output_variables`、`form_fields`、`form` 字段映射 | `source` | 裸路径 | `{"name": "msg", "source": "input.message"}` |
| LLM 的 `user_prompt` / `system_prompt` | `{{模板}}` | 同路径加 `{{...}}` | `请总结：{{input.content}}` |
| API 节点的 `api_url` / `headers` / `body` | `{{模板}}` | 同路径加 `{{...}}` | `{"url": "https://x.com/{{nodes.fetch.result.id}}"}` |
| Condition / IntentRouter 表达式 | 表达式字符串 | 裸路径 | `nodes.score.result >= 60` |

**易错点**：

- `source` 是裸路径，不写花括号：`source: "input.message"`，不是 `source: "{{input.message}}"`。
- 模板里**没有花括号就只是字面量**，`{{message}}` 才会被解析成变量；如果字段名是 `message` 而你想引用它，**两种写法都行**：`{{message}}`（走"裸名"解析）或 `{{input.message}}`（走"流程输入"路径）。建议显式写 `input.<字段名>`，意图更清楚，避免和 input_variables 别名冲突。
- LLM 节点的 `{{msg}}` 这种模板名是**局部别名**——只有当你在该 LLM 的 `input_variables` 里声明了 `{"name": "msg", "source": "input.message"}` 时才能解析。直接用 `{{input.message}}` 也可以，跳过别名层。
- `output.<field>` 只在结束节点里有效，别的节点用会得到空值。
- Python 节点返回字典后，访问 `main` 的返回值要走 `nodes.python_key.result.result.<你的字段>`（外层包了执行包装），详见 [Python 节点](#python-节点)。

### 实际示例

把"用户输入"原样塞进 LLM 提示词：

```json
{
  "input_variables": [{"name": "message", "type": "string"}],
  "user_prompt": "{{message}}"
}
```

把上游节点输出喂给下一个节点：

```json
{"name": "summary_text", "source": "nodes.writer.result"}
```

组装结束节点的最终输出：

```json
{
  "output_variables": [
    {"name": "summary", "source": "nodes.writer.result", "type": "string"},
    {"name": "score",   "source": "nodes.score.result",   "type": "number"}
  ]
}
```

API 节点的请求体里嵌入上游变量：

```json
{
  "body": "{\"user_id\": {{nodes.fetch.result.id}}, \"name\": \"{{input.name}}\"}"
}
```

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
| `ssh` | Agent 工具提供者（远程命令与 SFTP） | `tools -> tools` |

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
| `required_tools_max_retries` | 必需工具未调用时的最大重试次数（默认 2，结构化输出共用） |
| `max_tool_iterations` | 单轮最大工具调用轮次 |
| `approval_required_tools` | Agent 模式下执行前需要审批的完整工具名列表；空列表表示关闭审批 |
| `json_output_enabled` | 开启后绑定 `structured_output` 虚拟工具，输出变量自动追加 `structured_output`（object） |
| `json_fields` | 结构化输出字段树，规则见下节 |

配置 `required_tools` 或 `approval_required_tools` 前先调用 connected-tools 接口，从返回的 `tools[].name` 复制准确名称。它不是节点 key。动态注册且接口未返回的工具可手动填写完整名称。

### 结构化输出（structured_output）

`json_output_enabled: true` 后模型完成信息收集必须调用 `structured_output` 工具，其参数即结构化 JSON；下游用 `nodes.<llm_key>.structured_output` 引用解析后的对象。该工具自动并入必需工具检查清单（无需写入 `required_tools`），参数校验失败返回 `error` 由模型自动修正，重试共用 `required_tools_max_retries`。

`json_fields` 是字段树，递归不限层级：

```json
[
  {"name": "答案", "type": "string", "description": "最终回答", "required": true},
  {"name": "用户列表", "type": "array", "item_type": "object", "description": "", "required": true,
   "children": [
     {"name": "姓名", "type": "string", "required": true},
     {"name": "年龄", "type": "number", "required": false}
   ]},
  {"name": "元信息", "type": "object", "required": false,
   "children": [{"name": "版本", "type": "string", "required": true}]}
]
```

- `type`：`string` / `number` / `boolean` / `array` / `object`。
- `array` 写 `item_type`（`string`/`number`/`boolean`/`object`，缺省按 `string`）；元素为对象时必须 `item_type: "object"` 并用 `children` 描述元素字段。
- `object` 用 `children` 描述子字段；无 `children` 的 `object` 校验为自由 dict。
- 结束节点映射 `{"name": "structured_output", "source": "nodes.<llm_key>.structured_output", "type": "object"}` 后，Agent 模式每轮对话结束会把该输出持久化到最后一条 AI 消息的 `end_output` 字段。

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
- `use_preset_for_tool` 控制节点作为 **LLM 工具** 时是否使用预设参数：
  - `true`：节点配置中的 `api_url/method/headers/body` 等被当作**预设**（封装好的固定 API 端点）。LLM 工具调用时只能填 `input_variables` 里声明的业务参数，**不能改 URL/Headers/Body**。适合"封装一个外部服务（如天气、汇率、issue 列表）"作为受限工具，避免 LLM 误改端点。
  - `false`（默认）：节点配置**不当作预设**，通过工具边 `tools → tools` 连接到 LLM 后，LLM 工具调用时可以**自由提供** url/method/headers/body 等参数，变成一个通用 HTTP 工具。
- API 节点作为 **Workflow 独立执行节点**（`default → default` 连边）时，无论 `use_preset_for_tool` 取值，都会按节点配置发送预设请求；此时 `use_preset_for_tool` 只影响是否同时被 LLM 当工具看到。
- `description` 应明确工具用途、输入约束和副作用。
- 下载响应文件时使用实时 Schema 中的下载配置，结果会进入文件管理。
- 凭据应放在受控配置中，不要写进提示词或用户可见输出。

### 踩坑：API 工具无参数时不要留下划线占位

- 当 `use_preset_for_tool=true` 且 `input_variables` 为空，框架可能注入 `_dummy` 之类的占位字段触发 `Fields must not use names with leading underscores` 校验。
- 如果想要"通用 API 工具让 LLM 自己传参"，把 `use_preset_for_tool` 设为 `false` 即可，避免框架注入下划线占位。
- 如果确实需要 `true` 但又没有业务参数，加一个 `dummy` 占位字段：`{"name": "dummy", "type": "string", "required": false}`。

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

Shell 节点通过工具边提供 `shell_executor`、后台任务控制和文件操作工具。节点配置包含默认 `command`、`timeout`、`async_wait`、`default_workdir` 等字段；每次调用 `shell_executor` 还可传 `workdir` 指定工作目录。

- Windows 上命令经 cmd.exe 执行（不是 PowerShell）：命令必须为单行，含裸换行会被直接拒绝；多条独立命令用 `&&` 连接；多行 Python 先写入 .py 文件再执行；环境变量用 `%VAR%`。没有 `head`/`tail`，输出过滤用 `findstr` 或重定向文件后用 file_read 读取。
- 工作目录优先级：节点配置 `default_workdir`（相对路径基于项目根目录解析）> Agent 工作目录 > 服务进程目录。
- 每次调用结果附带 `cwd` 字段，表示实际执行目录。
- 每次调用都是独立进程，`cd` 不会改变后续调用的目录，切换目录必须传 `workdir`。
- 命令受危险模式和路径边界校验，不要尝试绕过（`Format-Volume`、`Clear-Disk`、递归强删盘根等 PowerShell 写法同样被拦截）。
- 长时间命令会转为后台任务并返回 `task_id`；后台任务支持并发，`shell_task_status` 可用 `wait_time`（8~120秒）长阻塞等待，多次查询不受限制。

## SSH 工具

SSH 节点（`ssh`）是 Agent 专用工具提供者，仅支持工具边 `tools -> tools` 连接；它不参与执行图，保存校验会拒绝普通数据边。连接信息全部内联在节点配置：

```json
{
  "host": "192.168.1.10",
  "port": 22,
  "username": "root",
  "auth_type": "password",
  "password": "...",
  "connect_timeout": 10,
  "command_timeout": 300,
  "max_transfer_mb": 50
}
```

- 工具集固定 4 个：`ssh_executor`（执行远程命令）、`ssh_upload` / `ssh_download`（SFTP 传文件）、`ssh_list_dir`（目录列表）。同一 LLM 只能连接一个 SSH 节点。
- `auth_type=private_key` 时改填私钥：PEM 内容写 `private_key`，或本机文件路径写 `private_key_path`；私钥口令放 `passphrase`（不是 `password`）。
- 每次调用独立建连，`cd` 不影响后续调用。命令默认超时 `command_timeout` 秒，调用时可传 `timeout`（1~3600）覆盖。
- 远程路径必须是 POSIX 绝对路径（以 `/` 开头）。上传父目录不存在时自动逐级创建；单文件上限取 `max_transfer_mb` 与全局工具上限的较小值，超限直接拒绝。
- `ssh_download` 成功后自动导入文件管理并返回 `download_url` / `preview_url`；本地保存路径缺省为 Agent 工作目录加远端文件名。
- 输出统一截断（带截断提示），大量输出先过滤（`| head -100`、`grep xxx`）；长耗时任务用 `nohup ... &` 后台化后查日志。
- 密码、私钥和私钥口令在读取接口中均以 `****` 掩码显示，回传掩码值不会覆盖数据库原值。

### 踩坑：SFTP 无会话与状态

- 上传/下载/列目录之间没有会话保持，也互不知晓对方结果：传输前先用 `ssh_list_dir` 或 `ssh_executor ls <dir>` 确认远程路径真实存在。
- 不支持目录递归传输；目标是目录时 `ssh_download` 会报 `is_directory` 错误。
- 连接失败细分：认证错误为 `auth_failed`（检查用户名/凭据），超时为 `timeout`（调大 `connect_timeout` 重试），其余网络问题为 `connection_error`。

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
7. 开启结构化输出时 `json_fields` 字段树符合类型规则（array 带 `item_type`，object 子字段放 `children`）。
