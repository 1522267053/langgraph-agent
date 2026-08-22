---
name: agent-manager
description: 通过 API 创建、配置、调试和维护 Agent 或 Workflow。当用户要求搭建智能体、工作流、子 Agent、节点编排或修改现有流程时使用。
---

# Agent & Workflow Manager

通过 `http://localhost:8000/api` 管理 Agent 和 Workflow。先读取实时配置，再做最小修改，最后通过 SSE 执行验证。

## 强制规则

1. **先查 Schema**：创建或配置节点前，调用 `GET /ai/flow/node-types/{node_type}/config-schema`，不得凭记忆猜字段。
2. **先读后改**：修改已有流程前，先调用 `GET /ai/flow/{id}/detail`，保留无关节点、边和配置。
3. **按 Schema 传原生 JSON**：`base_config` 是对象；数组、对象和布尔值保持原生类型，除非实时 Schema 明确要求字符串。
4. **配置更新是字段级合并**：`POST /ai/flow/{id}/nodes/batch/config` 只覆盖传入的 `base_config` 键，未传键保留。
5. **模型优先用全局默认值**：LLM 的 `provider/model/api_key/base_url` 可留空；系统有默认模型时会自动注入。
6. **工具边不参与执行图**：`source_handle="tools"` 只声明工具能力；普通数据流使用 `default -> default`。
7. **必须执行验证**：创建或修改后实际运行一次，检查 SSE 是否出现 `flow_done` 且 `data.status="success"`，不能只确认 API 返回成功。
8. **不要泄露密钥**：响应中的 `api_key` 只用于原样保留，不在回复或日志中展示。

## 按需参考

`load_skill` 会返回当前目录树；需要细节时用文件读取工具打开对应文档，不要一次加载全部 references。

| 场景 | 文档 |
|---|---|
| API 请求、响应、SSE、恢复执行 | [API 参考](references/api.md) |
| 节点字段、变量路径、边与常见配置 | [节点配置](references/node-config.md) |
| 子 Agent、会话复用、并发与审批 | [子 Agent](references/sub-agent.md) |
| 让 Agent 在运行时修改自身 | [运行时自更新](references/runtime-self-update.md) |

## 选择类型

| 类型 | 适用场景 | 主要入口 |
|---|---|---|
| `flow` | 固定步骤、批处理、API/Python/Shell 编排 | `/execution/stream/{id}` |
| `agent` | 对话、工具调用、记忆、知识库、子 Agent | `/agent/{id}/sessions` |

Agent 仅允许 `start`、`end`、`llm`、`condition`、`intent_router` 及工具节点，并且只能各有一个 `start`、`end`、`llm`。典型主链为 `start -> llm -> end`，能力节点通过 `tools -> tools` 连接到 LLM。

## 标准流程

1. **澄清目标**：确定 `flow` 或 `agent`、输入字段、预期输出、外部依赖和是否需要人工交互。
2. **检查环境**：调用 `GET /config/check`；需要模型时再调用 `GET /config/providers`。
3. **查找复用**：调用 `GET /ai/flow/list?keyword=...`，避免创建重复流程。
4. **创建容器**：调用 `POST /ai/flow/create`，明确 `flow_type` 和 `input_schema`。
5. **查询 Schema**：对每种节点类型调用配置 Schema 接口。
6. **批量建节点**：调用 `POST /ai/flow/{id}/nodes/batch`，使用稳定、语义化的 `node_key`。
7. **批量连边**：调用 `POST /ai/flow/{id}/edges/batch`；工具边和普通边分开判断。
8. **检查工具发现**：调用 `GET /ai/flow/{id}/node/{llm_node_key}/connected-tools`；需要强制调用时，将准确工具名写入 LLM 的 `required_tools`。
9. **检查结构**：再次读取详情，确认节点、边和字段级合并结果符合预期。
10. **执行验证**：使用真实最小输入运行，修复错误后重试，直至收到状态为 `success` 的 `flow_done`。

## API 速查

