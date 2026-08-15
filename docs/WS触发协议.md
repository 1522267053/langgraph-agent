# WS 触发协议 — 外部客户端接入指南

> 面向外部客户端（IM 桥接、脚本、第三方系统集成）的 WebSocket 触发协议完整参考。
>
> 对应实现：`app/api/ws_trigger_api.py`、`app/services/ws_gateway_service.py`

---

## 一、概述与连接

### 1. 端点

```
ws://<host>:<port>/ws/trigger/{token}
```

- `token` 在平台「网关管理」页面创建网关时自动生成，也可通过 `GET /api/ws-gateway/get/{id}/url` 获取
- 一个网关绑定一个 Flow 或 Agent：
  - **Flow 类型**：允许同一 token 多条连接并发
  - **Agent 类型**：全局仅允许一条活跃连接（多条连接供远程工具注入，见「远程工具」），重复连接以 `4409` 关闭

### 2. 握手

连接建立后服务端立即下发 `connected` 事件：

```json
{
  "type": "connected",
  "data": {
    "gateway_id": 1,
    "gateway_name": "elink-bridge",
    "flow_id": 3,
    "flow_type": "agent",
    "upload_url": "/api/ws-gateway/upload?token=xxx",
    "download_url_template": "/api/ws-gateway/download/{file_id}?token=xxx"
  }
}
```

### 3. 关闭码

| 关闭码 | 含义 |
|-------|------|
| `4404` | token 无效（网关不存在） |
| `4403` | 网关已禁用（`is_enabled=false`） |
| `4409` | Agent 类型已有活跃连接 |

### 4. 心跳

客户端发送纯文本 `ping`，服务端回复纯文本 `pong`（非 JSON）。

---

## 二、并发模型

| 场景 | 行为 |
|------|------|
| Agent + execute 显式携带 `session_id` | **同一会话串行**：正在执行时新请求被拒绝（`error` 事件「会话 X 正在执行中」）；不同会话并发执行 |
| Agent + execute 未携带 `session_id` | 新建会话，全新 session_id 无冲突，不加锁 |
| Flow | 每次 execute 新建独立 execution，**天然并发**，无锁 |

### call_id 事件路由（并发必读）

并发执行时事件流会在同一条连接上交错。**每个执行类事件顶层统一携带 `call_id`**（= 调用记录 ID，`call_started` 事件中首次下发），客户端必须按 `call_id` 将事件路由到对应的执行上下文：

```json
{ "type": "node_content", "call_id": 42, "data": { "node_key": "llm_1", "content": "..." } }
```

### session_id 解析优先级

1. execute 请求显式携带的 `session_id`
2. 否则使用连接当前会话 `current_session_id`（由 `create_session`/`switch_session`/未显式指定会话的 execute 设置）

> 多会话客户端（如 IM 桥接：chat_id → session_id 映射）应**始终显式携带 `session_id`**，避免依赖连接级当前会话。

---

## 三、客户端指令

所有指令为 JSON 文本，必须包含 `action` 字段。响应为 JSON 事件（`type` 字段区分）。

### 1. execute — 触发执行

```json
{
  "action": "execute",
  "session_id": 15,
  "message": "帮我总结今天的群消息"
}
```

| 字段 | 说明 |
|------|------|
| `session_id` | 可选。Agent 类型：指定会话则续接历史，不指定则新建会话。Flow 类型忽略（每次独立执行） |
| 其余字段 | 合并进输入数据：网关配置的 `input_config` 为底，请求字段覆盖（`action`/`session_id` 除外）。Agent 类型约定 `message` 为用户消息，其余字段作为参数传入 |

响应事件流：`call_started` → 执行事件（见「服务端事件」）→ `flow_done` / `error`。

`call_started` 示例：

```json
{ "type": "call_started", "data": { "call_id": 42, "session_id": 15 } }
```

- Agent 新建会话时 `session_id` 为新会话 ID（请求未显式指定时客户端应记录，后续 execute 续接）
- Flow 类型 `session_id` 恒为 `0`

### 2. resume — 恢复等待人工输入的执行

Agent/Flow 执行中触发人工交互（Human 节点 `interrupt()`）后，执行挂起等待输入；客户端收集到用户输入后通过 `resume` 恢复：

```json
{
  "action": "resume",
  "session_id": 15,
  "input": "同意，按方案A执行"
}
```

| 字段 | 说明 |
|------|------|
| `session_id` | **Agent 类型必填**：待恢复的会话 ID |
| `execution_id` | **Flow 类型必填**：待恢复的执行记录 ID（来自 `waiting_human` 事件的 `data.execution_id`） |
| `input` | 必填。人工输入内容（字符串） |

