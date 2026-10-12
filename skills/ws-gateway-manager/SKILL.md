---
name: ws-gateway-manager
description: |
  创建、管理网关，通过 WebSocket 触发流程/智能体执行，以及把外部平台（微信/飞书/钉钉等）接入本系统。
  适用场景：
  (1) 用户要求创建或管理网关
  (2) 外部系统通过 WebSocket 连接触发流程执行，实时接收流式结果
  (3) 注册远程工具，让 Agent 反向调用客户端函数
  (4) 管理会话（创建/切换/列表/删除）
  (5) 人工交互恢复（resume）、工具审批（tool_approval）、取消执行（cancel）、多会话并发（call_id 路由）
  (6) 把 IM / 聊天平台接入本系统（微信、企业微信、飞书、钉钉、Slack 等），需要写桥接客户端

  触发词：「创建网关」「管理网关」「websocket 触发」「远程工具」「网关会话」「注册工具」「人工交互」「工具审批」「取消执行」「并发会话」「接入微信」「微信机器人」「让微信接入」「接入飞书」「接入钉钉」「消息平台接入」「IM 接入」
---

# WebSocket 网关管理

服务器：`http://127.0.0.1:8000`

## 核心规则

1. **WebSocket 触发**：外部客户端通过 `ws://host/ws/trigger/{token}` 连接，以 JSON 指令驱动执行
2. **token 自动生成**：创建网关 时后端 `uuid.uuid4().hex` 生成 token
3. **实时流式返回**：执行结果通过 WebSocket 逐 token 流式推送（node_content/flow_done/token_usage 等），无需轮询。**完整事件列表见下方「执行指令」章节**
4. **错误码判定**：所有 `error` 事件携带 `data.error_code`（机器可读枚举），客户端**必须按 error_code 判断**而非匹配 message 字符串。完整枚举见 [references/api.md](references/api.md)「错误码枚举」章节
5. **Agent 专属功能**：远程工具注册、会话管理（创建/切换/列表/删除/消息查询）仅 Agent 类型支持。Flow 类型调用会收到 `error_code="NOT_AGENT_TYPE"` 错误
6. **并发模型（会话级）**：Agent 类型同一 `session_id` 正在执行时新 execute 被拒绝（`error_code="SESSION_BUSY"`）；不同会话、新建会话、Flow 类型可并发执行。并发时事件流交错，**每个执行事件顶层携带 `call_id`**（调用记录 ID，`call_started` 事件首次下发），客户端按 `call_id` 路由
7. **CRUD 需登录态**：管理接口（`/api/ws-gateway/page/create/update/delete`）需要 session cookie
8. **输入合并**：`input_data = {**gateway.input_config, **客户端参数}`（排除 `action` 和 `session_id`），客户端参数覆盖默认模板
9. **工具名**：远程工具直接使用客户端注册的原始名称，超时 120 秒
10. **文件传输 token 鉴权**：上传（`POST /api/ws-gateway/upload`）/下载（`GET /api/ws-gateway/download/{file_id}`）由网关 token 自鉴权，**免登录**（已豁免认证白名单）。上传返回 `file_id`，可塞进 `execute` 的 `files` 字段；下载严格校验文件归属该网关关联的 flow
11. **空闲超时**：连接空闲超过 `WS_TRIGGER_IDLE_TIMEOUT`（默认 120 秒，0 关闭）未收到任何消息（含 ping）时服务端以关闭码 `4408` 断开。客户端**必须周期发送 ping**（建议 30 秒间隔）
12. **平台接入走桥接客户端**：WS 网关是协议层，不绑定具体 IM。接入微信/飞书/钉钉等需写独立桥接进程（类型必须 `agent`、按用户复用 session、按 `error_code` 处理异常），详见「典型接入场景」

## 管理接口（HTTP）

