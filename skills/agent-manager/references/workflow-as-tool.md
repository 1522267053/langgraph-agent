# Workflow as Tool — Flow 作为 Agent 工具

## 背景

让 Agent 通过工具节点调用普通 Flow，**保留 Flow 的中断/审批能力**。与现有 `sub_agent`（调子 Agent）并列。

## 与 dify Workflow → Tool 的差异

| 维度 | dify Workflow→Tool | 本项目 Workflow as Tool |
|------|--------------------|------------------------|
| 中断能力 | ❌ 禁用（`pause_state_config=None`） | ✅ **保留 interrupt**（核心 Feature） |
| 工具调用契约 | 单次调用 | **单工具双模式**（execute / resume） |
| 工具返回值 | 字符串 | **JSON structured**（`status: completed/interrupted/error`） |
| 类型限制 | 不允许引用 Chatflow/Agent | 同（`flow_executor_service` 强校验 `FlowType.FLOW`） |
| 防嵌套自调 | 检测 | 保留（contextvar 调用栈） |

## 用法

### 1. 准备 Flow
- 创建一个普通 Flow（`flow_type=flow`）
- Flow 的 `input_schema` 定义入参（除 `message` 外）
- Flow 内可包含 human / human_assist 节点实现审批
- **注**：暂不限制 status（前端无发布入口，草稿也可作为 Flow-as-Tool 调用对象；完整发布流程为独立 P3）

### 2. 在 Agent 编辑器拖入 Flow 工具
- 节点面板 → "工具"分类 → **Flow工具**
- 画布上选中节点 → 右侧配置 → 选择 Flow
- 把 Flow 工具节点的 `tools` handle 连接到 LLM 节点的 `tools` handle
- 保存发布 Agent

### 3. LLM 自动获得工具

Agent 启动时，LLM 会看到工具签名：

```
工具名：flow_<flow_id>_tool
参数：
  - <Flow.input_schema 中的字段，除 message 外>
  - execution_id: int | None（resume 模式标识）
  - human_input: str | None（resume 模式必填）
```

## 单工具双模式

| 场景 | LLM 调用 | 后端行为 |
|------|---------|---------|
| 首次调用 | `flow_<id>_tool(period="this_week")` | execute 模式：启动 Flow 新执行 |
| Flow 中断 | 后端返回 `{status:"interrupted", execution_id, prompt}` | LLM 看到结果后决定下一步 |
| LLM 决定继续 | `flow_<id>_tool(execution_id=789, human_input="确认")` | resume 模式：恢复等待中的 Flow |

**关键**：两次调用**用同一个工具名**。LLM 不需要知道模式切换，只需"再次调用 + 提供上次返回的 execution_id"。

## 返回值契约

### 成功（completed）
```json
{
  "success": true,
  "status": "completed",
  "output_data": {"final_result": "..."}
}
```

### 中断（interrupted）
```json
{
  "success": true,
  "status": "interrupted",
  "execution_id": 789,
  "prompt": "确认Q3促销数据？",
  "context": "..."
}
```

### 失败（error）
```json
{
  "success": false,
  "status": "error",
  "error": "Flow 执行失败的具体原因"
}
```

## 中断处理时序（典型场景：多步审批）

```
[Agent LLM] → flow_42_tool(period="this_week")
              ↓
[Flow 工具 handler] → flow_tool_service.invoke(execute)
              ↓
[Flow 42] python 读销售表 → LLM 总结 → human 节点
              ↓
[Flow 42] waiting_human(execution_id=789, prompt="确认Q3促销数据？")
              ↓
[handler] 返回 {status:"interrupted", execution_id:789, prompt:"..."}
              ↓
[Agent LLM] 收到 ToolMessage → 决策
              ↓
   选项 A: flow_42_tool(execution_id=789, human_input="确认")  ← 自动继续
   选项 B: 先回用户问"是否确认Q3促销？"  ← 询问后再决定
              ↓
（如选 A）
[Flow 工具 handler] → flow_tool_service.invoke(resume)
              ↓
[Flow 42] 继续执行 → flow_done(output_data={...})
              ↓
[handler] 返回 {status:"completed", output_data:{...}}
              ↓
[Agent LLM] 整合结果 → 回复用户
```

