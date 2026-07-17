# WebSocket WebSocket 网关 API 参考

## 管理接口（HTTP，需登录态）

### 创建网关

```
POST /api/gateway/create
```

```json
{
  "name": "订单处理",
  "flow_id": 1,
  "description": "处理外部订单",
  "input_config": {"message": "默认消息"},
  "is_enabled": 1
}
```

响应中 `token` 为 WebSocket 连接凭据。

### 获取 WebSocket 地址

```
GET /api/gateway/get/{id}/url
```

```json
{"code": 1, "data": {"url": "/ws/trigger/abc123...", "token": "abc123..."}}
```

---

## WebSocket 触发协议

### 连接

```
ws://host/ws/trigger/{token}
```

连接成功后收到 `connected` 事件，之后可发送 JSON 指令。

---

## 客户端 → 服务端指令

### execute — 发送执行指令

Agent 类型（通过 message 字段）：
```json
{"action": "execute", "message": "查询天气", "city": "北京"}
```

指定已有会话：
```json
{"action": "execute", "message": "继续", "session_id": 5}
```

Flow 类型（直接传参数，作为 input_data）：
```json
{"action": "execute", "city": "北京", "date": "2026-07-15"}
```

**输入合并**：`input_data = {**gateway.input_config, **指令参数}`（排除 `action` 和 `session_id`）。

### register_tools — 注册远程工具

```json
{
  "action": "register_tools",
  "tools": [
    {
      "name": "query_db",
      "description": "查询数据库",
      "parameters": {
        "type": "object",
        "properties": {
          "sql": {"type": "string", "description": "SQL语句"}
        },
        "required": ["sql"]
      }
    },
    {
      "name": "send_email",
      "description": "发送邮件",
      "parameters": {
        "type": "object",
        "properties": {
          "to": {"type": "string"},
          "subject": {"type": "string"},
          "body": {"type": "string"}
        },
        "required": ["to", "subject", "body"]
      }
    }
  ]
}
```

响应：
```json
{"type": "tools_registered", "data": {"count": 2, "names": ["query_db", "send_email"]}}
```

**规则**：
- 工具名自动加 `remote__` 前缀（如 `remote__query_db`），避免与流程内工具冲突
- 仅 Agent 类型支持远程工具
- 重复注册会覆盖之前的注册
- 工具超时默认 120 秒

### unregister_tools — 注销所有远程工具

```json
{"action": "unregister_tools"}
```

### tool_result — 返回工具执行结果

成功：
```json
{"action": "tool_result", "call_id": "abc-123", "result": "查询结果内容"}
```

失败：
```json
{"action": "tool_result", "call_id": "abc-123", "error": "数据库连接失败"}
```

`call_id` 对应服务端 `tool_invoke` 事件中的 `call_id`。

### create_session — 创建新会话（仅 Agent）

```json
{"action": "create_session", "title": "新对话"}
```

响应：
```json
{"type": "session_created", "data": {"session_id": 10, "title": "新对话"}}
```

### switch_session — 切换会话

```json
{"action": "switch_session", "session_id": 5}
```

响应：
```json
{"type": "session_switched", "data": {"session_id": 5}}
```

校验会话是否属于该网关（`gateway_id` 匹配）。

### list_sessions — 查询会话列表

```json
{"action": "list_sessions", "page": 1, "page_size": 20}
```

响应：
```json
{
  "type": "sessions_list",
  "data": {
    "sessions": [{"id": 5, "title": "[WS] 订单处理", "create_time": "2026-07-15T10:00:00"}],
    "total": 1
  }
}
```

### delete_session — 删除会话

```json
{"action": "delete_session", "session_id": 5}
```

同时清理会话消息和 LangGraph checkpoint。

### get_messages — 查询会话历史消息

```json
{"action": "get_messages", "session_id": 5, "limit": 20}
```

游标分页（向上翻页）：
```json
{"action": "get_messages", "session_id": 5, "limit": 20, "before_id": 100}
```

### delete_message — 删除会话消息

```json
{"action": "delete_message", "session_id": 5, "message_id": 100}
```

删除 `message_id` 及其后所有消息，自动清理 checkpoint。

### ping — 心跳

纯文本 `ping`，服务端回纯文本 `pong`。

---

## 服务端 → 客户端事件

### 连接管理事件

| type | 说明 |
|------|------|
| `connected` | 连接确认，含 gateway_id/flow_id/flow_type |
| `call_started` | 执行开始，含 call_id/session_id |
| `tools_registered` | 工具注册确认 |
| `tools_unregistered` | 工具注销确认 |
| `session_created` | 会话创建成功 |
| `session_switched` | 会话切换成功 |
| `sessions_list` | 会话列表查询结果 |
| `session_deleted` | 会话删除成功 |
| `messages_list` | 消息列表查询结果 |
| `message_deleted` | 消息删除成功 |
| `error` | 错误信息 |
| `pong`（纯文本） | 心跳响应 |

### 执行流式事件（同 SSE 事件格式）

| type | data 关键字段 | 说明 |
|------|-------------|------|
| `flow_start` | flow_id, execution_id | 执行开始 |
| `node_start` | node_key, node_type, node_name | 节点开始 |
| `node_thinking` | node_key, content | LLM 思考链（逐段） |
| `node_content` | node_key, content | LLM 正文输出（逐 token） |
| `tool_call_start` | node_key, tool_name, tool_args | 工具调用开始 |
| `tool_call_end` | node_key, tool_name, status, result | 工具调用结束 |
| `token_usage` | prompt_tokens, completion_tokens, total_tokens | Token 用量 |
| `node_done` | node_key, node_type | 节点结束 |
| `flow_done` | execution_id, status, output_data | **执行完成**（status: success/cancelled/failed） |
| `error` | message | 执行错误 |
| `waiting_human` | node_key, question, wait_data | 等待人工输入（中断） |

### tool_invoke — 远程工具调用请求

```json
{
  "type": "tool_invoke",
  "data": {
    "call_id": "abc-123",
    "name": "query_db",
    "args": {"sql": "SELECT * FROM users"}
  }
}
```

客户端需在 120 秒内返回 `tool_result`，否则超时失败。

---

## 错误处理

### 连接阶段

| 关闭码 | 说明 |
|--------|------|
| 4404 | token 无效（网关不存在） |
| 4403 | Gateway 已禁用 |

### 执行阶段错误

```json
{"type": "error", "data": {"message": "正在执行中，请等待完成"}}
```

常见错误：
- `正在执行中，请等待完成` — 并发保护
- `会话 X 不存在或不属于该 Agent` — session_id 无效
- `仅 Agent 类型支持创建会话` — Flow 类型不支持会话操作
- `远程工具 X 执行超时（120秒）` — tool_result 未在超时内返回