| 操作 | 方法与路径 |
|---|---|
| 环境检查 | `GET /config/check` |
| 模型提供商 | `GET /config/providers` |
| 查询流程 | `GET /ai/flow/list?keyword=xxx` |
| 创建流程 | `POST /ai/flow/create` |
| 流程详情 | `GET /ai/flow/{id}/detail` |
| 节点 Schema | `GET /ai/flow/node-types/{node_type}/config-schema` |
| 批量创建节点 | `POST /ai/flow/{id}/nodes/batch` |
| 批量配置节点 | `POST /ai/flow/{id}/nodes/batch/config` |
| 批量删除节点 | `POST /ai/flow/{id}/nodes/batch/delete` |
| 批量创建边 | `POST /ai/flow/{id}/edges/batch` |
| 已连接工具 | `GET /ai/flow/{id}/node/{node_key}/connected-tools` |
| 执行 Workflow | `POST /execution/stream/{id}` |
| 创建 Agent 会话 | `POST /agent/{id}/sessions` |
| Agent 对话 | `POST /agent/{id}/sessions/{session_id}/chat` |

所有普通响应使用 `{ "code": 1, "msg": "success", "data": ... }`；HTTP 状态码通常仍为 200，因此必须检查 `code`。

## 最小请求骨架

创建：

```json
{
  "name": "订单查询助手",
  "flow_type": "agent",
  "input_schema": {
    "fields": [{"name": "message", "type": "string", "required": true}]
  }
}
```

批量创建节点：

```json
{
  "nodes": [
    {"node_type": "start", "node_key": "start", "base_config": {"input_variables": [{"name": "message", "type": "string", "required": true}]}},
    {"node_type": "llm", "node_key": "assistant", "base_config": {"user_prompt": "{{message}}"}},
    {"node_type": "end", "node_key": "end", "base_config": {"output_variables": [{"name": "content", "type": "string", "source": "nodes.assistant.result"}]}}
  ]
}
```

批量创建边：

```json
{
  "edges": [
    {"source_node_key": "start", "target_node_key": "assistant", "source_handle": "default", "target_handle": "default"},
    {"source_node_key": "assistant", "target_node_key": "end", "source_handle": "default", "target_handle": "default"}
  ]
}
```

## 执行判断

- Workflow 请求：`{"input_data": {...}}`。若遇到 `waiting_human`，使用 `execution_id` 调恢复接口。
- Agent 先创建会话，再发送 `{"content": "...", "params": {}}`；`params` 必须是 JSON 对象。
- 成功以 SSE 的 `flow_done.data.status="success"` 为准；`error`、取消或连接关闭都不能视为完成。
- Agent 工具审批和子 Agent 审批的恢复路径不同，按 [API 参考](references/api.md) 与 [子 Agent](references/sub-agent.md) 处理。

## 高频陷阱

- `input_schema` 不是说明文档，它决定 `{{变量}}` 能否从输入解析。
- LLM 必须有 `user_prompt`；上下文可用 `{{input}}`、`{{query}}` 或上游变量。
- `required_tools` 使用 `connected-tools` 返回的实际工具 `name`，不是节点 `node_key` 或显示名。
- 工具调用通常还需要 `tools -> tools` 边；遗漏后 LLM 看不到工具。
- 条件节点分支使用 `true` / `false` handle；意图路由使用动态 intent key。
- 卡片输入、循环变量、Python 包装和媒体文件有专门规则，修改前读取 [节点配置](references/node-config.md)。
- 子 Agent 工具名为 `ask_{node_key}`；`session_mode` 是调用参数，不是节点配置，详见 [子 Agent](references/sub-agent.md)。
- 修改对话历史会同步影响 checkpoint；不要绕过正式 API 直接改数据库。

## 完成检查

- 名称、描述和输入字段能让使用者理解用途。
- 每种节点配置都来自当前实时 Schema。
- 普通边、条件边和工具边的 handle 正确。
- LLM 的 `required_tools` 名称与 `connected-tools` 返回值一致。
- 详情接口返回的配置符合预期，没有覆盖无关字段。
- 至少一次真实执行收到状态为 `success` 的 `flow_done`，输出结构满足目标。
