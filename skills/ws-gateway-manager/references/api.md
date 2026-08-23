# WebSocket 网关 API 参考

## 管理接口（HTTP，需登录态）

### 创建网关

```
POST /api/ws-gateway/create
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
GET /api/ws-gateway/get/{id}/url
```

```json
{"code": 1, "data": {"url": "/ws/trigger/abc123...", "token": "abc123..."}}
```

---

## 文件传输接口（HTTP，token 鉴权）

外部客户端通过网关 token 上传/下载文件，**无需登录态**（端点已加入认证豁免白名单，由 token 自鉴权）。上传的文件归属绑定到网关关联的 flow，下载时严格校验同 flow 归属。

### upload — 上传文件

```
POST /api/ws-gateway/upload?token={token}
Content-Type: multipart/form-data
```

表单字段：

| 字段 | 类型 | 说明 |
|------|------|------|
| `file` | File | 上传的文件（必填） |

鉴权：query 参数 `token` 为网关 token。token 无效或网关已禁用返回 `token 无效或网关已禁用`。

响应：

```json
{
  "code": 1,
  "data": {
    "file_id": 42,
    "download_url": "/api/ws-gateway/download/42?token=abc123...",
    "mime_type": "image/png",
    "file_size": 10240
  },
  "msg": "上传成功"
}
```

**规则**：
- 单文件大小限制由 `MAX_UPLOAD_SIZE` 控制（默认 100MB），超限返回 `文件大小 X.XMB 超过限制（最大 100MB）`
- 文件 `source_type` 取网关关联 flow 的 `flow_type`（无则 `flow`），`flow_id` 绑定为网关的 `flow_id`
- 返回的 `file_id` 可塞进 `execute` 指令的 `files` 字段供 Agent 使用

### 在 execute 中引用上传的文件

```json
{
  "action": "execute",
  "message": "看这张图，描述一下内容",
  "files": [{"id": 42, "mime_type": "image/png"}]
}
```

`files[].id` 即 upload 返回的 `file_id`，Agent 据此读取文件内容（多模态模型可直接识别图片）。

### download — 下载文件

```
GET /api/ws-gateway/download/{file_id}?token={token}
```

鉴权：query 参数 `token` 为网关 token。

**严格归属校验**：文件的 `flow_id` 必须等于该网关关联的 `flow_id`，否则返回 `文件不存在或无权访问`。

成功返回文件二进制流（`FileResponse`，含原文件名与 MIME 类型）。文件记录不存在或磁盘文件丢失返回 `文件不存在`。

### connected 事件携带的文件端点模板

连接建立后服务端推送的 `connected` 事件中带两个字段，客户端可直接取用，无需自行拼接：

```json
{
  "type": "connected",
  "data": {
    "gateway_id": 1,
    "flow_id": 1,
    "flow_type": "agent",
    "upload_url": "/api/ws-gateway/upload?token=abc123...",
    "download_url_template": "/api/ws-gateway/download/{file_id}?token=abc123..."
  }
}
```

`download_url_template` 中的 `{file_id}` 占位符用实际文件 ID 替换。

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
- 工具名直接使用客户端注册的原始名称（如 `query_db`）
- 仅 Agent 类型支持远程工具
- 重复注册会覆盖之前的注册
- 工具超时默认 120 秒

### unregister_tools — 注销所有远程工具

```json
{"action": "unregister_tools"}
```

### resume — 恢复等待人工输入的执行

Agent 类型（传 session_id）：
```json
{"action": "resume", "session_id": 5, "input": "同意，按方案A执行"}
```

Flow 类型（execution_id 取自 `waiting_human` 事件的 `data.execution_id`）：
```json
{"action": "resume", "execution_id": 12, "input": "确认"}
```

`input` 必填（人工输入内容）。恢复可能再次触发中断（多轮交互），继续 resume 即可。响应事件流与 execute 同构（`call_started` 的 `data` 含 `resumed: true`）。

### tool_approval — 确认/拒绝待审批的工具调用（仅 Agent）

