# 运行时自更新

本页用于 Agent 在用户明确授权后，通过 Agent Manager API 修改自身流程。自更新仍然是普通持久化配置变更，不得绕过 API 直接操作数据库。

## 前提

- 已确认当前 Agent 的 `flow_id`；建议写入 system prompt，不确定时用 `/api/ai/flow/list?flow_type=agent&keyword=...` 查询并核对名称与描述。
- 当前 Agent 已连接能发起本地 HTTP 请求的 API、Python 或 Shell 工具。
- 已加载本 Skill，并按需读取 [API 参考](api.md) 和 [节点配置](node-config.md)。
- 用户明确要求或批准这次修改；不能因一次工具失败擅自永久改变自身权限。

普通 Agent 没有直接写流程节点或边的内置工具。要自更新，必须预先连接通用 API 节点（`use_preset_for_tool=false`）或具备本地 HTTP 能力的 Shell/Python 工具。

## 安全流程

1. `GET /api/ai/flow/{flow_id}/detail`，保存当前节点、边和配置。
2. `POST /api/flow-snapshot/create/{flow_id}` 创建手动快照，记录变更目的。
3. 对涉及的每种节点调用实时 config-schema。
4. 计算最小增量：优先新增或字段级配置，不重建整个 Agent。
5. 先创建节点，再创建边，最后按需配置 LLM 的 `required_tools`。
6. 再次读取详情与 connected-tools，确认结构和工具名。
7. 在下一轮或新会话中做最小真实验证。
8. 验证失败时修复；无法安全修复则用快照恢复，并向用户说明。

快照请求示例：

```text
POST /api/flow-snapshot/create/{flow_id}
Body: {"name": "自更新前备份", "description": "新增资料研究子 Agent"}
```

恢复：

```text
POST /api/flow-snapshot/restore/{snapshot_id}
```

## 生效边界

- 当前一次 SSE 执行使用开始时构建的图，执行中新增的节点或工具不会加入本轮。
- Agent 每次发送新消息时会重新读取 Flow 并构建图；拓扑改动后优先新建会话验证，避免旧 checkpoint 干扰判断。
- 配置 API 返回成功不代表新图可执行，必须等下一轮出现状态为 `success` 的 `flow_done`。
- 删除或修改历史消息必须使用正式会话 API，以便同步清理 checkpoint。

## 最小修改原则

- 不删除自身唯一的 `start`、`end` 或 `llm` 节点。
- 不在当前执行中移除正在使用的管理 Skill、Shell、API 或审批能力。
- 不覆盖完整 `base_config`；批量配置接口按字段合并，只发送目标键。
- 新工具先连接并验证，再移除旧工具，避免失去恢复通道。
- `required_tools` 必须使用 connected-tools 返回的工具名；不要把所有新工具默认设为必需。
- 改子 Agent 前先独立验证子 Agent，且禁止引用自身或形成嵌套委派。

## 从工具发起请求

使用现有 HTTP 工具时，发送原生 JSON 对象并检查响应 `code`。使用 Shell 时，可运行短 Python 脚本通过 `urllib.request` 调 `127.0.0.1:8000`，但脚本不得打印密钥或完整敏感配置。

认证中间件会放行 `127.0.0.1` 和 `::1` 的内部工具请求，无需附带浏览器 Cookie。若仍返回 401，说明请求未被识别为回环流量；不要读取密码或伪造凭据，改用现有认证能力或请用户处理。

## 常见自更新任务

### 新增工具

1. 查询节点 Schema 并创建工具节点。
2. 创建 `tool_node:tools -> llm:tools` 边。
3. 调 connected-tools 获取实际工具名。
4. 仅在业务确实要求每轮调用时更新 `required_tools`。

### 更换提示词

只更新 LLM 的 `system_prompt` 或 `user_prompt`，保留模型、历史、审批和工具检查字段。提示词中说明新增能力、调用时机和禁止事项。

### 新增子 Agent

按 [子 Agent 参考](sub-agent.md) 检查目标 Agent，创建 `sub_agent` 节点和工具边。并行任务由每次调用的 `session_mode` 控制，不写入节点配置。

## 完成回报

向用户说明修改了哪些节点或字段、快照是否创建、验证输入和结果。不要回显 `api_key`、Cookie、密码哈希或完整敏感响应。
