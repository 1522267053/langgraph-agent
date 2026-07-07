# Webhook API 参考

Base URL: `http://127.0.0.1:8000` | 响应: `{code:1, msg, data}` 成功 / `{code:0, msg}` 失败

## 接口总览

| 方法 | 路径 | 认证 | 说明 |
|------|------|:--:|------|
| POST | `/api/webhook/page` | ✅ | 分页列表 |
| POST | `/api/webhook/create` | ✅ | 创建 |
| POST | `/api/webhook/update` | ✅ | 更新 |
| GET | `/api/webhook/delete/{id}` | ✅ | 软删除 |
| GET | `/api/webhook/get/{id}/url` | ✅ | 获取触发 URL |
| POST | `/api/webhook/trigger/{token}` | ❌ | 外部触发 |
| GET | `/api/webhook/query/{token}/calls` | ❌ | 调用记录列表 |
| GET | `/api/webhook/query/{token}/calls/{call_id}` | ❌ | 调用记录详情 |
| GET | `/api/webhook/query/{token}/calls/{call_id}/messages` | ❌ | 调用消息列表 |
| GET | `/api/webhook/query/{token}/sessions` | ❌ | Webhook 创建的会话列表 |
| GET | `/api/webhook/query/{token}/sessions/{session_id}` | ❌ | 会话详情 |
| GET | `/api/webhook/query/{token}/sessions/{session_id}/delete` | ❌ | 删除会话 |
| GET | `/api/webhook/query/{token}/sessions/{session_id}/messages` | ❌ | 会话消息列表 |
| GET | `/api/webhook/query/{token}/sessions/{session_id}/messages/{message_id}/delete` | ❌ | 删除会话消息 |

---

## Webhook 配置 CRUD

### POST /api/webhook/create

创建 Webhook（自动生成 token）。

**请求体：**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|:----:|------|
| `flow_id` | int | ✅ | — | 关联流程/Agent ID |
| `name` | string | ✅ | — | Webhook 名称 |
| `description` | string | ❌ | null | 描述 |
| `input_config` | object | ❌ | null | 默认输入参数模板（触发时与请求体合并） |
| `callback_url` | string | ❌ | null | 执行完成后的回调 URL |
| `is_enabled` | int | ❌ | 1 | 0=禁用，1=启用 |

**响应：**
```json
{
  "code": 1,
  "data": {
    "id": 1,
    "flow_id": 1,
    "name": "订单处理",
    "token": "a1b2c3d4e5f6...",
    "description": "...",
    "input_config": {"message": "请处理新订单"},
    "callback_url": "https://external.com/callback",
    "is_enabled": 1,
    "call_count": 0,
    "last_call_time": null,
    "create_time": "2026-06-21T08:00:00"
  }
}
```

### POST /api/webhook/update

更新 Webhook（`exclude_unset`，未传字段保持不变）。

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `id` | int | ✅ | Webhook ID |
| `name` | string | ❌ | 名称 |
| `description` | string | ❌ | 描述 |
| `input_config` | object | ❌ | 输入模板 |
| `callback_url` | string | ❌ | 回调 URL |
| `is_enabled` | int | ❌ | 启用状态 |

### POST /api/webhook/page

分页查询。

**请求体：**
```json
{
  "page": 1,
  "page_size": 20,
  "name": "订单",
  "flow_id": null,
  "is_enabled": null
}
```

**查询条件：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 名称关键词（LIKE 模糊匹配） |
| `flow_id` | int | 精确匹配流程 ID |
| `is_enabled` | int | 精确匹配启用状态 |

### GET /api/webhook/delete/{id}

软删除（设置 `is_delete=1`）。

### GET /api/webhook/get/{id}/url

获取触发 URL。

**响应：**
```json
{
  "code": 1,
  "data": {
    "url": "/api/webhook/trigger/a1b2c3d4...",
    "token": "a1b2c3d4..."
  }
}
```

---

## 触发接口

### POST /api/webhook/trigger/{token}

**免认证**，通过 URL 中的 token 鉴权。

**请求体：** 任意 JSON 对象（可选）
```json
{
  "message": "查询订单状态",
  "order_id": "ORD-2026-001"
}
```

**会话控制（Agent 类型）：** 可通过 `session_id` 字段指定目标会话（不传则新建）
```json
{
  "session_id": 5,
  "message": "继续上次的对话"
}
```
`session_id` 是控制参数，不进入流程输入。仅 Agent 类型有效，Flow 类型忽略。

