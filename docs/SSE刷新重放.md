# SSE 流式内容刷新重放（Agent 侧）

> 状态：已实施（未 commit）
> 范围：仅 Agent 对话链路（`/agent/{id}/sessions/{sid}/chat|resume|events`）
> 背景：流式输出 `A→B→C` 时（C 正在进行）刷新页面，恢复后应完整展示 `A→B→C + C 已流出的内容`，
> 并继续接收 C 的后续增量。

## 1. 问题

刷新后前端按 `after_event_id=0` 全量回放事件，但处理器被 `createReattachHandlers`
按「刷新瞬间的 `last_event_id`」过滤——**刷新前已产生的内容 chunk 全部被丢弃**。

根因不是「后端没有回放能力」（`_AgentRun.events` 早已缓存全量事件），而是
**水位口径错配**：

| 口径 | 含义 | 可比性 |
|------|------|--------|
| `last_event_id` | run 已发布的事件**总数** | 与落库范围无关 |
| DB 重建范围 | **已落库的节点**（按 `save_to_db` 时机） | 与事件计数无关 |

两者不可比 → 无论取哪个值都切不准边界。

## 2. 基石不变量

> **`node_done` 事件发布 ⇒ 该 `node_key` 的消息必定已落库。**

依据（代码顺序保证）：

1. `app/agent_flow/node_handlers/llm_tool_handler.py:839` —
   `await msg_buf.save_to_db()` 在 handler **返回之前**完成；
2. handler 返回后 LangGraph 才产出 `updates` 流 → 才由
   `app/services/agent_executor_service.py:1987` 发布 `node_done`。

因此定义：

```
content_watermark_event_id = 最后一个 node_done 事件的 id
```

**内容类事件的 `id > watermark` ⇔ 属于「正在进行中、尚未落库」的节点 ⇔ 必须回放。**

## 3. 事件切分表（本方案唯一新增语义）

| 类别 | 事件类型 | `id <= watermark` | 理由 |
|------|---------|------------------|------|
| **内容类（切分）** | `node_thinking` `node_content` `tool_call_start` `tool_call_end` `token_usage` `knowledge_citations` `llm_retry` `file_changed` | **跳过** | 随 `save_to_db` 落进 `AgentMessage`，DB 重建已呈现；回放会重复 |
| **等待响应类** | `tool_approval_required` `question_request` | **全量回放** | 与落库无关；`subscribe_run` 的 `remaining_seconds` 重算必须保留 |
| **快照幂等类** | `todo_update` `sub_agent_progress` `flow_preview` `context_compressing` | **全量回放** | 快照替换语义（重复无害）；`todo_update` 不落消息表，过滤会致计划面板空白 |
| **终态 / 生命期类** | `flow_start` `node_start` `node_done` `flow_done` `error` `tool_call_limit` `waiting_human` | **全量回放** | handler 为 no-op 或终态收敛（`onFlowDone` 刷新本身幂等） |

> ⚠️ 切分必须落在**后端**（确定性归代码），前端 `after_event_id` 保持 0
> —— 改为传 watermark 会连带跳过 watermark 之前的审批/反问事件，弹窗恢复失效。

## 4. 数据流

```
                            ┌── 已落库节点（id <= watermark）→ 跳过回放 → DB 重建呈现
subscribe_run(after=0) ─────┤
                            └── 进行中节点（id > watermark）→ 回放 → 空占位行长出内容
                                                                    （前端累积语系直接续写）
```

前端 `useStreamingMessage` 的 handler 天然支持「在空占位行上重建」：
重放 `node_thinking`/`node_content` chunk 会建立 `currentSegmentType` 与累积值，
后续新 chunk 续写同一段，不会错位新开段。

## 5. 改动清单

### 后端 `app/services/agent_executor_service.py`
1. 新增 `_PERSISTED_CONTENT_EVENT_TYPES`（内容类 8 项）
2. `_AgentRun` 增加 `content_watermark_event_id: int = 0`
3. `_publish_agent_run_event`：发布 `node_done` 时推进水位
4. `subscribe_run`：按类型 + 水位切分回放
5. `get_run_status`：透出 `content_watermark_event_id`

### 前端
6. `src/api/agent.ts`：`AgentRunStatus` 增加 `content_watermark_event_id`
7. `src/stores/agentOptimized.ts`：
   - 刷新恢复 running / waiting 两分支改用 `createStreamHandlers`
   - **删除 `createReattachHandlers`**（消除双重过滤；保留它必须同步传对水位，
     两个"看起来都像水位"的数字并存是维护隐患）

## 6. 已知限制：竞态窗口（档位 a，已确认接受）

```
handler 内 save_to_db 提交 ──[微秒~毫秒窗口]──> node_done 发布（水位才推进）
              DB 已有数据                        watermark 仍指向上一个节点
```

若刷新**恰好**命中该窗口，该节点内容会被「DB + 回放」渲染两份。
判定：窗口极窄、同进程微秒级、用户再刷一次即自愈 → **接受**，不做 contextvar 主动推进。

## 7. 回归清单

- [ ] 多节点流式进行中刷新：`A→B→C + C 当前内容`完整、无重复
- [ ] 刷新时机取「节点执行中」「`node_done` 紧邻前后」三点
- [ ] 审批弹窗刷新恢复（含倒计时剩余秒数正确）
- [ ] 反问弹窗刷新恢复
- [ ] 子 Agent 运行中刷新（`call_sub_agent_*` 预览恢复）
- [ ] 无刷新正常流式（零影响）
- [ ] `waiting_human` 分支行为不变
- [ ] token 数值不比刷新前偏大
- [ ] 向上翻页历史不丢