| 方法 | 路径 | 认证 | 用途 |
|------|------|:--:|------|
| POST | `/api/ws-gateway/page` | ✅ | 分页列表 |
| POST | `/api/ws-gateway/create` | ✅ | 创建（自动生成 token） |
| POST | `/api/ws-gateway/update` | ✅ | 更新 |
| GET | `/api/ws-gateway/delete/{id}` | ✅ | 软删除 |
| GET | `/api/ws-gateway/get/{id}/url` | ✅ | 获取 WebSocket 地址 |
| GET | `/api/ws-gateway/get/{id}/tools` | ✅ | 查询客户端当前注册的远程工具（运行时内存态） |

## 查询已注册工具

```bash
curl "http://host/api/ws-gateway/get/{id}/tools"
```

响应（`data` 字段）：

```json
{
  "connected": true,
  "tools": [
    {
      "name": "query_database",
      "description": "查询本地数据库",
      "parameters": {"type": "object", "properties": {"sql": {"type": "string"}}}
    }
  ]
}
```

- 工具由 WS 客户端连接后通过 `register_tools` 指令动态注册，**不落库**，接口返回的是实时内存态；连接断开即清空
- `connected=false`（客户端离线）时 `tools` 恒为空数组
- 仅 Agent 类型网关会有工具（Flow 类型不支持注册，恒为空）
- 前端「WS 网关管理」页的网关详情弹窗即基于此接口展示连接状态与已注册工具

## 创建网关

```json
POST /api/ws-gateway/create
{
  "name": "订单处理",
  "flow_id": 1,
  "description": "处理外部订单",
  "input_config": {"message": "请处理新订单"},
  "is_enabled": 1
}
```

## 典型接入场景

WS 网关是**协议层**（WebSocket + JSON 指令），本身不绑定任何具体 IM/平台。要让某个平台接入，需要写一个**桥接客户端**：把平台消息转成 `execute` 指令发给网关，再把网关返回的回复转回平台。

下面用**微信**作为完整案例（已有可运行的参考实现），其他平台（飞书、钉钉、企业微信、Slack）同理替换最外层收发即可。

### 接入前必须做的 3 个决策

| 决策 | 要求 | 原因 |
|---|---|---|
| 网关类型 | **必须 `agent`**，不能是 `flow` | 远程工具注册、会话管理（多轮对话）仅 Agent 支持；`flow` 类型调用会收到 `NOT_AGENT_TYPE` |
| 会话策略 | **按用户维度复用 session**（如 `user_id → session_id` 持久化到本地） | 每个微信用户要有多轮对话记忆；不复用会导致每句话都是新会话、无上下文 |
| 多用户并发 | 不同用户不同 session（天然并发）；同一 session 内串行 | 同 session 并发会被拒（`SESSION_BUSY`），客户端需排队重试而非报错 |

### 完整链路

```
微信用户 ──发消息──> 桥接客户端 ──execute──> WS 网关 ──> Agent 执行
                          ↑                                   │
                          └────── flow_done（回复） <──────────┘
                          │
                    回发微信（wechatbot-sdk）
```

**关键点**：桥接客户端是**独立进程**，跑在能连到网关的机器上；它同时持有两个连接——微信侧（`wechatbot-sdk`）和网关侧（WebSocket）。两者**必须共用同一个事件循环**（否则 ws 连接绑在已结束的 loop 上，下次 send 报 `ConnectionClosedError`）。

### 桥接客户端要做 5 件事

| # | 职责 | 要点 |
|---|---|---|
| 1 | **消息格式化** | 把平台消息组装成 `message` 字符串；用户连发多条时应**合并**成一条（标注"用户连续发送 N 条"），避免刷屏和浪费调用 |
| 2 | **会话映射** | `user_id → session_id` 本地持久化（原子写入）；收到 `SESSION_INVALID` 时删除映射、重建会话、重试一次 |
| 3 | **并发排队** | 同 session 收到 `SESSION_BUSY` → sleep 后重试（不可立即报错）；不同用户自然并发 |
| 4 | **文件双向传输** | 用户发来的文件先下载到本地，路径塞进 message；Agent 产出文件用 `send_file_to_*` 远程工具回发 |
| 5 | **断线重连 + 心跳** | 定时 `ping` 保活（防 4408 空闲断开）；断线后自动重连并**重新 register_tools**（工具不落库，断连即清空） |