> **会话标记**：仅当「未传 `session_id` 由 Webhook 新建会话」时，会话写入 `webhook_id` 标记，后续可通过会话查询接口查到。外部传入复用的用户会话不写入标记，不会出现在 Webhook 会话列表中。

**输入合并规则：** `{...(webhook.input_config), ...request_body}`（`session_id` 已被提取，不参与合并）

**响应：**
```json
{
  "code": 1,
  "data": {
    "status": "started",
    "webhook_id": 1,
    "call_id": 10
  }
}
```

| 场景 | 响应中 session_id |
|------|:----------------:|
| 传了有效 `session_id` | ✅ 回显传入的值 |
| 传了无效 `session_id` | ❌ 返回错误 |
| 未传 `session_id`（Agent 类型） | ✅ 返回新建的 session_id |
| 未传 `session_id`（Flow 类型） | ❌ 不返回（Flow 无会话概念） |

`call_id` 可用于后续查询调用记录和消息。

---

## 查询接口（免认证）

所有 `/api/webhook/query/{token}/...` 接口**免 session 认证**，通过 URL 中的 token 鉴权。

### GET /api/webhook/query/{token}/calls

查询该 Webhook 的所有调用记录列表。

**参数：**

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|:----:|------|
| `token` | path | ✅ | — | Webhook token |
| `page` | query | ❌ | 1 | 页码 |
| `page_size` | query | ❌ | 20 | 每页条数（最大 100） |

**响应：**
```json
{
  "code": 1,
  "data": {
    "total": 1,
    "list": [
      {
        "id": 10,
        "webhook_id": 1,
        "flow_id": 1,
        "ref_type": "session",
        "ref_id": 5,
        "input_data": {"message": "查询订单状态"},
        "status": 2,
        "output_data": {"result": "处理完成"},
        "error_message": null,
        "callback_status": "sent",
        "started_at": "2026-06-21T08:30:00",
        "finished_at": "2026-06-21T08:30:05"
      }
    ]
  }
}
```

**status 枚举：** 0=待执行，1=执行中，2=成功，3=失败，4=已取消

**ref_type/ref_id：**
- `session` → `ref_id` 为 `agent_session.id`（Agent 类型）
- `execution` → `ref_id` 为 `flow_execution.id`（Flow 类型）
- 执行中可能为 null（尚未创建会话/执行记录）

### GET /api/webhook/query/{token}/calls/{call_id}

查询单条调用记录详情（同列表响应格式，含完整 input_data/output_data）。

### GET /api/webhook/query/{token}/calls/{call_id}/messages

查询该次调用产生的消息列表。自动按 `ref_type` 分流：
- `session` → 查 `agent_message` 表
- `execution` → 查 `conversation_message` 表

**参数：**

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|:----:|------|
| `token` | path | ✅ | — | Webhook token |
| `call_id` | path | ✅ | — | 调用记录 ID |
| `before_id` | query | ❌ | null | 游标 ID（返回此 ID 之前的消息，用于向上翻页） |
| `limit` | query | ❌ | 20 | 每页条数（最大 100） |

**响应：**
```json
{
  "code": 1,
  "data": {
    "total": 5,
    "list": [
      {
        "id": 100,
        "role": "user",
        "content": "查询订单状态",
        "thinking": null,
        "tool_calls": null,
        "tool_call_id": null,
        "status": null,
        "sequence": 0,
        "created_at": "2026-06-21T08:30:01"
      },
      {
        "id": 101,
        "role": "assistant",
        "content": "订单 ORD-2026-001 当前状态：已发货",
        "thinking": "用户查询订单状态...",
        "tool_calls": [{"name": "query_order", "arguments": {"order_id": "ORD-2026-001"}}],
        "tool_call_id": null,
        "status": null,
        "sequence": 1,
        "created_at": "2026-06-21T08:30:03"
      }
    ]
  }
}
```

---

## 会话与消息管理接口（免认证，仅 Agent 类型）

所有 `/api/webhook/query/{token}/sessions/...` 接口**免 session 认证**，通过 URL 中的 token 鉴权。

**重要：会话隔离** — 这些接口仅返回/操作 **该 Webhook 创建的会话**（`agent_session.webhook_id == webhook.id`）。用户在 UI 聊天产生的会话（`webhook_id` 为空）不可见、不可操作。外部触发时传入 `session_id` 复用的用户会话也不算 Webhook 创建，不会出现在会话列表中。

### GET /api/webhook/query/{token}/sessions

查询该 Webhook 创建的会话列表（分页）。

**参数：**

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|:----:|------|
| `token` | path | ✅ | — | Webhook token |
| `page` | query | ❌ | 1 | 页码 |
| `page_size` | query | ❌ | 20 | 每页条数（最大 100） |