- Agent 会话与 execute 共用会话级锁（正在执行/恢复中时拒绝）
- Flow 由执行记录的 CAS 乐观锁保证并发安全（被其他请求抢占时返回错误「执行已被其他请求抢占」）
- 恢复可能再次触发 `human_input_required`（多轮交互），继续 resume 即可
- 响应事件流与 execute 同构（`call_started` 的 `data` 含 `resumed: true` 和 `session_id`/`execution_id`）

### 3. register_tools — 注册远程工具（仅 Agent 类型）

客户端注册的函数可被 Agent 在执行中反向调用（服务端下发 `tool_invoke`，客户端返回 `tool_result`）：

```json
{
  "action": "register_tools",
  "tools": [
    {
      "name": "send_reply",
      "description": "向 eLink 聊天发送回复消息",
      "parameters": {
        "type": "object",
        "properties": {
          "chat_id": { "type": "string", "description": "聊天ID" },
          "content": { "type": "string", "description": "消息内容" }
        },
        "required": ["chat_id", "content"]
      }
    }
  ]
}
```

| 字段 | 说明 |
|------|------|
| `name` | 工具名（直接暴露给 LLM，建议语义化命名） |
| `description` | 工具描述（LLM 据此决定是否调用） |
| `parameters` | JSON Schema（支持 string/number/integer/boolean/array/object，不支持的类型回退为 string） |

- 响应：`{"type": "tools_registered", "data": {"count": 1, "names": ["send_reply"]}}`
- 工具超时：120 秒，超时返回 `{"success": false, "error": "远程工具 xx 执行超时（120秒）"}`
- Flow 类型网关返回错误「远程工具仅 Agent 类型支持」

**tool_invoke / tool_result 回调协议**

Agent 调用远程工具时服务端下发：

```json
{
  "type": "tool_invoke",
  "data": { "call_id": "a1b2c3...", "name": "send_reply", "args": { "chat_id": "R:1001", "content": "收到" } }
}
```

客户端执行完毕后回传（`call_id` 必须匹配）：

```json
{
  "action": "tool_result",
  "call_id": "a1b2c3...",
  "result": "{\"success\": true}"
}
```

失败时回传 `error` 字段：

```json
{ "action": "tool_result", "call_id": "a1b2c3...", "error": "发送失败" }
```

- `result` 为字符串（推荐 JSON 字符串，与内置工具返回格式一致）
- 并发执行时多个 `tool_invoke` 可能交错，`call_id` 为 uuid，按其匹配

### 4. unregister_tools — 注销远程工具

```json
{ "action": "unregister_tools" }
```

响应：`{"type": "tools_unregistered", "data": {}}`

### 5. 会话管理指令（仅 Agent 类型）

| action | 参数 | 响应事件 | 说明 |
|--------|------|---------|------|
| `create_session` | `title`（可选，默认 `[WS] 网关名`） | `session_created` | 新建会话并设为连接当前会话 |
| `switch_session` | `session_id` | `session_switched` | 切换连接当前会话（校验归属该网关） |
| `list_sessions` | `page`（默认1）、`page_size`（默认20） | `sessions_list` | 分页查询该网关创建的会话 |
| `delete_session` | `session_id` | `session_deleted` | 删除会话（含消息和 checkpoint） |
| `get_messages` | `session_id`、`before_id`（游标）、`limit`（默认20） | `messages_list` | 查询会话历史消息（游标分页） |
| `delete_message` | `session_id`、`message_id` | `message_deleted` | 删除 message_id 及其后所有消息（含 checkpoint 清理） |

---

## 四、服务端事件总览

### 连接与指令回执

| type | 触发时机 |
|------|---------|
| `connected` | 连接建立 |
| `error` | 指令错误/执行异常（`data.message` 为错误信息） |
| `call_started` | execute/resume 开始（`data.call_id`、`data.session_id`） |
| `tools_registered` / `tools_unregistered` | 工具注册/注销回执 |
| `tool_invoke` | Agent 反向调用远程工具 |
| `session_*` / `messages_list` / `message_deleted` | 会话管理回执 |

### 执行事件（转发自执行引擎）

> 以下事件在并发执行时顶层携带 `call_id`；`data` 字段以实际下发为准，常用字段列举如下。