### Agent 侧配套要求

桥接客户端只是管道，**能不能用取决于 Agent 怎么配**：

- 网关关联的 Agent 必须有 `end` 节点（否则触发即失败）
- 把平台相关的上下文做成 `input_schema` 字段，由客户端通过 `params` 传入。例如微信场景的字段示例（`bot_name` / `chat_type` / `sender_id`）见下方「传入 input_schema 字段」
- 需要 Agent 反向操作客户端环境时（如读本地文件、查本地数据库），走**远程工具注册**
- 风险操作（shell/ssh/python/api/mcp）建议开**工具审批**，把确认权交回给聊天里的用户

### 远程工具的定位（这是 WS 网关最大的价值）

远程工具让 Agent **反向调用客户端所在机器**的能力——这是纯云端平台做不到的：

| 场景 | 客户端注册的工具 | Agent 能做什么 |
|---|---|---|
| 微信回发文件 | `send_file_to_wechat(path, caption)` | 用户说"把月报发我"→ Agent 读本地文件 → 工具回发到微信 |
| 查本地数据库 | `query_database(sql)` | 用户问"上周订单多少"→ Agent 生成 SQL → 工具在本地库执行 → 结果回传 |
| 控制本机软件 | `run_xxx(...)` | 让 Agent 操作只有本机才有的软件/硬件 |

### 微信接入实测要点（来自参考实现的经验）

- **类型必须 agent**：用 flow 类型网关会在连接后立刻收到类型错误
- **session 失效自愈**：服务端删了会话/重启后，本地 `sessions.json` 里的 id 会失效，必须靠 `error_code=SESSION_INVALID` 判断并自动重建（**不要匹配 message 文本**——错误文案会变）
- **症状 vs 根因**：连发消息时若客户端不排队，会出现"回复错位/丢消息"，根因是 `SESSION_BUSY` 被当成普通错误丢弃
- **登录态持久化**：微信扫码凭证落盘后，桥接进程重启不用重新扫码
- ⚠️ **风控风险**：第三方微信协议接入**存在被封号风险**，建议用测试号/小号，不要挂主力号

### 参考实现

微信桥接已有可运行的完整实现（独立于本仓库）：`wechatbot-sdk` 负责微信收发，本项目的 `ws-gateway-manager/references/ws_client_example.py` 提供了通用的 ws 客户端封装（心跳 + `tool_invoke` 自动处理 + 按 `call_id` 路由），可直接作为桥接客户端的底座。

## WebSocket 触发

### 连接

```
ws://host/ws/trigger/{token}
```

连接成功后收到 `connected` 事件：
```json
{
  "type": "connected",
  "data": {
    "gateway_id": 1,
    "gateway_name": "订单处理",
    "flow_id": 1,
    "flow_type": "agent",
    "upload_url": "/api/ws-gateway/upload?token=abc123...",
    "download_url_template": "/api/ws-gateway/download/{file_id}?token=abc123..."
  }
}
```

#### `connected` 事件字段说明

| 字段 | 类型 | 说明 |
|------|------|------|
| `gateway_id` | int | 网关 ID（管理用） |
| `gateway_name` | string | 网关显示名（创建时设置的 `name`） |
| `flow_id` | int | 关联的 flow ID（Agent 或 Flow） |
| `flow_type` | string | `"agent"` 或 `"flow"`，**影响可用指令** |
| `upload_url` | string | 文件上传端点（token 自鉴权） |
| `download_url_template` | string | 文件下载端点模板（`{file_id}` 占位符） |

