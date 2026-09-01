# Agent Manager API 参考

基础地址：`http://localhost:8000/api`。除 SSE 外，响应统一为：

```json
{"code": 1, "msg": "success", "data": {}}
```

业务错误通常也返回 HTTP 200，始终检查 `code` 和 `msg`。以下路径均省略 `/api` 前缀。

## 环境与发现

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | `/config/check` | 检查初始化和模型配置状态 |
| GET | `/config/providers` | 获取可用模型提供商 |
| GET | `/ai/flow/list?flow_type=agent&keyword=xxx` | 查询流程或 Agent |
| GET | `/ai/flow/node-types` | 获取节点类型及 Agent 可用性 |
| GET | `/ai/flow/node-types/{node_type}/config-schema` | 获取单类节点实时配置 Schema |
| GET | `/ai/flow/config-schemas` | 一次获取全部节点配置 Schema |

创建或修改节点时，以实时 Schema 为准；参考文档只解释用法，不替代 Schema。

## 流程生命周期

### 创建

`POST /ai/flow/create`

```json
{
  "name": "日报整理",
  "description": "汇总输入并生成日报",
  "flow_type": "flow",
  "input_schema": {
    "fields": [
      {"name": "content", "type": "string", "required": true}
    ]
  },
  "output_schema": {
    "fields": [
      {"name": "summary", "type": "string", "required": true}
    ]
  }
}
```

`flow_type` 只能是 `flow` 或 `agent`。`input_schema`、`output_schema` 都是含 `fields` 数组的对象，不是数组本身。

### 读取与元数据更新

- `GET /ai/flow/{flow_id}/detail`：返回流程、节点、边和 Mermaid 图。
- `POST /flow/update`：更新名称、描述、状态、输入输出 Schema 等；请求中必须带 `id`。
- `POST /ai/flow/delete/{flow_id}`：级联软删除流程及关联数据。

修改已有流程前必须先读取详情。删除只在用户明确要求时执行。

## 节点操作

### 批量创建

`POST /ai/flow/{flow_id}/nodes/batch`

```json
{
  "nodes": [
    {
      "node_type": "python",
      "node_key": "normalize_text",
      "node_name": "清洗文本",
      "position_x": 320,
      "position_y": 120,
      "base_config": {
        "code": "def main(text):\n    return {'result': text.strip()}",
        "input_variables": [{"name": "text", "source": "input.content"}],
        "output_variables": [{"name": "result", "type": "object"}]
      }
    }
  ]
}
```

| 字段 | 必需 | 说明 |
|---|---|---|
| `node_type` | 是 | 必须是 `/node-types` 返回的类型 |
| `node_key` | 否 | 省略时自动生成；冲突时自动追加序号 |
| `node_name` | 否 | UI 显示名 |
| `position_x/y` | 否 | 默认 0，仅影响 UI |
| `base_config` | 否 | JSON 对象，缺失字段由节点默认值补全 |
| `ref_flow_id` | 卡片需要 | `card` 引用的子流程 ID |

响应 `data.created_nodes` 返回最终 `node_key`，创建边时使用这个值。

### 批量配置

`POST /ai/flow/{flow_id}/nodes/batch/config`

```json
{
  "nodes": [
    {
      "node_key": "assistant",
      "node_name": "主助手",
      "base_config": {
        "system_prompt": "回答要简洁",
        "required_tools": ["web_search"]
      }
    }
  ]
}
```

`base_config` 按键合并到已有配置；未传键保留。`node_name`、位置等顶层字段传 `null` 时不会覆盖旧值；嵌套配置能否为 `null` 以节点 Schema 为准。

### 批量删除

`POST /ai/flow/{flow_id}/nodes/batch/delete`

```json
{"node_keys": ["obsolete_node"]}
```

关联边会级联删除。

## 边操作

### 批量创建

`POST /ai/flow/{flow_id}/edges/batch`

```json
{
  "edges": [
    {
      "source_node_key": "start",
      "target_node_key": "assistant",
      "source_handle": "default",
      "target_handle": "default"
    },
    {
      "source_node_key": "web_search",
      "target_node_key": "assistant",
      "source_handle": "tools",
      "target_handle": "tools"
    }
  ]
}
```

合法 handle 配对：

| 边类型 | `source_handle` | `target_handle` |
|---|---|---|
| 普通数据流 | `default` | `default` |
| 工具连接 | `tools` | `tools` |
| 条件分支 | `true` / `false` | `default` |
| 意图路由 | intent key | `default` |