**响应：**
```json
{
  "code": 1,
  "data": {
    "total": 12,
    "items": [
      {
        "id": 5,
        "flow_id": 1,
        "title": "[Webhook] 订单处理",
        "status": 1,
        "created_at": "2026-06-21T08:30:00"
      }
    ]
  }
}
```

**会话字段：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 会话 ID |
| `flow_id` | int | 关联 Agent Flow ID |
| `title` | string | 会话标题（Webhook 创建的默认 `[Webhook] {name}`） |
| `status` | int | 1=活跃，0=已归档 |
| `created_at` | datetime | 创建时间 |

### GET /api/webhook/query/{token}/sessions/{session_id}

查询会话详情（校验该会话由本 Webhook 创建）。响应同上单个会话对象。

校验失败返回 `{code:0, msg:"会话不存在或不属于该Webhook"}`。

### GET /api/webhook/query/{token}/sessions/{session_id}/delete

删除指定会话（校验归属）。同时清理：
- 会话所有消息（软删除）
- LangGraph checkpoint（确保下次执行从 DB 重建历史）

**响应：**
```json
{ "code": 1, "data": null, "msg": "删除成功" }
```

### GET /api/webhook/query/{token}/sessions/{session_id}/messages

查询指定会话的消息列表（校验归属，游标分页）。

**参数：**

| 参数 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|:----:|------|
| `token` | path | ✅ | — | Webhook token |
| `session_id` | path | ✅ | — | 会话 ID |
| `before_id` | query | ❌ | null | 游标 ID（返回此 ID 之前的消息，用于向上翻页） |
| `limit` | query | ❌ | 20 | 每页条数（最大 100） |

**响应格式同** [调用消息列表](#get-apiwebhookquerytokencallscall_idmessages)，返回 `{total, items}`。

### GET /api/webhook/query/{token}/sessions/{session_id}/messages/{message_id}/delete

删除指定会话的 `message_id` **及其后所有消息**（校验归属）。

用途：回滚到某条消息之前的状态。删除后自动清理 LangGraph checkpoint，下次执行将从剩余消息重建历史。

**响应：**
```json
{ "code": 1, "data": null, "msg": "删除成功" }
```

消息不存在时返回 `{code:0, msg:"消息不存在"}`。

---

## WebhookConfig 字段表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键 |
| `flow_id` | int | 关联流程 ID |
| `name` | string | Webhook 名称 |
| `token` | string | 唯一令牌（自动生成，只读） |
| `description` | string | 描述 |
| `input_config` | object | 默认输入参数模板 |
| `callback_url` | string | 回调 URL |
| `is_enabled` | int | 0=禁用，1=启用 |
| `call_count` | int | 调用次数（只读） |
| `last_call_time` | datetime | 最后调用时间（只读） |

## WebhookCallRecord 字段表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键 |
| `webhook_id` | int | 关联 webhook_config.id |
| `flow_id` | int | 关联 flow.id（冗余） |
| `ref_type` | string | 引用类型：`session`(Agent) / `execution`(Flow) |
| `ref_id` | int | 引用 ID：agent_session.id 或 flow_execution.id |
| `input_data` | object | 本次触发输入数据快照 |
| `status` | int | 0=待执行，1=执行中，2=成功，3=失败，4=已取消 |
| `output_data` | object | 输出数据 |
| `error_message` | string | 错误信息 |
| `callback_status` | string | 回调状态：`pending`/`sent`/`failed`/`skipped` |
| `started_at` | datetime | 触发时间 |
| `finished_at` | datetime | 完成时间 |

## 回调通知格式

执行完成后 POST 到 `callback_url`：

```json
{
  "webhook_name": "订单处理",
  "flow_id": 1,
  "call_id": 10,
  "session_id": 5,
  "execution_id": null,
  "status": "success",
  "output_data": {"result": "处理完成"},
  "error": null,
  "timestamp": "2026-06-21T08:30:00"
}
```

| 字段 | 类型 | 说明 |
|------|------|------|
| `webhook_name` | string | Webhook 名称 |
| `flow_id` | int | 流程 ID |
| `call_id` | int | 调用记录 ID（可用于后续查询） |
| `session_id` | int | Agent 类型时非空 |
| `execution_id` | int | Flow 类型时非空 |
| `status` | string | `success` / `failed` / `cancelled` |
| `output_data` | object | 流程输出 |
| `error` | string | 错误信息（失败时） |
| `timestamp` | string | ISO 格式时间戳 |