> **关键建议**：
> - 连接后**必须检查 `data.flow_type`**。`"agent"` 才支持远程工具（`register_tools`/`tool_invoke`/`tool_result`）和会话管理（`create_session`/`switch_session` 等），`"flow"` 仅支持 `execute`。
> - 非 Agent 类型调用 register_tools 或会话操作会收到 `{"type":"error","data":{"error_code":"NOT_AGENT_TYPE","message":"仅 Agent 类型支持..."}}`。
> - `upload_url` 和 `download_url_template` 已在 connected 事件中下发，无需再次查询，直接使用。
> - **建议客户端实现心跳**：定时发送 `ping` 纯文本消息（不发送 JSON 包装），服务端回 `pong`，避免长时间无数据时连接被中间设备断开。
>
> `data.upload_url` / `download_url_template` 为文件传输端点模板（token 自鉴权，免登录），用法见下文「文件传输」。

### 客户端指令一览

| 指令 | 用途 | 适用类型 |
|------|------|:------:|
| `execute` | 发送消息/数据触发执行 | 全部 |
| `resume` | 恢复等待人工输入的执行（Agent 传 `session_id`，Flow 传 `execution_id`，`input` 为输入内容） | 全部 |
| `cancel` | 取消正在执行的会话/执行记录 | 全部 |
| `register_tools` | 注册远程工具 | 仅 Agent |
| `unregister_tools` | 注销所有远程工具 | 仅 Agent |
| `tool_result` | 返回工具执行结果（回应 tool_invoke） | 仅 Agent |
| `tool_approval` | 确认/拒绝待审批的工具调用（`result`=approved/rejected） | 仅 Agent |
| `create_session` | 创建新会话 | 仅 Agent |
| `switch_session` | 切换当前会话 | 仅 Agent |
| `list_sessions` | 查询会话列表 | 仅 Agent |
| `delete_session` | 删除会话（含消息 + checkpoint） | 仅 Agent |
| `get_messages` | 查询会话历史消息（游标分页） | 仅 Agent |
| `delete_message` | 删除指定消息及其后所有消息 | 仅 Agent |
| `ping`（纯文本） | 心跳，服务端回 `pong`（**必须周期发送**，否则空闲超时 4408 断开） | 全部 |

### 执行指令

```json
{"action": "execute", "message": "你好"}
```

指定已有会话（多轮对话）：
```json
{"action": "execute", "message": "继续", "session_id": 5}
```

Flow 类型（无 message，用 input_data 字段传递）：

```json
{"action": "execute", "city": "北京"}
```

#### 传入 input_schema 字段

Agent 类型 flow 通常有自定义 `input_schema`（如微信接入场景的 `bot_name`、`chat_type`、`sender_id`），需要通过 **`params`** 字段传递。`params` 的 keys 必须与 `input_schema.fields[].name` 一致：

```json
{
  "action": "execute",
  "message": "[张三](msg_001): 你好",
  "params": {
    "bot_name": "吴国邦",
    "chat_type": "group",
    "sender_id": "zhangsan"
  }
}
```

> 上例即微信接入的典型形态：`message` 是格式化后的聊天文本（含发送者标识），`params` 提供 Agent 判断群聊/私聊、称呼等所需的上下文。详见「典型接入场景」。

> **输入合并规则**：`input_data = {**gateway.input_config, **客户端参数}`（排除 `action` 和 `session_id`）。也就是说：
> - 创建网关时设的 `input_config` 作为默认模板
> - 客户端通过 `params` 传入的字段覆盖默认值
> - LLM 节点的 `user_prompt` 通过 `{{input.field_name}}` 引用

> ⚠️ **常见陷阱**：
> - Agent 类型必须有 end 节点（见 agent-manager SKILL.md），否则 ws-gateway 触发会失败
> - `params` 中字段名必须严格匹配 `input_schema.fields[].name`，拼写错误会导致模板渲染失败

#### SSE 事件完整列表

执行后服务端实时推送以下事件（按发生顺序）：

