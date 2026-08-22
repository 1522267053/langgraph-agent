# 子 Agent 参考

`sub_agent` 节点把另一个 Agent 暴露为父 Agent 的工具。父 LLM 负责决定何时委派、传什么任务以及是否复用子会话。

## 前置约束

被引用对象必须：

- `flow_type` 为 `agent`，且不能是父 Agent 自身。
- 有明确、非空的 `description`，该描述会进入工具说明，帮助父 LLM 判断能力边界。
- 不直接或通过 card 包含另一个 `sub_agent` 节点，禁止递归委派链。
- 具备可运行的 `start -> llm -> end` 主链，并在投入使用前独立验证。
- 建议先设置为已发布状态 `status=1`，避免把未完成配置暴露给父 Agent。

## 创建与连接

节点只配置一个 `agent_id`：

```json
{
  "nodes": [
    {
      "node_type": "sub_agent",
      "node_key": "researcher",
      "node_name": "资料研究员",
      "base_config": {"agent_id": 42}
    }
  ]
}
```

通过工具边连接父 Agent 的 LLM：

```json
{
  "edges": [
    {
      "source_node_key": "researcher",
      "target_node_key": "assistant",
      "source_handle": "tools",
      "target_handle": "tools"
    }
  ]
}
```

生成的工具名是 `ask_{node_key}`，上例为 `ask_researcher`。用 connected-tools 接口确认名称，不要使用 Agent 名称猜测。

同一父 LLM 可以连接多个 `sub_agent` 节点，用不同 `node_key` 表达不同角色或能力边界。

## 工具参数

每个 `ask_*` 工具至少包含：

| 参数 | 类型 | 说明 |
|---|---|---|
| `task` | string | 委派给子 Agent 的任务，必填 |
| `session_mode` | `resume` / `new` | 会话策略，默认 `resume` |

子 Agent 的 `input_schema.fields` 会动态扩展为工具参数：

- 名为 `message` 的字段不会重复暴露，`task` 就是子会话的用户消息。
- `file_list` 转为文件 ID 数组 `list[int]`。
- 其他字段按 `string`、`number`、`integer`、`boolean` 转换。
- 必填性沿用子 Agent 的 input schema。

父 Agent 的输入和工具结果不会自动成为子 Agent 上下文。需要的数据必须放进 `task` 或上述动态参数。

## 会话模式

| 模式 | 行为 | 适用场景 |
|---|---|---|
| `resume` | 同一父会话、同一 `sub_agent` 节点复用首次创建的子会话 | 连续追问、分阶段任务、需要历史上下文 |
| `new` | 每次调用创建独立子会话 | 无依赖任务、隔离上下文、并行研究 |

`session_mode` 是每次工具调用的参数，不是节点 `base_config`。`resume` 的复用键包含父会话和节点，因此：

- 换父会话不会复用旧子会话。
- 同一子 Agent 若通过两个不同 `node_key` 接入，也会维护两条会话链。
- 没有有效父会话上下文时，`resume` 会退化为创建新会话。

## 并发规则

父 LLM 在同一轮返回多个工具调用时：

- 同一个 `ask_*` 工具的多个 `resume` 调用串行执行，避免并发写入同一子会话。
- 不同 `ask_*` 工具的 `resume` 调用可并行，各自使用独立会话。
- `session_mode=new` 调用可并行，即使目标是同一个子 Agent 节点。
- 子 Agent 调用与其他普通工具调用保持并行。

需要并行时，在父 Agent 的 system prompt 中明确要求“一次发起多个独立工具调用”，并为各调用使用 `new`。模型是否真正并发仍取决于它是否在同一轮生成多个 tool calls。

## 运行行为

- `ask_*` 是阻塞工具，子 Agent 完成后才向父 LLM 返回文本。
- 等待期间每 20 秒发送 `sub_agent_progress` 心跳，保持父 SSE 连接活跃。
- 子 Agent 的最终 LLM 文本通过 `flow_done.data.output_data.content` 成为工具结果。
- 子执行的 `error` 会转换为 `{"error": "..."}` 返回父 LLM。
- 工具结果进入统一截断流程，阈值由服务配置控制。
- 子 Agent 使用自己的模型、工具、Skill、知识和记忆配置，不自动继承父 Agent 的能力节点。
- 子会话会持久化并显示在子 Agent 的会话列表中，初始标题为 `[子Agent调用] {task}` 的截断文本。

如果父 Agent 需要综合多个子结果，应在 system prompt 中要求先完整收集工具返回，再进行比较和汇总。

## 工具审批

子 Agent 触发危险工具审批时，事件通过父 Agent 的 SSE 流转发。事件包含：

```json
{
  "type": "tool_approval_required",
  "data": {
    "is_sub_agent": true,
    "sub_agent_id": 42,
    "sub_session_id": 123,
    "sub_agent_name": "资料研究员"
  }
}
```

审批必须提交到子 Agent 和子会话，而不是父会话：

```text
POST /api/agent/{sub_agent_id}/sessions/{sub_session_id}/tool_approval
Body: {"action": "approved"}
```

`action` 可为 `approved` 或 `rejected`。审批等待最多 5 分钟。

## 取消与故障

- 取消父 Agent 时，当前子任务收到取消信号，子会话执行和待审批状态一并终止。
- 若工具未出现在 connected-tools 中，检查 `agent_id`、引用 Agent 是否存在、工具边方向和 handle。
- 若工具返回空字符串，优先检查子 Agent 的 LLM 是否在工具调用后生成了最终文本。
- 若连续任务丢失上下文，确认每次调用都是同一父会话、同一工具名且 `session_mode=resume`。
- 若并行任务互相污染，改用 `session_mode=new`。
