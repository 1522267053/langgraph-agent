---
name: webhook-manager
description: |
  创建、管理、触发和查询 Webhook。适用场景：
  (1) 用户要求创建或管理 Webhook（将流程/智能体暴露为 HTTP 接口）
  (2) 用户想查看 Webhook 的触发记录和产生的会话消息
  (3) 用户需要外部系统通过 HTTP POST 触发流程执行
  (4) 用户想获取 Webhook 的触发 URL 或 token

  触发词：「创建 webhook」「管理 webhook」「触发 webhook」「外部触发」「查询 webhook 记录」「webhook 回调」
---

# Webhook Manager

服务器：`http://127.0.0.1:8000`

## 核心规则（必须遵守）

1. **token 自动生成不可指定**：创建时后端 `uuid.uuid4().hex` 自动生成 64 位 token，请求中传 `token` 字段会被忽略
2. **trigger 接口免认证**：`POST /api/webhook/trigger/{token}` 无需 session cookie，通过 URL 中的 token 鉴权
3. **query 接口免认证**：`GET /api/webhook/query/{token}/...` 同样通过 token 鉴权，外部系统可直接查询调用记录和消息
4. **其余接口需登录态**：CRUD 接口（`/api/webhook/page/create/update/delete/get`）需要 session cookie
5. **异步执行**：触发后立即返回 `{"status":"started","webhook_id":...,"call_id":...}`，后台异步执行，不等待结果
6. **`is_enabled=0` 时触发被拒**：返回 `"Webhook 已禁用"`
7. **更新使用 `exclude_unset`**：未传字段保持不变，无法将字段更新为 `None`

## API 速查

| 方法 | 路径 | 认证 | 用途 |
|------|------|:--:|------|
| POST | `/api/webhook/page` | ✅ | 分页列表 |
| POST | `/api/webhook/create` | ✅ | 创建（自动生成 token） |
| POST | `/api/webhook/update` | ✅ | 更新 |
| GET | `/api/webhook/delete/{id}` | ✅ | 软删除 |
| GET | `/api/webhook/get/{id}/url` | ✅ | 获取触发 URL |
| POST | `/api/webhook/trigger/{token}` | ❌ | **外部触发** |
| GET | `/api/webhook/query/{token}/calls` | ❌ | 调用记录列表 |
| GET | `/api/webhook/query/{token}/calls/{call_id}` | ❌ | 调用记录详情 |
| GET | `/api/webhook/query/{token}/calls/{call_id}/messages` | ❌ | 调用产生的消息列表 |

## 创建 Webhook 流程

```
1. POST /api/webhook/create     # 创建（关联 flow_id）
2. GET /api/webhook/get/{id}/url  # 获取触发 URL
3. 外部系统 POST 触发 URL        # 执行流程
4. GET /api/webhook/query/{token}/calls  # 查询调用记录
```

### 创建示例

```json
POST /api/webhook/create
{
  "name": "订单处理",
  "flow_id": 1,
  "description": "处理外部订单系统回调",
  "input_config": {
    "message": "请处理新订单"
  },
  "callback_url": "https://external.com/callback",
  "is_enabled": 1
}
```

响应中 `token` 字段即为外部触发凭据。

### 获取触发 URL

```
GET /api/webhook/get/{id}/url
```

响应：
```json
{
  "code": 1,
  "data": {
    "url": "/api/webhook/trigger/abc123...",
    "token": "abc123..."
  }
}
```

前端拼接 `host` 后得到完整 URL：`http://host/api/webhook/trigger/abc123...`

## 触发机制

### 输入合并

```
input_data = {**(webhook.input_config or {}), **request_body}
```

优先级：请求 body > `input_config` 默认模板

### 异步执行

1. 创建 `WebhookCallRecord`（status=1 执行中）
2. 后台 fire-and-forget 执行
3. 按 `flow_type` 分流：
   - **Agent** → 创建临时会话（标题 `[Webhook] {webhook_name}`），`input_data.message` 作为用户消息，其余字段作为 `params`
   - **Flow** → `flow_executor_service.execute_stream`，从 `flow_start` 事件捕获 `execution_id`
4. 完成后更新 record 的 `status/output_data/finished_at`

### 回调通知

配置了 `callback_url` 时，执行完成后 POST 回调：

```json
POST {callback_url}
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

- Agent 类型：`session_id` 非空，`execution_id` 为 null
- Flow 类型：`execution_id` 非空，`session_id` 为 null
- 回调失败不影响流程执行，仅记录 `callback_status=failed`

## 外部触发示例（curl）

### 基础触发

```bash
curl -X POST "http://host/api/webhook/trigger/abc123..."
```

### 带输入参数触发

```bash
curl -X POST "http://host/api/webhook/trigger/abc123..." \
  -H "Content-Type: application/json" \
  -d '{"message": "查询订单状态", "order_id": "ORD-2026-001"}'
```

### 查询调用记录列表

```bash
curl "http://host/api/webhook/query/abc123.../calls?page=1&page_size=10"
```

### 查询单条调用记录详情

```bash
curl "http://host/api/webhook/query/abc123.../calls/10"
```

### 查询调用产生的消息

```bash
curl "http://host/api/webhook/query/abc123.../calls/10/messages?limit=20"
```

## 完整接口详情

见 [references/api.md](references/api.md)。