```json
{"type": "flow_start", "data": {"flow_id": 1, "execution_id": 5}}
{"type": "node_start", "data": {"node_key": "start", "node_type": "start", "node_name": "开始"}}
{"type": "node_done", "data": {"node_key": "start", "node_type": "start", "node_name": "开始"}}
{"type": "node_start", "data": {"node_key": "llm", "node_type": "llm", "node_name": "AI助手"}}
{"type": "node_content", "data": {"node_key": "llm", "content": "你好"}}
{"type": "node_content", "data": {"node_key": "llm", "content": "！"}}
{"type": "node_thinking", "data": {"node_key": "llm", "thinking": "思考过程..."}}
{"type": "token_usage", "data": {"node_key": "llm", "prompt_tokens": 100, "completion_tokens": 50, "total_tokens": 150, "model": "...", "provider": "...", "cache_read_tokens": 0, "cache_write_tokens": 0, "reasoning_tokens": 0}}
{"type": "node_done", "data": {"node_key": "llm", "node_type": "llm", "node_name": "AI助手"}}
{"type": "node_start", "data": {"node_key": "end", "node_type": "end", "node_name": "结束"}}
{"type": "node_done", "data": {"node_key": "end", "node_type": "end", "node_name": "结束"}}
{"type": "flow_done", "data": {"execution_id": 5, "status": "success", "output_data": {"content": "你好！"}}}
```

> **事件说明**：
>
> | 事件类型 | 用途 | 必现 |
> |---------|------|:----:|
> | `flow_start` | 流程开始 | ✅ |
> | `node_start` | 节点开始执行 | ✅ |
> | `node_content` | LLM 流式输出（逐 token 推送） | ✅ |
> | `node_thinking` | LLM 思考过程（仅部分模型，如 Claude） | ❌ |
> | `node_done` | 节点完成 | ✅ |
> | `token_usage` | LLM token 消耗统计 | ✅ |
> | `flow_done` | 流程完成，`data.output_data` 含 end 节点 output_variables 解析结果 | ✅ |
> | `call_started` | 执行开始，`data.call_id`/`data.session_id`；之后的所有执行事件顶层携带 `call_id` | ✅ |
> | `waiting_human` | Human 节点暂停等待用户输入（用 `resume` 恢复，Flow 传 execution_id） | ❌ |
> | `human_input_required` | Agent 人工协助中断（用 `resume` + session_id 恢复） | ❌ |
> | `tool_approval_required` | 工具调用待审批（用 `tool_approval` 指令确认/拒绝） | ❌ |
> | `tool_approval_result` | tool_approval 指令回执（`data.resolved` 是否命中待审批） | ❌ |
> | `cancel_accepted` | cancel 指令已受理，随后下发 `flow_done(status=cancelled)` | ❌ |
> | `error` | 流程出错（`data.error_code` 为机器可读枚举，见下表） | ❌ |
> | `tool_invoke` | Agent 调用 ws-gateway 远程工具（仅 Agent 类型，需通过 `tool_result` 返回结果） | ❌ |

### 人工交互与控制指令

**resume — 恢复等待人工输入的执行**（Agent 传 `session_id`，Flow 从 `waiting_human` 事件取 `execution_id`）：

```json
{"action": "resume", "session_id": 5, "input": "同意，按方案A执行"}
```

恢复后可能再次触发中断（多轮交互），继续 resume 即可。Flow 的并发恢复由执行记录 CAS 乐观锁保护（被抢占时报「执行已被其他请求抢占」）。

**tool_approval — 工具审批**（仅 Agent，工具节点开启「调用审批」时触发）：

```json
{"action": "tool_approval", "session_id": 5, "approval_id": "dd3dc382f0334d56", "result": "approved"}
```

`result` 为 `approved`（放行）或 `rejected`（拒绝）。**`approval_id` 必填**——从 `tool_approval_required` 事件的 `data.approval_id` 取（服务端按 `(session_id, approval_id)` 精确路由到指定审批批次，缺失报 `INVALID_PARAMS`）。无待审批时返回 `{"type":"tool_approval_result","data":{"resolved":false}}`，可安全忽略；命中时 `resolved=true`。

