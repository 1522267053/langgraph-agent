# WebSocket 网关（ws-gateway）实现流程

> 本文档描述平台 WebSocket 网关的完整架构、数据流和关键实现细节。
> 源码分布：`app/models/ws_gateway*.py`、`app/services/ws_*.py`、`app/api/ws_*.py`、`app/agent_flow/ws_tool_context.py`、`app/agent_flow/remote_tool_builder.py`、`frontend/src/{api,types}/wsGateway.ts`。

---

## 一、整体架构

平台包含 **两套相互独立的 WebSocket 通道**，请勿混淆：

| 通道 | 端点 | 认证 | 用途 | 方向 |
|------|------|------|------|------|
| **通知通道** | `/ws/notifications` | session cookie（与 HTTP 共用 `auth_session`） | 后台任务向前端推送执行完成/日程提醒 | 服务端 → 前端 |
| **触发通道** | `/ws/trigger/{token}` | URL 路径中的 `token` | 外部系统连接并驱动 Agent/Flow 执行 | 双向（外部客户端 ↔ 平台） |

> 通常说的「ws-gateway」指 **触发通道**，即让外部程序通过 WebSocket 触发平台上的智能体或流程。通知通道是平台的辅助推送管道。

### 注册机制

WebSocket 路由 **不走** `app/api/` 的自动扫描注册（因为会继承 FastAPI 全局 `HTTPBearer` 依赖，而 `HTTPBearer.__call__` 需要HTTP 请求对象，不兼容 WebSocket）。改为在 `app/config/app_setup.py:67` 显式调用：

```python
register_websocket_routes(app)
```

`register_websocket_routes`（`app/api/ws_api.py:71`）一次性注册两条路由：

1. `/ws/notifications` → `notification_ws`
2. 调用 `register_trigger_ws_routes(app)` 注册 `/ws/trigger/{token}` → `trigger_ws`

两者都通过 `starlette.routing.WebSocketRoute` 插入到路由表最前（`routes.insert(0, ...)`），绕过全局 HTTP 依赖。

---

## 二、数据模型

### 2.1 `WsGatewayConfig`（`app/models/ws_gateway.py`）

网关配置主表，每行代表一个对外暴露的触发入口。

| 字段 | 类型 | 说明 |
|------|------|------|
| `flow_id` | Integer | 关联的流程 ID（agent 或 flow 类型） |
| `name` | String(255) | 网关名称 |
| `token` | String(64) | 唯一令牌，**创建时由 `uuid4().hex` 自动生成**，作为 URL 认证凭证 |
| `description` | Text | 描述 |
| `input_config` | JSON | 默认输入参数模板（每次 execute 时作为基础合并） |
| `callback_url` | String(500) | 预留的执行完成回调 URL |
| `is_enabled` | SmallInteger | 0=禁用 / 1=启用（禁用的网关连接时返回 4403） |
| `call_count` | Integer | 累计调用次数（触发时自增） |
| `last_call_time` | DateTime | 最后调用时间 |

### 2.2 `WsGatewayCallRecord`（`app/models/ws_gateway_call_record.py`）

每次外部触发都会写入一条调用记录，用于审计与历史回查。

| 字段 | 说明 |
|------|------|
| `gateway_id` | 关联 `ws_gateway_config.id` |
| `flow_id` | 冗余存储，便于直接按流程查询 |
| `ref_type` / `ref_id` | 引用类型与 ID：`session`（Agent 会话）或 `execution`（Flow 执行记录） |
| `input_data` | 本次触发的输入数据快照 |
| `status` | 0=待执行, 1=执行中, 2=成功, 3=失败, 4=已取消（复用 `ExecutionStatus` 枚举） |
| `output_data` / `error_message` | 执行结果或错误信息 |
| `callback_status` | 回调状态：`pending`/`sent`/`failed`/`skipped` |
| `started_at` / `finished_at` | 起止时间 |

> 两个模型均遵循项目约定：禁止数据库外键、`is_delete` 软删除、`create_time` 默认值用函数引用。

---