## 实现要点

### 服务端

| 文件 | 职责 |
|------|------|
| `app/constants/node_types.py` | `NODE_REGISTRY` 注册 `flow_tool` 节点元数据 |
| `app/models/flow_node.py` | `NodeType.FLOW_TOOL` 枚举值 |
| `app/agent_flow/tool_resolver.py` | `LlmToolConfig.flow_tool_node_keys/flow_tool_configs` |
| `app/services/flow_tool_service.py` | `invoke()` 单入口双模式 + contextvar 防嵌套自调 + `_consume()` 终结事件收尾 |
| `app/agent_flow/node_handlers/flow_tool_handler.py` | `FlowToolNodeHandler` 注册 + `get_tool()` 返回 StructuredTool + `_build_flow_tool_schema()` Pydantic 模型 |

### 前端

| 文件 | 职责 |
|------|------|
| `frontend/src/types/flow.ts` | `CardNodeType` 加 `'flow_tool'` |
| `frontend/src/components/FlowEditor/config/types.ts` | `FlowToolConfig` 类型 |
| `frontend/src/components/FlowEditor/nodes/FlowToolNode.vue` | 节点画布组件（自动发现） |
| `frontend/src/components/FlowEditor/config/FlowToolConfig.vue` | 节点配置面板（Flow 下拉） |
| `frontend/src/components/FlowEditor/nodeRegistry.ts` | `registry.flow_tool` 元数据 |

## 已知限制

1. **Flow 类型必须为 flow**——`agent` 类型不能作为工具（`flow_executor_service` 强校验 `FlowType.FLOW`）
2. **防嵌套自调**：调用栈中已含同 flow_id 则拒绝（`flow_tool_service` 的 `_FLOW_TOOL_STACK` contextvar）
3. **调用深度未限**——v4 阶段 1 仅做"存在性检查"；后续可加 stack 深度 ≤ 5 限制
4. **Tool schema 字段多 → token 占用**——与 dify 一致；Flow.input_schema.fields 限制前端编辑
5. **`message` 字段剔除**——Agent 模式的主入口字段，Flow-as-Tool 不使用
6. **Agent 必须先 plan + 选 tool**：LLM 在拿到 `interrupted` 后需自主决策调 resume（不强制）；若 LLM 选错路径会失败

## 适用场景

| 场景 | 用法 |
|------|------|
| 多步审批 | Agent 调审批 Flow → 等业务确认 → 自动继续 |
| 多步数据处理 | Agent 调 ETL Flow → 中途业务确认 → 继续 ETL |
| 复杂子任务委派 | Agent 调一个复杂业务 Flow（多节点 + 人工介入），把结果汇总到上层 |
| A/B 测试 / 灰度 | Agent 根据场景选不同 Flow 工具调用 |

## 调试技巧

```bash
# 启动期断言（NodeType ↔ NODE_REGISTRY 一致性）
poetry run python -c "from app.models.flow_node import NodeType; print(list(NodeType))"

# 验证 handler 自动注册
poetry run python -c "
from app.agent_flow.node_handlers import flow_tool_handler
from app.agent_flow.handler_registry import NodeHandlerRegistry
print(NodeHandlerRegistry.get_handler_class('flow_tool'))
"

# 端到端集成自验
poetry run python -m workspace.temp._check_handler
poetry run python -m workspace.temp._check_consume
poetry run python -m workspace.temp._check_full
```

## 后续迭代

- [ ] 调用栈深度限制（≤ 5）
- [ ] wechat-bot 端到端 interrupt 体验（阶段1：bridge 加 on_event 回调 + _await_user_reply Future）
- [ ] ws-gateway 把 Flow-as-Tool 暴露为远程工具（让其他 client 也可调用）
- [ ] 前端"Flow 调用进度"实时展示（通过透传 node_start / node_content 事件）
- [ ] 工具审批接入（如果 Flow 工具节点本身需要审批 LLM 是否调用，与 human_assist 节点是不同语义）