**cancel — 取消执行**：

```json
{"action": "cancel", "session_id": 5}
{"action": "cancel", "execution_id": 12}
```

受理后回 `cancel_accepted`，执行终止时下发 `flow_done`（`status=cancelled`）。Flow 仅 RUNNING/WAITING_HUMAN 状态可取消。

## 文件传输（token 鉴权）

外部客户端通过网关 token 上传/下载文件，**免登录**（由 token 自鉴权，已豁免认证白名单）。`connected` 事件已下发 `upload_url` / `download_url_template`，可直接取用。

### 上传 → 带 file_id 执行 → 下载产物

```bash
# 上传：拿 file_id
curl -F "file=@test.png" "http://host/api/ws-gateway/upload?token=TOKEN"
# → {"code":1,"data":{"file_id":42,"download_url":"/api/ws-gateway/download/42?token=TOKEN","mime_type":"image/png","file_size":10240}}
```

把 `file_id` 塞进 `execute` 的 `files` 字段供 Agent 使用：

```json
{"action": "execute", "message": "看这张图", "files": [{"id": 42, "mime_type": "image/png"}]}
```

下载 Agent 产出的文件（`file_id` 从 `flow_done.output_data` 提取）：

```bash
curl -o out.bin "http://host/api/ws-gateway/download/123?token=TOKEN"
```

**规则**：
- 单文件上限 `MAX_UPLOAD_SIZE`（默认 100MB）
- 上传的文件归属绑定到网关关联的 flow；下载严格校验同 flow 归属，不匹配报 `文件不存在或无权访问`

> 完整字段说明见 [references/api.md](references/api.md) 的「文件传输接口」。

## 远程工具注册

> **仅 Agent 类型支持**：网关必须关联「智能体」流程。关联「流程」类型的 Gateway 注册工具无效，Agent 无法发现和调用。

客户端注册函数工具后，Agent 执行中可反向调用：

```json
{
  "action": "register_tools",
  "tools": [
    {
      "name": "query_database",
      "description": "查询本地数据库",
      "parameters": {
        "type": "object",
        "properties": {
          "sql": {"type": "string", "description": "SQL查询语句"}
        },
        "required": ["sql"]
      }
    }
  ]
}
```

Agent 调用工具时，服务端发送：
```json
{"type": "tool_invoke", "data": {"call_id": "abc-123", "name": "query_database", "args": {"sql": "SELECT * FROM users"}}}
```

客户端执行后返回结果：
```json
{"action": "tool_result", "call_id": "abc-123", "result": "[{\"id\": 1, \"name\": \"张三\"}]"}
```

> ⚠️ **关键约束**：`result` 字段值必须是 **字符串**（不能是对象/数组）。如果本地函数返回 Python 对象/list，需用 `json.dumps(..., ensure_ascii=False)` 序列化为字符串后再发送。LLM 收到的就是这个字符串内容。

工具超时默认 120 秒。工具名直接使用客户端注册的原始名称。

> **工具注册约束**：
> - 每个工具的 `name` 必须**唯一**（在同一连接内）
> - `name` 建议符合 **Python 标识符规范**（字母数字下划线，不含空格/中文/特殊字符）
> - `description` 是 LLM 决定何时调用该工具的依据，**必须清晰描述工具功能和使用场景**
> - `parameters` 必须是合法 **JSON Schema**（含 `type: "object"` / `properties` / `required`），后端会用它校验 LLM 传入的参数
> - **可注册的工具有数量上限**（默认 50 个），超出时报错
> - 调用 `register_tools` 会**覆盖之前注册的所有工具**（不是增量），需要重新发送全部工具
> - `unregister_tools` 用于清空所有已注册工具（无 body）
> - 注册失败会收到 `{"type": "error", "data": {"message": "..."}}`