## 三、配置管理（HTTP CRUD）

管理类操作走标准 HTTP API，前端页面使用。

- **API 文件**：`app/api/ws_gateway_api.py`（继承 `BaseApi`，前缀 `/api/ws-gateway`）
- **Service**：`app/services/ws_gateway_service.py`
- **Schema**：`app/schemas/ws_gateway_schema.py`

提供的接口：

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/ws-gateway/page` | 分页查询（支持按 name/flow_id/is_enabled 过滤） |
| POST | `/api/ws-gateway/create` | 创建网关（**自动生成 token**） |
| POST | `/api/ws-gateway/update` | 更新（注意 `exclude_unset=True` 无法置空字段） |
| GET | `/api/ws-gateway/delete/{id}` | 软删除 |
| GET | `/api/ws-gateway/get/{id}/url` | 获取触发地址：`/ws/trigger/{token}` |

前端封装：`frontend/src/api/wsGateway.ts`，类型定义：`frontend/src/types/wsGateway.ts`。

---

## 四、触发通道核心流程

### 4.1 连接建立与鉴权

`trigger_ws`（`app/api/ws_trigger_api.py:48`）：

```
客户端发起 ws://host/ws/trigger/{token}
        │
        ▼
┌────────────────────────────────────┐
│ 1. 从 path_params 取 token          │
│ 2. ws_gateway_service.get_by_token  │
│ 3. 不存在 → close(4404)             │
│ 4. is_enabled=0 → close(4403)       │
│ 5. 查关联 flow，读取 flow_type       │
│ 6. 构造 WSConnection 运行时对象      │
└────────────────────────────────────┘
        │
        ▼
   websocket.accept()
   发送 {"type":"connected", data:{gateway_id, gateway_name, flow_id, flow_type}}
        │
        ▼
   进入 _message_receiver 消息循环
```

`WSConnection`（`ws_trigger_api.py:29`）是一个 dataclass，保存单个连接的全部运行时状态：token、gateway_id、flow_id、flow_type、`registered_tools`（客户端注册的远程工具）、`pending_calls`（工具调用 Future 映射）、`current_session_id`、`executing`（执行中标志，串行化执行）等。

### 4.2 消息分发循环

`_message_receiver`（`ws_trigger_api.py:112`）持续 `receive_text()`，按 `action` 字段分发：

| action | 处理函数 | 说明 | 是否阻塞循环 |
|--------|----------|------|------------|
| `ping` | — | 直接回 `pong` | 同步 |
| `register_tools` | `_handle_register_tools` | 注册远程工具（**仅 Agent 类型生效**） | 同步 |
| `unregister_tools` | — | 清空 `registered_tools` | 同步 |
| `tool_result` | `_resolve_tool_call` | 把客户端返回结果 resolve 到对应 Future | 同步 |
| `execute` | `_handle_execute` | **派发为独立 asyncio task**，不阻塞接收 | 异步 |
| `create_session` | `_handle_create_session` | 新建 Agent 会话 | 同步 |
| `switch_session` | `_handle_switch_session` | 切换当前会话 | 同步 |
| `list_sessions` | `_handle_list_sessions` | 查询会话列表 | 同步 |
| `delete_session` | `_handle_delete_session` | 删除会话（连带消息与 checkpoint） | 同步 |
| `get_messages` | `_handle_get_messages` | 查询历史消息（游标分页） | 同步 |
| `delete_message` | `_handle_delete_message` | 删除指定消息及之后的所有消息 | 同步 |

> 设计要点：`execute` 用 `asyncio.create_task` 派发，保证执行期间仍能接收 `tool_result`、`ping` 等指令；同时 `conn.executing` 标志保证同一连接**串行执行**（并发 execute 会返回 "正在执行中" 错误）。

### 4.3 执行流程（`stream_execute`）

核心在 `WsGatewayService.stream_execute`（`ws_gateway_service.py:391`），它是一个 async generator，yield 所有事件由 WS 端点转发给客户端。

```
stream_execute(gateway_id, input_data, session_id)
        │
        ▼
