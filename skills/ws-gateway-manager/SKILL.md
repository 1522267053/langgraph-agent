---
name: ws-gateway-manager
description: |
  创建、管理网关，通过 WebSocket 触发流程/智能体执行。
  适用场景：
  (1) 用户要求创建或管理网关
  (2) 外部系统通过 WebSocket 连接触发流程执行，实时接收流式结果
  (3) 注册远程工具，让 Agent 反向调用客户端函数
  (4) 管理会话（创建/切换/列表/删除）

  触发词：「创建网关」「管理网关」「websocket 触发」「远程工具」「网关会话」「注册工具」
---

# WebSocket 网关管理

服务器：`http://127.0.0.1:8000`

## 核心规则

1. **WebSocket 触发**：外部客户端通过 `ws://host/ws/trigger/{token}` 连接，以 JSON 指令驱动执行
2. **token 自动生成**：创建网关 时后端 `uuid.uuid4().hex` 生成 token
3. **实时流式返回**：执行结果通过 WebSocket 逐 token 流式推送（node_content/tool_call/flow_done 等），无需轮询
4. **Agent 专属功能**：远程工具注册、会话管理（创建/切换/列表/删除/消息查询）仅 Agent 类型支持。Flow 类型调用会返回 `"仅 Agent 类型支持"` 错误
5. **并发限制**：同一连接同时只允许一个 execute 执行
6. **CRUD 需登录态**：管理接口（`/api/gateway/page/create/update/delete`）需要 session cookie
7. **输入合并**：`input_data = {**gateway.input_config, **客户端参数}`（排除 `action` 和 `session_id`），客户端参数覆盖默认模板
8. **工具名**：远程工具直接使用客户端注册的原始名称，超时 120 秒

## 管理接口（HTTP）

| 方法 | 路径 | 认证 | 用途 |
|------|------|:--:|------|
| POST | `/api/gateway/page` | ✅ | 分页列表 |
| POST | `/api/gateway/create` | ✅ | 创建（自动生成 token） |
| POST | `/api/gateway/update` | ✅ | 更新 |
| GET | `/api/gateway/delete/{id}` | ✅ | 软删除 |
| GET | `/api/gateway/get/{id}/url` | ✅ | 获取 WebSocket 地址 |

## 创建网关

```json
POST /api/gateway/create
{
  "name": "订单处理",
  "flow_id": 1,
  "description": "处理外部订单",
  "input_config": {"message": "请处理新订单"},
  "is_enabled": 1
}
```

## WebSocket 触发

### 连接

```
ws://host/ws/trigger/{token}
```

连接成功后收到：
```json
{"type": "connected", "data": {"gateway_id": 1, "gateway_name": "订单处理", "flow_id": 1, "flow_type": "agent"}}
```

> **建议**：连接后检查 `data.flow_type`。`"agent"` 才支持远程工具和会话管理，`"flow"` 仅支持 `execute`。非 Agent 类型调用 register_tools 或会话操作会收到 `{"type":"error","data":{"message":"仅 Agent 类型支持..."}}`。

### 客户端指令一览

| 指令 | 用途 | 适用类型 |
|------|------|:------:|
| `execute` | 发送消息/数据触发执行 | 全部 |
| `register_tools` | 注册远程工具 | 仅 Agent |
| `unregister_tools` | 注销所有远程工具 | 仅 Agent |
| `tool_result` | 返回工具执行结果（回应 tool_invoke） | 仅 Agent |
| `create_session` | 创建新会话 | 仅 Agent |
| `switch_session` | 切换当前会话 | 仅 Agent |
| `list_sessions` | 查询会话列表 | 仅 Agent |
| `delete_session` | 删除会话（含消息 + checkpoint） | 仅 Agent |
| `get_messages` | 查询会话历史消息（游标分页） | 仅 Agent |
| `delete_message` | 删除指定消息及其后所有消息 | 仅 Agent |
| `ping`（纯文本） | 心跳，服务端回 `pong` | 全部 |

### 执行指令

```json
{"action": "execute", "message": "你好"}
```

指定已有会话（多轮对话）：
```json
{"action": "execute", "message": "继续", "session_id": 5}
```

Flow 类型（无 message，用 input_data）：
```json
{"action": "execute", "city": "北京"}
```

执行后服务端实时推送事件：
```json
{"type": "call_started", "data": {"call_id": 10, "session_id": 5}}
{"type": "flow_start", "data": {"flow_id": 1, "execution_id": 5}}
{"type": "node_content", "data": {"content": "你好"}}
{"type": "node_content", "data": {"content": "！"}}
{"type": "flow_done", "data": {"status": "success", "output_data": {"content": "你好！"}}}
```

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

工具超时默认 120 秒。工具名直接使用客户端注册的原始名称。

## Python 客户端示例

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

见 [references/ws_client_example.py](references/ws_client_example.py)，包含 5 个完整示例：

| 编号 | 名称 | 演示内容 |
|------|------|---------|
| 1 | 最简执行 | 连接 → 发消息 → 逐 token 接收流式回复 |
| 2 | 远程工具 | 注册 `get_local_time`/`calculate` 函数，Agent 调用后回传结果 |
| 3 | 会话管理 | 创建多会话、多轮对话、切换、列表 |
| 4 | 封装客户端类 | 后台 task 自动处理 `tool_invoke`，适合集成到实际项目 |
| 5 | 指定 session_id 继续 | 用已知 session_id 跨连接恢复上下文（先创建，后恢复） |

运行方式：

```bash
pip install websockets

# 交互式选择示例
WS_TOKEN=你的token python references/ws_client_example.py

# 直接运行指定示例
WS_TOKEN=你的token python references/ws_client_example.py 2     # 远程工具

# 示例 5：先创建会话
WS_TOKEN=你的token python references/ws_client_example.py 5
# 示例 5：用返回的 session_id 恢复
WS_TOKEN=你的token python references/ws_client_example.py 5 123
```