> **完整调用流程**：
> 1. 客户端连接 ws 后，先 `register_tools` 注册工具（带 JSON Schema 描述）
> 2. 客户端发 `execute` 触发执行
> 3. Agent 执行中需要工具时，服务端推 `tool_invoke` 事件，含 `call_id`/`name`/`args`
> 4. 客户端执行本地函数，将结果 `json.dumps()` 序列化为字符串，通过 `tool_result` 回传（必须带相同的 `call_id`）
> 5. 服务端把字符串结果传给 LLM，LLM 继续决策
> 6. 整个执行完成后，服务端推 `flow_done` 事件

## Python 客户端

**生产级标准底座：[references/ws_gateway_client.py](references/ws_gateway_client.py)** —— 写桥接客户端直接复制/导入这个文件，不要从零写。

`WsGatewayWSClient` 类已内置（均为实测踩坑修复）：

- **心跳保活**（30 秒 ping，防 4408 空闲断开）+ 单 `_loop` 独占 recv（websockets 只允许一个 recv 协程）
- **call_id 路由**：并发 execute 各自 Future 解锁；无 call_id 的 error 路由 FIFO 头
- **流式文本收集**：`execute()` 返回事件注入 `_collected_content`（node_content 累积，跳过 start/end）
- **断线解锁**：`ConnectionClosed` 时解锁全部挂起 Future（否则永久挂起）
- **`WsError` 异常**：携带 `error_code`，按错误码分支处理
- **工具 result 序列化**：dict/list 自动 `json.dumps`（协议要求字符串）；async 工具函数自动 await
- **`ConnectionError` vs `WsError` 分层**：外层重连循环只捕获前者

API 一览：`connect()` → `tool()`*N → `register_tools()` → `execute()` / `create_session()` / `resume()` / `tool_approval()` / `cancel()` / `close()`。

**教学示例：[references/ws_client_example.py](references/ws_client_example.py)** —— 11 个场景化示例（远程工具/文件传输/人工交互/审批/并发等），演示协议用法。注意示例 4 内嵌的简化类**不含上述修复**，集成项目时以 `ws_gateway_client.py` 为准。

```python
from ws_gateway_client import WsGatewayWSClient, WsError

client = WsGatewayWSClient("ws://host/ws/trigger/TOKEN")
info = await client.connect()          # {"flow_type": "agent", ...}
client.tool("send_file", "发送文件给用户", {...schema...}, send_file_fn)
await client.register_tools()          # 显式注册（Agent 类型）
event = await client.execute("你好")
reply = event.get("_collected_content")  # 流式累积的回复文本
```

```python
import asyncio
import json
import websockets

async def main():
    async with websockets.connect("ws://host/ws/trigger/TOKEN") as ws:
        # 注册远程工具
        await ws.send(json.dumps({
            "action": "register_tools",
            "tools": [{"name": "get_time", "description": "获取当前时间",
                       "parameters": {"type": "object", "properties": {}}}]
        }))

        # 发送执行指令
        await ws.send(json.dumps({"action": "execute", "message": "现在几点？"}))

        # 接收事件
        async for msg in ws:
            data = json.loads(msg)
            if data["type"] == "tool_invoke":
                # Agent 调用了远程工具
                await ws.send(json.dumps({
                    "action": "tool_result",
                    "call_id": data["data"]["call_id"],
                    "result": "14:30"
                }))
            elif data["type"] == "flow_done":
                print("执行完成")
                break

asyncio.run(main())
```

## 完整协议详情

见 [references/api.md](references/api.md)。

## 客户端示例代码

见 [references/ws_client_example.py](references/ws_client_example.py)，包含 11 个完整示例：