┌─────────────────────────────────────────────────┐
│ 1. 查 gateway + flow，解析 flow_type             │
│ 2. Agent 类型：                                  │
│    - 传入 session_id → 校验存在且属该 flow       │
│    - 未传 → agent_executor_service.create_session│
│      （带 gateway_id 标记来源，标题 [WS] xxx）    │
│ 3. create_call_record（status=RUNNING）          │
│ 4. update_call_record_ref("session", sid)        │
└─────────────────────────────────────────────────┘
        │
        ▼ yield {"type":"call_started", data:{call_id, session_id}}
        │
        ▼
┌─────────────────────────────────────────────────┐
│ 遍历执行事件流（按 flow_type 分流）：              │
│  • Agent → agent_executor_service.chat_stream    │
│  • Flow  → flow_executor_service.execute_stream  │
│                                                  │
│ 对每个 event：                                    │
│  - flow_start → 捕获 execution_id，回填 record   │
│  - flow_done  → 记录 status + output_data        │
│  - error      → 记录 status=failed + message     │
│  - 其余事件原样 yield                             │
└─────────────────────────────────────────────────┘
        │
        ▼
   finish_call_record(record_id, status, output_data, error_message)
   （自建 AsyncSessionLocal 会话写入，独立于执行流）
```

**关键点**：

- Agent 类型触发时，`input_data["message"]` 作为用户消息，其余键作为 `params` 透传。
- Flow 类型从 `flow_start` 事件中拿到 `execution_id`，回填到调用记录的 `ref_id`。
- `finish_call_record` 使用独立数据库会话（`AsyncSessionLocal()`），避免与执行流的会话生命周期耦合——这与项目"SSE 流式方法自建会话"的约定一致。

### 4.4 客户端接收的事件类型

执行期间客户端会收到（原样转发执行引擎的事件）：

| type | 说明 |
|------|------|
| `connected` | 握手成功 |
| `call_started` | 本次调用启动，含 `call_id` 和 `session_id` |
| `node_content` | 流式文本 token |
| `tool_call` / `tool_start` / `tool_end` | 工具调用事件 |
| `flow_start` / `flow_done` | 流程起止（Flow 类型） |
| `tool_invoke` | **远程工具被 Agent 调用**，需客户端回 `tool_result` |
| `error` | 错误 |

---

## 五、远程工具机制（核心特色）

外部客户端可以把**自己本地的函数**注册为 Agent 可调用的工具。Agent 执行中需要该工具时，会通过 WebSocket **反向调用**客户端，等客户端返回结果后继续推理。

### 5.1 流程

```
客户端                         平台(WS trigger)               Agent 执行引擎
  │                                │                              │
  │── register_tools(定义列表) ───►│                              │
  │◄── tools_registered ───────────│                              │
  │                                │                              │
  │── execute(message) ───────────►│  _current_ws_conn.set(conn)  │
  │                                │── stream_execute ───────────►│
  │                                │                              │
  │                                │   收集工具时从 contextvars    │
  │                                │   读取 ws_conn.registered_   │
  │                                │   tools，转成 StructuredTool │
  │                                │                              │
  │◄── tool_invoke(name,args, ─────│   ← remote_coro 发起调用     │
  │       call_id)                 │   并创建 Future 挂起等待      │
  │                                │                              │
  │── tool_result(call_id, ───────►│   _resolve_tool_call 找到    │
  │       result)                  │   Future.set_result          │
  │                                │   → 工具执行返回，Agent 继续  │
  │                                │                              │
  │◄── node_content / flow_done ───│                              │