批量接口会校验节点存在性、handle、Agent 结构、工具连接限制及条件分支完整性。

### 批量删除

`POST /ai/flow/{flow_id}/edges/batch/delete`

```json
{
  "edges": [
    {
      "source_node_key": "start",
      "target_node_key": "assistant",
      "source_handle": "default"
    }
  ]
}
```

### 检查工具发现

`GET /ai/flow/{flow_id}/node/{llm_node_key}/connected-tools`

返回指定 LLM 通过工具边发现的工具信息。若预期工具不在结果中，优先检查边方向、两端 handle 和工具节点配置。

## Workflow 执行

`POST /execution/stream/{flow_id}`，响应为 SSE：

```json
{
  "input_data": {"content": "待处理文本"},
  "files": []
}
```

常见事件：`flow_start`、`node_start`、`node_thinking`、`node_content`、`tool_call_start`、`tool_call_end`、`node_done`、`token_usage`、`waiting_human`、`flow_done`、`error`。

- 从 `flow_start.data.execution_id` 保存执行 ID。
- 仅 `flow_done` 且 `data.status` 为 `success` 表示正常结束，最终结果在 `data.output_data`。
- `waiting_human` 表示暂停而非失败。
- `error` 后应读取消息并修复，不要把连接关闭当作成功。

人工输入恢复：

```text
GET  /execution/wait-status/{execution_id}
POST /execution/human-input-stream/{execution_id}
Body: {"input": "用户补充内容"}
```

恢复接口同样返回 SSE，并以状态为 `success` 的 `flow_done` 判断完成。

## Agent 会话

1. `POST /agent/{agent_id}/sessions` 创建会话，请求体为空。
2. 从普通响应的 `data.id` 获取 `session_id`。
3. `POST /agent/{agent_id}/sessions/{session_id}/chat` 发送消息，从普通响应的
   `data.run_id` 获取后台执行 ID。
4. `POST /agent/{agent_id}/sessions/{session_id}/events` 订阅 SSE。

```json
{"content": "帮我分析这段数据", "params": {}}
```

`params` 必须是 JSON 对象，可承载 Agent 输入字段和文件参数。

事件订阅请求：

```json
{"run_id": "chat 返回的 run_id", "after_event_id": 0}
```

后台执行产生的 SSE 包含递增的 `id`。连接中断后使用最后处理的 `id` 作为
`after_event_id` 重新订阅，不要重新提交 `/chat`，否则可能重复执行工具。
已完成执行的事件最多在当前服务进程保留 5 分钟；同一会话启动新执行后，
旧执行不再保证可回放。`waiting_human` 执行会保留到恢复或取消。

页面恢复可查询 `GET /agent/{agent_id}/sessions/{session_id}/running`。
`running=true` 表示会话仍被占用；仅当 `managed_running=true` 且存在 `run_id`
时才能通过 `/events` 重新订阅。直接执行（如定时任务或 WebSocket）只支持状态轮询。
等待人工输入时响应包含 `waiting_human=true` 和 `waiting_event`。

Agent 人工输入恢复：

```text
POST /agent/{agent_id}/sessions/{session_id}/resume
Body: {"human_input": "用户补充内容"}
```

恢复接口同样先返回新的 `run_id`，再通过 `/events` 订阅执行事件。

对话消息分页（刷新恢复用）：

```text
POST /agent/{agent_id}/sessions/{session_id}/messages/page
Body: {"page": 1, "page_size": 50, "condition": {}}
```

LLM 开启结构化输出且结束节点映射了 `structured_output` 时，每轮最后一条
AI 消息带 `end_output` 字段（结束节点输出持久化），可直接读取无需重放 SSE。

工具审批：

```text
POST /agent/{agent_id}/sessions/{session_id}/tool_approval
Body: {"action": "approved"}
```

`action` 只能是 `approved` 或 `rejected`。子 Agent 审批需要使用事件携带的子 Agent ID 和子会话 ID，详见 [子 Agent](sub-agent.md)。

## 修改后验证

1. 再次读取 `/ai/flow/{flow_id}/detail`，确认没有丢失无关配置。
2. 对每个 LLM 调用 `connected-tools`，确认工具可见。
3. 使用最小真实输入执行一次。
4. 检查 `flow_done.data.output_data` 的字段、类型和内容。