| 编号 | 名称 | 演示内容 |
|------|------|---------|
| 1 | 最简执行 | 连接 → 发消息 → 逐 token 接收流式回复 |
| 2 | 远程工具 | 注册 `get_local_time`/`calculate` 函数，Agent 调用后回传结果 |
| 3 | 会话管理 | 创建多会话、多轮对话、切换、列表 |
| 4 | 封装客户端类 | 后台心跳 + 自动处理 `tool_invoke`，execute 按 call_id 返回 Future，适合集成到实际项目 |
| 5 | 指定 session_id 继续 | 用已知 session_id 跨连接恢复上下文（先创建，后恢复） |
| 6 | 文件传输 | 上传本地图片 → 带 `file_id` 执行 → 下载 Agent 产物（依赖 httpx） |
| 7 | 文件工具 | 注册文件处理工具，前端聊天触发时双向传输文件（依赖 httpx） |
| 8 | 人工交互 resume | `human_input_required`/`waiting_human` → 征询输入 → `resume` 恢复（支持多轮） |
| 9 | 工具审批 tool_approval | `tool_approval_required` → 征询批准/拒绝 → `tool_approval` 恢复 |
| 10 | 取消执行 cancel | 执行中 `cancel` → `cancel_accepted` → `flow_done(status=cancelled)` |
| 11 | 并发多会话 + call_id | 双会话并发 execute、同会话锁拒绝演示、事件按 `call_id` 分流、30 秒心跳保活 |

运行方式：

```bash
pip install websockets

# 交互式选择示例
WS_TOKEN=你的token python references/ws_client_example.py

# 直接运行指定示例
WS_TOKEN=你的token python references/ws_client_example.py 2     # 远程工具
WS_TOKEN=你的token python references/ws_client_example.py 8     # 人工交互 resume
WS_TOKEN=你的token python references/ws_client_example.py 9     # 工具审批
WS_TOKEN=你的token python references/ws_client_example.py 10    # 取消执行
WS_TOKEN=你的token python references/ws_client_example.py 11    # 并发多会话

# 示例 5：先创建会话
WS_TOKEN=你的token python references/ws_client_example.py 5
# 示例 5：用返回的 session_id 恢复
WS_TOKEN=你的token python references/ws_client_example.py 5 123

# 示例 6/7 需额外安装 httpx，并准备本地文件
pip install httpx
WS_TOKEN=你的token python references/ws_client_example.py 6     # 文件传输
WS_TOKEN=你的token python references/ws_client_example.py 7     # 文件工具（前端触发）
```

## 错误码枚举（`error.data.error_code`）

所有 `error` 事件必带 `data.error_code`（机器可读枚举），客户端**必须按 error_code 判断**，**不要匹配 message 字符串**。

| error_code | 含义 | 客户端处理建议 |
|---|---|---|
| `SESSION_INVALID` | 会话失效/被删/不属于该网关/Agent | **删本地 session_id，自动重建会话并重试** |
| `EXECUTION_INVALID` | 执行记录失效/不可取消 | 提示用户该执行不可用 |
| `GATEWAY_NOT_FOUND` | 网关不存在 | 致命错误，需 PM 介入检查网关配置 |
| `NOT_AGENT_TYPE` | 非 Agent 类型不支持该操作 | 检查网关 flow 配置 |
| `SESSION_BUSY` | 会话正在执行中 | 短暂等待后重试 |
| `MISSING_FIELD` | 缺少必填字段 | 检查客户端请求 |
| `INVALID_PARAMS` | 参数校验失败 | 检查客户端请求 |
| `INVALID_JSON` | 消息不是合法 JSON | 检查客户端序列化 |
| `UNKNOWN_ACTION` | 未知 action 指令 | 检查客户端版本 |
| `IDLE_TIMEOUT` | 空闲超时（连接即将关闭） | 重连并重发待处理请求 |
| `INTERNAL_ERROR` | 内部异常（兜底） | 记录错误消息后人工排查 |
| `CONNECTION_CLOSED` | ws 连接被关闭（客户端内部封装使用） | 重连 |

**典型反模式**（不要这样写）：
```python
# ❌ 靠 message 字符串判断
if "正在执行中" in event["data"].get("message", ""):
    ...

# ✅ 靠 error_code 判断
if event["data"].get("error_code") == "SESSION_BUSY":
    ...
```

**完整协议字段**见 [references/api.md](references/api.md)。
