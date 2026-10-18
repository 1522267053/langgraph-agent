# 工具审批（Tool Approval）

工具节点在执行危险操作前可触发用户审批，**Agent 与 Flow 两种模式都支持**。本文档说明：
1. 哪些节点支持审批、字段语义
2. 各节点运行时真实工具名格式（前端 connected-tools 接口拿到的 `name`）
3. 审批触发的判定流程与正则匹配 4 层设计
4. 前后端实现要点（基类、方案 C 工具名下拉）
5. 双模式分流（Agent/Flow 均走 SSE Future 等待，Flow 含整批合并）与免审授权

恢复接口与 SSE 事件已分别在 [API 参考](api.md#sse-事件) 和 [子 Agent](sub-agent.md#工具审批) 描述，本文不重复。

## 节点覆盖范围

只有 **LLM 可调用的工具节点** 才需要审批；纯编排节点（start / end / condition / intent_router / card / loop / wait）不触发。

| 节点 | 是否支持审批 | 备注 |
|---|---|---|
| `shell` | ✅ | 已支持，shell_handler 历史实现 |
| `ssh` | ✅ | 已支持，复刻 shell 模式 |
| `browser` | ✅ | 12 工具统一走审批闭包；未配置白名单/正则时零开销 |
| `python` | ✅ | 通用 + preset 两种模式都支持 |
| `api` | ✅ | 通用 + preset 两种模式都支持 |
| `mcp` | ✅ | 工具名依赖 server 连接过的缓存 |
| `skill` / `memory` / `todo` / `knowledge` / `agenda` / `sub_agent` / `question` | ❌ | 只读或编排节点，无危险操作 |

> 表里节点类型与 NODE_REGISTRY 一致；新增工具节点时同步评估是否加入审批。

## 配置字段

`BaseNodeConfig`（所有节点的 `base_config` 父类）提供两个字段，**两者独立生效，可只配其一，也可都配**：

| 字段 | 类型 | 作用 |
|---|---|---|
| `approval_required_tools` | `list[str]` | 完整工具名白名单；列表内的工具触发审批。空列表 = 关闭审批 |
| `approval_required_patterns` | `list[str]` | 正则列表，对工具的"内容字符串"做 `re.search(..., re.IGNORECASE)`；任一命中即触发审批。空列表 = 关闭内容匹配 |

判定逻辑（伪代码）：

```python
needs_approval = (
    tool_name in approval_required_tools
    or any(re.search(p, content_for_pattern, re.IGNORECASE) for p in approval_required_patterns if content_for_pattern)
)
```

> **Agent 与 Flow 两种模式均生效**（2026-10-18 起；此前仅 Agent，Workflow 忽略）。
> 判定层（白名单/正则/免审授权）两模式共用；交互层分流见下文「双模式分流」。

## 各节点 content_for_pattern 语义

`content_for_pattern` 是传给正则匹配的"内容字符串"。每个工具节点定义自己的语义，必须能让用户在正则里写出意图明确的模式（例如 `rm\s+-rf`、`https?://[^/]+\.internal`）。

| Handler | content_for_pattern |
|---|---|
| `shell` / `ssh` | `command`（命令全文） |
| `browser` | 按工具区分：navigate 传 `url`，click 传 `ref`，input 传 `text`（要输入的文本），evaluate 传 `script`，get_content 传 `selector`，wait_for 传 `text`；其余工具传空字符串 |
| `python` 通用 / preset | `code`（代码全文） |
| `api` 通用 / preset | `f"{method} {api_url} {body_text[:200]}"` |
| `mcp` | `f"{tool_name} " + " ".join(str(v)[:200] for v in flat.values())` |

**pattern 内容匹配是全文搜索（`re.search`），不需要 `^` 前缀。** `content_for_pattern` 为空字符串时短路跳过正则层（避免误判）。

## 运行时真实工具名

LLM 调用工具时使用的是 `name` 字段（不是节点的 `node_key`）。配置 `approval_required_tools` 前必须从 connected-tools 接口拿准确名称。**各节点实际生成的工具名格式：**

| 节点 | use_preset_for_tool | 工具名 | 说明 |
|---|---|---|---|
| `python` | False | `python_executor_<node_key>` | 通用模式默认 |
| `python` | True（`tool_name="x"`） | `python_executor_x` | preset 模式带 `python_executor_` 前缀 |
| `api` | False | `api_call_tool_<node_key>` | 通用模式默认 |
| `api` | True | `api_<node_key>` | preset 模式无 `_call_tool` 中缀 |
| `mcp` | — | `mcp__<server_name>__<tool_name>` | 下划线双段分隔（LangChain 标准） |
| `shell` | — | `shell_executor_<node_key>` | — |
| `ssh` | — | `ssh_executor_<node_key>` | — |
| `browser` | — | `browser_<action>_<node_key>`（如 `browser_click_xxx`） | 12 个工具名固定 `browser_` 前缀 |

> Python preset 工具名的 `python_executor_` 前缀容易漏写——必须从 connected-tools 复制，不要凭记忆。

## 审批触发流程

1. **白名单检查**：工具名是否在 `approval_required_tools` 列表中
2. **正则检查**：`content_for_pattern` 非空 + 模式非空 + 任一正则命中
3. **免审授权检查**：同域同工具已有「批准并记住」授权（session_tool_grant 表）→ 直接放行，不弹窗
4. **任一命中** → 按模式分流（见下节）
5. 用户决策 → 后端写审计终态（interaction_audit_log）→ 工具按结果执行 / 返回拒绝文本

**返回语义**：

| 后端返回 | 工具表现 |
|---|---|
| `approved` | 放行，正常执行 |
| `rejected` | 返回 `{"error": "用户未批准..."}`，LLM 收到错误后可换工具或放弃 |
| `timeout`（Agent/Flow 均 60 分钟，future 统一超时） | 同 rejected |

## 双模式分流（Agent / Flow 均走 SSE Future 等待）

判定层共用，交互层按运行域分流（**同一等待机制**）：

| | Agent 模式 | Flow 模式 |
|---|---|---|
| 运行域标识 | `_session_id > 0`（thread_id=`agent_{id}`） | `_execution_id > 0`（thread_id=`flow_{id}`） |
| 交互机制 | SSE `tool_approval_required` 事件 + 内存 Future 等待 | 同左（2026-10-18 起弃用 interrupt 路线） |
| 审批队列 key | session_id | execution_id（两域主键独立，队列按 key 隔离） |
| 批次粒度 | 逐工具（队列化，N/M 进度） | **整批合并**：并行调用收集窗口合并进同一批次，一次弹窗一次决策 |
| 前端弹窗 | AgentChat 审批弹窗（agentOptimized store） | HumanInputDialog 独立审批弹窗（useFlowExecution） |
| 提交接口 | `POST /agent/{id}/sessions/{sid}/tool_approval` | `POST /execution/tool-approval/{eid}` |
| 超时 | 3600 秒（USER_RESPONSE_TIMEOUT_SECONDS） | 同左（future 等待统一超时；取消执行时审计记 cancelled） |
| 刷新恢复 | SSE 重放（审批事件带 expires_in 重算） | SSE 重放同左（审批不再产生 WAITING_HUMAN，图不暂停） |

**为何 Flow 弃用 interrupt 路线**（2026-10-18）：LangGraph interrupt 会暂停整张图并在 resume 后**从头重执行节点**——LLM 被重新调用生成新工具调用，旧工具段在前端永远停在「执行中」（新事件是新 tool_call_id），且多批审批连环弹窗。future 路线图不暂停，SSE 推审批事件，工具协程 `await Event` 等决策，与 Agent 完全同构。

**Flow 整批审批批次合并**（`base_handler` 三方法结构，铁律：锁内只做内存快速决策，任何 await 必须在锁外）：

1. `_join_or_create_batch`（锁内毫秒级）：查免审授权 → 加入活跃批次或创建新批次（创建者**先不发事件**）
2. `_collect_and_emit_batch`（锁外）：创建者收集窗口 ~200ms（100ms 轮询，无新增即收敛），并行兄弟进锁 JOIN，收敛后一次性发全量 ToolApprovalEvent（多工具卡片一次弹出）
3. `_await_batch_decision`（锁外）：全体等同一 Event，一次决策放行整批；创建者负责清理批次状态

慢兄弟兜底：窗口关闭后才进锁的工具走 JOIN 分支重发全量事件（前端弹窗刷新）。

## 免审授权（「批准并记住」）

审批弹窗提供三按钮：**拒绝 / 批准并记住 / 批准**。

- 「批准并记住」写 `session_tool_grant` 表（scope + session_id + tool_name 唯一），同域同工具后续调用自动放行
- **授权范围按域隔离**：Agent=会话级（scope=agent，session_id=会话ID），Flow=执行级（scope=flow，session_id=执行记录ID，执行结束失效）——两域主键独立自增会撞号，查询强制带 scope 条件
- 授权命中时审计仍记 `approved`（由决策路径写入），免审放行不产生新审批批次
- 审计落盘：每次审批全生命周期记录在 `interaction_audit_log`（kind=approval/question 统一表，scope 判别域），含 asked 快照（工具调用/原因/选项）与终态（approved/rejected/timeout/cancelled/answered）

## 正则匹配 4 层设计

`BaseNodeHandler._check_and_request_approval` 内层实现：

1. **空字符串短路**：`if content_for_pattern and approval_patterns:`——两者任一为空直接跳过正则
2. **懒编译 + tuple 内容缓存**：`_ensure_patterns_compiled(cfg)` 用 `tuple(patterns)` 内容做 cache key，避免每轮重编译
3. **IGNORECASE + search**：`re.search(pattern, content, re.IGNORECASE)`——免写 `^`，且忽略大小写
4. **短路退出 + 原始 pattern 回传**：命中后立即退出循环，`matched_pattern` 取原始字符串（前端 reason 文案展示用户直观）

**未用替代方案的考量**：

| 替代方案 | 弃用原因 |
|---|---|
| `fnmatch`（glob） | 写不出 `\brm\s+-rf\b` 这类需要词边界的模式 |
| 子串 `in` | 写不出 `https?://[^/]+\.internal` 这类需要元字符的模式 |
| AST（Python 强制） | 用户写正则门槛低，AST 需要二次学习 |
| 前缀匹配（`startswith`） | 覆盖不到"命令字符串中段含 rm -rf"的常见场景 |

## 前端：方案 C 运行时工具名下拉

用户配 `approval_required_tools` 时，需要从下拉里选工具名而不是手填。后端**已有接口** `POST /flow/{flow_id}/connected-tools/resolve` 返回运行时真实工具名，前端 3 个组件 watch 关键配置后调它：

| 组件 | watch 触发 key | 下拉展示的工具名 |
|---|---|---|
| `PythonConfig.vue` | `[currentNodeId, tool_name, use_preset_for_tool]` | `python_executor_<key>` 或 `python_executor_<tool_name>` |
| `ApiConfig.vue` | `[currentNodeId, api_url, method, use_preset_for_tool]` | `api_call_tool_<key>` 或 `api_<key>` |
| `McpConfig.vue` | `[currentNodeId, ...mcp_server_ids]` | `mcp__<server_name>__<tool_name>` |

公共 UI 抽到 `ApprovalConfigSection.vue`，接收 `availableTools: string[]` + 当前选中列表，内部维护 `el-select multiple` + `allow-create`（允许手动填未在列表中的工具名）。

**竞态保护**：组件内 `let requestVersion = 0`，每次 watch 触发时自增；请求回调里比较 `version` 是否仍是当前值，旧请求直接丢弃，防止 A→B→A 切换时旧请求污染。

### MCP 工具列表来源与已知限制

MCP 工具列表按**三级读取**：① 内存连接缓存（存活 10 分钟）→ ② DB 持久缓存 `McpToolCache`（服务重启不丢，毫秒级）→ ③ 两者皆无（服务器从未连接过）才真实建连预热。因此**重启后下拉不再为空**（DB 缓存兜底）；仅全新服务器首次使用需一次建连（stdio 类型需数秒）。

- 下拉仍为空时：检查 MCP 服务器是否在线（预热失败不报错，静默返回空）；`allow-create` 仍可自由输入完整工具名
- `McpConfig.vue` 的 resolve 请求按 `mcp_server_ids` 序列化值去重触发，仅服务器增删时重新拉取
- **审批覆盖两条路径**（2026-10-18 起）：工具路径（LLM 自主调用）与直接执行模式（固定工具挂主干）均生效——provider 构建时透传 execution_id，Flow 模式直执行同样走 future 审批；此前仅工具路径生效

## 后端基类实现要点

`app/agent_flow/node_handlers/base_handler.py` 提供公共方法：

```python
async def _check_and_request_approval(
    self,
    tool_name: str,
    tool_args: dict,
    cfg: BaseNodeConfig,
    content_for_pattern: str,
    node_key: str,
) -> Optional[str]:
    """返回 None = 未触发；'approved' = 放行；'rejected'/'timeout' = 拒绝。"""
```

- 双模式分流：Agent（`_session_id > 0`）与 Flow（`_execution_id > 0`）均走 SSE future 等待（队列 key 分别为 session_id / execution_id；Flow 另有整批合并，见「双模式分流」节）
- `content_for_pattern` 为空时短路跳过正则
- `_ensure_patterns_compiled`：用 `tuple(patterns)` 内容做 cache key；非法正则单条跳过 + `logger.warning`
- 各 handler 在自己的执行入口（run / execute）调一次；直执行节点（python/api 主干）用 `_check_direct_execution_approval`（内部先 `_resolve_context` 解析运行域）
- **新增运行域维度时**（如本次 `_execution_id`），所有绕过 `_resolve_context` 手工构造 handler 的路径（MCP provider / 延迟域）必须同步透传——否则该域审批静默放行（P0 安全后门）

## 排查清单

- [ ] 审批没触发（Agent）→ 确认 `_session_id > 0` 且 `_writer` 非空
- [ ] 审批没触发（Flow）→ 确认 thread_id 为 `flow_{id}` 格式；MCP 工具确认 provider 构建收到 execution_id
- [ ] 直执行节点（python/api 主干）没触发 → 确认配置在节点 base_config 上（`approval_required_tools` 填 `{node_type}_executor_{node_key}`）
- [ ] 工具名不匹配 → 用 connected-tools 复制真实名称，别用 `node_key`
- [ ] 正则没命中 → 检查 `content_for_pattern` 是什么（python 是 code，api 是 method+url+body 前 200 字）
- [ ] Agent/Flow 模式 60 分钟超时被拒 → 拉长审批等待或拆解为更小的命令（future 等待统一超时）
- [ ] 「批准并记住」没生效 → 确认同域（scope）同工具；Flow 授权仅本执行内有效，新执行需重新批准
- [ ] MCP 下拉为空 → 检查服务器在线状态；DB 持久缓存应兜底（仅全新服务器首次需建连，稍候重试）；仍不行用 `allow-create` 手填
- [ ] Python preset 工具名前缀漏写 → 是 `python_executor_xxx` 不是 `xxx`
- [ ] 老 flow 没配审批字段 → `approval_required_tools` / `approval_required_patterns` 默认空，行为不变，不报错