```json
{"action": "tool_approval", "session_id": 5, "result": "approved"}
```

- `result`：`approved`（放行执行）或 `rejected`（拒绝，工具收到拒绝结果后 LLM 继续推理）
- 前置事件：执行中下发 `tool_approval_required`（含 `data.tool_calls` 待审批列表）
- 响应：`{"type": "tool_approval_result", "data": {"session_id": 5, "resolved": true}}`
- `resolved=false` 表示当前无待审批工具（已超时或已完成），可安全忽略

### cancel — 取消正在执行的会话/执行记录

Agent 类型：
```json
{"action": "cancel", "session_id": 5}
```

Flow 类型：
```json
{"action": "cancel", "execution_id": 12}
```

- 响应：`{"type": "cancel_accepted", "data": {...}}`，随后执行终止下发 `flow_done`（`status=cancelled`）
- Flow 仅 RUNNING / WAITING_HUMAN 状态可取消，否则返回错误事件
- 目标不存在或不属于该网关时返回错误事件

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

响应中的每条消息都包含 `message_type` 和 `removed_count`：

```json
{
  "type": "messages_list",
  "data": {
    "messages": [
      {
        "id": 101,
        "role": "human",
        "message_type": "context_summary",
        "content": "用户正在排查订单同步问题……",
        "removed_count": 18,
        "create_time": "2026-08-23T10:00:00"
      }
    ],
    "total": 1
  }
}
```

`message_type="context_summary"` 表示上下文压缩摘要，`removed_count` 是被
压缩的历史消息数量。普通消息的这两个字段为 `null`。

### delete_message — 删除会话消息

```json
{"action": "delete_message", "session_id": 5, "message_id": 100}
```

删除 `message_id` 及其后所有消息，自动清理 checkpoint。

### ping — 心跳

纯文本 `ping`，服务端回纯文本 `pong`。

**必须周期发送**（建议 30 秒间隔）：连接空闲超过 `WS_TRIGGER_IDLE_TIMEOUT`（默认 120 秒，0 关闭）未收到任何消息时，服务端以关闭码 `4408` 断开。

---

## 服务端 → 客户端事件

### 连接管理事件

| type | 说明 |
|------|------|
| `connected` | 连接确认，含 gateway_id/flow_id/flow_type/upload_url/download_url_template |
| `call_started` | 执行开始，含 call_id/session_id；之后的所有执行事件顶层携带 `call_id`（并发时按其路由） |
| `tools_registered` | 工具注册确认 |
| `tools_unregistered` | 工具注销确认 |
| `tool_approval_result` | tool_approval 指令回执（`data.resolved` 是否命中待审批） |
| `cancel_accepted` | cancel 指令已受理（随后下发 flow_done status=cancelled） |
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
| `waiting_human` | execution_id, node_key, question, wait_data | Flow Human 节点等待输入（用 resume + execution_id 恢复） |
| `human_input_required` | question, context | Agent 人工协助中断（用 resume + session_id 恢复） |
| `tool_approval_required` | node_key, tool_calls, approval_needed | 工具调用待审批（用 tool_approval 指令确认/拒绝） |

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
| 4408 | 连接空闲超时（默认 120 秒未收到任何消息，含 ping） |
| 4409 | Agent 类型已有活跃连接（全局单连接） |

### 执行阶段错误

```json
{"type": "error", "data": {"message": "会话 5 正在执行中，请等待完成"}}
```

常见错误：
- `会话 X 正在执行中，请等待完成` — 同一会话并发保护（不同会话/新建会话可并发）
- `会话 X 不存在或不属于该网关` — session_id 无效或归属校验失败
- `执行记录 X 不存在或不属于该网关流程` — execution_id 无效或归属校验失败
- `仅 Agent 类型支持创建会话` — Flow 类型不支持会话操作
- `result 必须为 approved 或 rejected` — tool_approval 参数错误
- `远程工具 X 执行超时（120秒）` — tool_result 未在超时内返回