| type | data 常用字段 | 说明 |
|------|--------------|------|
| `flow_start` | `flow_id`、`execution_id` | Flow 执行开始 |
| `resume_start` | `execution_id` | Flow 恢复执行开始 |
| `flow_done` | `execution_id`、`status`（success/failed/cancelled）、`output_data` | 执行完成（Agent 的 execution_id 为 session_id） |
| `node_start` | `node_key`、`node_type`、`node_name` | 节点开始 |
| `node_done` | `node_key`、`node_type`、`output_data`、`error` | 节点完成 |
| `node_thinking` | `node_key`、`content` | LLM 推理过程（thinking 模型流式输出） |
| `node_content` | `node_key`、`content` | LLM 回答内容（流式增量） |
| `tool_call_start` | `node_key`、`tool_name`、`tool_args` | 工具调用开始 |
| `tool_call_end` | `node_key`、`tool_name`、`status`、`result` | 工具调用结束 |
| `tool_approval_required` | `node_key`、`tool_calls`、`approval_needed` | 工具调用需要审批（SSE 流内确认） |
| `human_input_required` | `type`、`question`、`context`、`tool_call_id` | Agent 人工交互 interrupt（用 resume 恢复） |
| `waiting_human` | `execution_id`、`node_key`、`question`、`context`、`wait_data` | Flow Human 节点等待输入（用 resume 恢复） |
| `token_usage` | `prompt_tokens`、`completion_tokens`、`total_tokens`、`model`、`provider` | Token 用量 |
| `tool_call_limit` | `node_key`、`max_iterations` | 工具调用达到最大迭代次数 |
| `todo_update` | `todos` | 任务计划更新 |
| `llm_retry` | `message`、`retry_count`、`max_retries`、`wait_seconds` | LLM 重试 |
| `context_compressing` | `status`、`removed_count` | 上下文压缩 |
| `flow_preview` | `flow_id`、`flow_name`、`action`、`nodes`、`edges` | 流程变更预览 |
| `error` | `message`、`execution_id`、`node_key` | 执行错误 |

---

## 五、人工确认闭环（时序）

```
客户端                          平台
  │ ── execute(session_id) ──→ │
  │ ←─ call_started ────────── │
  │ ←─ node_start ... ──────── │
  │ ←─ human_input_required ── │  Agent interrupt()
  │      （向真实用户征询输入）    │
  │ ── resume(session_id, ───→ │  恢复执行
  │        input="同意")        │
  │ ←─ node_content ... ────── │  （可能再次 interrupt，多轮交互）
  │ ←─ flow_done ───────────── │
```

Flow 类型将 `session_id` 换为 `execution_id`（取自 `waiting_human` 事件）。

---

## 六、文件传输

文件端点通过 URL 中的 `token` 自鉴权，无需登录态（已豁免平台认证中间件）。

### 上传

```
POST /api/ws-gateway/upload?token={token}
Content-Type: multipart/form-data

file: <二进制>
```

响应（`ApiResponse` 包装，`code=1` 成功）：

```json
{
  "code": 1,
  "data": {
    "file_id": 7,
    "download_url": "/api/ws-gateway/download/7?token=xxx",
    "mime_type": "text/markdown",
    "file_size": 1024
  }
}
```

上传的文件归属绑定到网关关联的 Flow/Agent。execute 时在请求字段中携带 `files`（`file_id` 列表）即可作为附件输入。

### 下载

```
GET /api/ws-gateway/download/{file_id}?token={token}
```

严格校验文件归属该网关关联的 Flow，跨网关访问返回错误。

---

## 七、最小接入示例（Python）

```python
import asyncio
import json
import uuid
import websockets


async def main():
    uri = "ws://127.0.0.1:8000/ws/trigger/<你的token>"
    async with websockets.connect(uri) as ws:
        # 注册远程工具（可选）
        await ws.send(json.dumps({
            "action": "register_tools",
            "tools": [{
                "name": "send_reply",
                "description": "发送回复到聊天",
                "parameters": {
                    "type": "object",
                    "properties": {"content": {"type": "string"}},
                    "required": ["content"],
                },
            }],
        }))

        # 触发执行（显式携带 session_id 以支持并发）
        await ws.send(json.dumps({
            "action": "execute",
            "session_id": 15,
            "message": "帮我总结今天的群消息",
        }))

        async for raw in ws:
            if raw == "pong":
                continue
            event = json.loads(raw)

            # 远程工具回调
            if event["type"] == "tool_invoke":
                d = event["data"]
                # ... 实际执行工具逻辑 ...
                await ws.send(json.dumps({
                    "action": "tool_result",
                    "call_id": d["call_id"],
                    "result": json.dumps({"success": True}, ensure_ascii=False),
                }))
                continue

            # 人工交互 → 征询用户后 resume
            if event["type"] == "human_input_required":
                answer = input(event["data"].get("question", "") + " > ")
                await ws.send(json.dumps({
                    "action": "resume",
                    "session_id": 15,
                    "input": answer,
                }))
                continue

            # 按 call_id 路由（并发时事件交错）
            call_id = event.get("call_id")
            print(f"[call={call_id}] {event['type']}: {event.get('data', {})}")

            if event["type"] == "flow_done":
                break


asyncio.run(main())
```

> 多会话客户端建议：chat_id → session_id 一一映射，execute 显式携带 `session_id`，同会话收到「正在执行中」错误时本地排队重试，事件按 `call_id` 分发回对应 chat。