```

### 5.2 关键组件

1. **上下文传递** — `app/agent_flow/ws_tool_context.py`
   - `_current_ws_conn` 是 `contextvars.ContextVar`，保存当前执行对应的 WS 连接。
   - `_handle_execute` 开始时 `_current_ws_conn.set(conn)`，结束时 `set(None)`。
   - 利用 asyncio 的 contextvars 自动传播特性，**LangGraph 并行节点也能正确读取**到同一个连接。

2. **工具收集** — `app/agent_flow/node_handlers/llm_tool_executor.py:160`
   - 在组装 LLM 可用工具时，检查 `_current_ws_conn.get()`；
   - 若存在且 `registered_tools` 非空，对每个定义调用 `create_remote_tool(tool_def, ws_conn)` 转为 `StructuredTool`；
   - 最后与其它工具一起按名称去重。

3. **工具构造** — `app/agent_flow/remote_tool_builder.py`
   - `create_remote_tool` 把客户端的 JSON Schema（`{name, description, parameters}`）转为 Pydantic `args_schema`；
   - 工具的协程 `remote_coro`：
     - 生成 `call_id = uuid4().hex`，创建 `asyncio.Future` 存入 `conn.pending_calls[call_id]`；
     - 通过 WS 发送 `{"type":"tool_invoke", data:{call_id, name, args}}`；
     - `await asyncio.wait_for(future, timeout)`，默认 120s 超时；
     - 超时/异常时清理 `pending_calls` 并返回 JSON 错误串。

4. **结果回填** — `_resolve_tool_call`（`ws_trigger_api.py:166`）
   - 客户端回 `{"action":"tool_result", call_id, result}` 时，从 `pending_calls` 弹出 Future 并 `set_result`；
   - 这会让挂起的 `remote_coro` 立即恢复，工具调用完成。

> 这套机制让平台 Agent 可以调用**客户端本地的资源**（环境变量、本地文件、本地 API、IoT 设备等），而无需把这些能力搬到服务端。

---

## 六、会话管理（仅 Agent 类型）

外部触发 Agent 类型网关时，支持完整的多会话生命周期管理。会话通过 `AgentSession.gateway_id` 字段标记来源，只有该网关创建的会话才可被该网关操作。

| 操作 | action | 校验 |
|------|--------|------|
| 新建会话 | `create_session` | 创建时自动写入 `gateway_id`，标题默认 `[WS] {网关名}` |
| 切换会话 | `switch_session` | 校验会话存在且 `gateway_id` 匹配 |
| 查询列表 | `list_sessions` | 按 `gateway_id` 过滤分页 |
| 删除会话 | `delete_session` | 委托 `agent_executor_service.delete_session`，连带清理消息和 LangGraph checkpoint |
| 查询消息 | `get_messages` | 游标分页（`before_id` + `limit`） |
| 删除消息 | `delete_message` | 删除 `message_id` 及其后所有消息，并同步清理 checkpoint |

> 删除消息/会话时必须同步清理 checkpoint（`_cleanup_thread_checkpoint`），否则旧消息会从 checkpoint 回填——这是项目对话历史双写机制的关键约定。

### 跨连接恢复

会话 ID 持久化在数据库，客户端断开后可凭 `session_id` 在新连接中恢复上下文：
1. 第一次连接：`create_session` 或直接 `execute`（自动建会话）→ 记下 `session_id`；
2. 后续连接：`execute` 时带上 `session_id`，Agent 自动加载历史消息与 checkpoint。

---

## 七、调用记录查询

调用记录（`WsGatewayCallRecord`）支持外部免认证回查，便于外部系统对账与追踪。

> Schema 中定义了对应的查询接口（`WsGatewayCallRecordPageRequest`、`WsGatewaySessionPageRequest`、`WsGatewayMessagePageRequest` 等），均通过 `token` 鉴权（与触发通道一致的 token 机制）。

按 `ref_type` 自动分流查询消息：
- `ref_type == "session"` → 查 `AgentMessage`
- `ref_type == "execution"` → 查 `ConversationMessage`

---

## 八、通知通道（辅助）

虽然不属于触发网关，但同属 WebSocket 体系，简要说明。

- **端点**：`/ws/notifications`（`app/api/ws_api.py`）
- **认证**：复用 HTTP 的 `auth_session` cookie；未配置密码时免认证。
- **管理器**：`ws_manager`（`app/services/ws_manager.py`，全局单例）
  - `_user_connections: dict[username, set[WebSocket]]` — 按用户分组，支持定向推送
  - `_ws_user: dict[WebSocket, username]` — 反向映射，O(1) 断开
  - `broadcast()` / `broadcast_to_user()` — 发送时自动清理失效连接
  - 受 `_notification_enabled` 开关控制（系统配置更新时调用 `set_notification_enabled`）

提供的高级方法：
- `notify_execution_done(...)` — 执行完成广播（受开关控制）
- `notify_agenda_reminder(...)` — 日程提醒定向推送（不受开关限制）

前端：`frontend/src/composables/useBrowserNotification.ts` 封装浏览器 Notification API，收到 WS 消息后弹出桌面通知。

---

## 九、文件索引

### 后端

| 文件 | 职责 |
|------|------|
| `app/models/ws_gateway.py` | 网关配置模型 |
| `app/models/ws_gateway_call_record.py` | 调用记录模型 |
| `app/schemas/ws_gateway_schema.py` | 配置与查询 Schema |
| `app/services/ws_gateway_service.py` | 业务服务（CRUD + stream_execute + 会话管理） |
| `app/services/ws_manager.py` | 通知通道连接管理器（单例） |
| `app/api/ws_gateway_api.py` | 管理 CRUD API（`/api/ws-gateway/*`） |
| `app/api/ws_trigger_api.py` | 触发端点 + 消息分发 + 会话/消息指令 |
| `app/api/ws_api.py` | 通知端点 + WS 路由统一注册入口 |
| `app/agent_flow/ws_tool_context.py` | contextvars 传递 WS 连接 |
| `app/agent_flow/remote_tool_builder.py` | 远程工具 → StructuredTool 转换 |
| `app/agent_flow/node_handlers/llm_tool_executor.py` | 工具收集时注入远程工具（L160） |
| `app/config/app_setup.py` | 应用创建时注册 WS 路由（L67） |

### 前端

| 文件 | 职责 |
|------|------|
| `frontend/src/api/wsGateway.ts` | 网关配置 CRUD 请求封装 |
| `frontend/src/types/wsGateway.ts` | TypeScript 类型定义 |
| `frontend/src/composables/useBrowserNotification.ts` | 浏览器桌面通知（配合通知通道） |

### 示例

| 文件 | 说明 |
|------|------|
| `skills/ws-gateway-manager/references/ws_client_example.py` | 5 个完整 Python 客户端示例（最简执行/远程工具/会话管理/封装客户端类/跨连接恢复） |

---

## 十、典型场景速查

### 场景 1：外部程序触发流程并接收结果

```python
import asyncio, json, websockets

async def main():
    async with websockets.connect("ws://127.0.0.1:8000/ws/trigger/{token}") as ws:
        await ws.recv()  # connected
        await ws.send(json.dumps({"action": "execute", "message": "你好"}))
        async for raw in ws:
            e = json.loads(raw)
            if e["type"] == "node_content":
                print(e["data"]["content"], end="", flush=True)
            elif e["type"] in ("flow_done", "error"):
                break

asyncio.run(main())
```

### 场景 2：注册本地工具供 Agent 调用

```python
await ws.send(json.dumps({
    "action": "register_tools",
    "tools": [{
        "name": "get_local_time",
        "description": "获取客户端本地时间",
        "parameters": {"type": "object", "properties": {}}
    }]
}))

# 执行中收到 tool_invoke 时回传结果
if event["type"] == "tool_invoke":
    d = event["data"]
    result = get_local_time()  # 本地执行
    await ws.send(json.dumps({
        "action": "tool_result",
        "call_id": d["call_id"],
        "result": str(result)
    }))
```

### 场景 3：多会话对话

```python
# 新建
await ws.send(json.dumps({"action": "create_session", "title": "技术讨论"}))
session_id = (json.loads(await ws.recv()))["data"]["session_id"]

# 在该会话内对话
await ws.send(json.dumps({
    "action": "execute",
    "message": "记住我叫张三",
    "session_id": session_id
}))

# 下次连接恢复
await ws.send(json.dumps({
    "action": "execute",
    "message": "我叫什么？",
    "session_id": session_id
}))
```
