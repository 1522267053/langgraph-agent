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
3. **实时流式返回**：执行结果通过 WebSocket 逐 token 流式推送（node_content/flow_done/token_usage 等），无需轮询。**完整事件列表见下方「执行指令」章节**
4. **Agent 专属功能**：远程工具注册、会话管理（创建/切换/列表/删除/消息查询）仅 Agent 类型支持。Flow 类型调用会返回 `"仅 Agent 类型支持"` 错误
5. **并发限制**：同一连接同时只允许一个 execute 执行
6. **CRUD 需登录态**：管理接口（`/api/ws-gateway/page/create/update/delete`）需要 session cookie
7. **输入合并**：`input_data = {**gateway.input_config, **客户端参数}`（排除 `action` 和 `session_id`），客户端参数覆盖默认模板
8. **工具名**：远程工具直接使用客户端注册的原始名称，超时 120 秒
9. **文件传输 token 鉴权**：上传（`POST /api/ws-gateway/upload`）/下载（`GET /api/ws-gateway/download/{file_id}`）由网关 token 自鉴权，**免登录**（已豁免认证白名单）。上传返回 `file_id`，可塞进 `execute` 的 `files` 字段；下载严格校验文件归属该网关关联的 flow

## 管理接口（HTTP）

| 方法 | 路径 | 认证 | 用途 |
|------|------|:--:|------|
| POST | `/api/ws-gateway/page` | ✅ | 分页列表 |
| POST | `/api/ws-gateway/create` | ✅ | 创建（自动生成 token） |
| POST | `/api/ws-gateway/update` | ✅ | 更新 |
| GET | `/api/ws-gateway/delete/{id}` | ✅ | 软删除 |
| GET | `/api/ws-gateway/get/{id}/url` | ✅ | 获取 WebSocket 地址 |

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
> - 非 Agent 类型调用 register_tools 或会话操作会收到 `{"type":"error","data":{"message":"仅 Agent 类型支持..."}}`。
> - `upload_url` 和 `download_url_template` 已在 connected 事件中下发，无需再次查询，直接使用。
> - **建议客户端实现心跳**：定时发送 `ping` 纯文本消息（不发送 JSON 包装），服务端回 `pong`，避免长时间无数据时连接被中间设备断开。
>
> `data.upload_url` / `download_url_template` 为文件传输端点模板（token 自鉴权，免登录），用法见下文「文件传输」。

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

Flow 类型（无 message，用 input_data 字段传递）：

```json
{"action": "execute", "city": "北京"}
```

#### 传入 input_schema 字段

Agent 类型 flow 通常有自定义 `input_schema`（如 `bot_name`、`chat_type` 等），需要通过 **`params`** 字段传递。`params` 的 keys 必须与 `input_schema.fields[].name` 一致：

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
> | `waiting_human` | Human 节点暂停等待用户输入（Agent 类型需 resume） | ❌ |
> | `error` | 流程出错 | ❌ |
> | `tool_invoke` | Agent 调用 ws-gateway 远程工具（仅 Agent 类型，需通过 `tool_result` 返回结果） | ❌ |

> ⚠️ **注意**：旧版示例中的 `call_started` 事件在当前实现中**不存在**，请勿依赖。

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

见 [references/ws_client_example.py](references/ws_client_example.py)，包含 7 个完整示例：

| 编号 | 名称 | 演示内容 |
|------|------|---------|
| 1 | 最简执行 | 连接 → 发消息 → 逐 token 接收流式回复 |
| 2 | 远程工具 | 注册 `get_local_time`/`calculate` 函数，Agent 调用后回传结果 |
| 3 | 会话管理 | 创建多会话、多轮对话、切换、列表 |
| 4 | 封装客户端类 | 后台 task 自动处理 `tool_invoke`，适合集成到实际项目 |
| 5 | 指定 session_id 继续 | 用已知 session_id 跨连接恢复上下文（先创建，后恢复） |
| 6 | 文件传输 | 上传本地图片 → 带 `file_id` 执行 → 下载 Agent 产物（依赖 httpx） |
| 7 | 文件工具 | 注册文件处理工具，前端聊天触发时双向传输文件（依赖 httpx） |

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

# 示例 6/7 需额外安装 httpx，并准备本地文件
pip install httpx
WS_TOKEN=你的token python references/ws_client_example.py 6     # 文件传输
WS_TOKEN=你的token python references/ws_client_example.py 7     # 文件工具（前端触发）
```
