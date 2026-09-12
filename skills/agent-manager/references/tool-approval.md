# 工具审批（Tool Approval）

Agent 模式下，工具节点在执行危险操作前可触发用户审批。本文档说明：
1. 哪些节点支持审批、字段语义
2. 各节点运行时真实工具名格式（前端 connected-tools 接口拿到的 `name`）
3. 审批触发的判定流程与正则匹配 4 层设计
4. 前后端实现要点（基类、方案 C 工具名下拉）

恢复接口与 SSE 事件已分别在 [API 参考](api.md#sse-事件) 和 [子 Agent](sub-agent.md#工具审批) 描述，本文不重复。

## 节点覆盖范围

只有 **LLM 可调用的工具节点** 才需要审批；纯编排节点（start / end / condition / intent_router / card / loop）不触发。

| 节点 | 是否支持审批 | 备注 |
|---|---|---|
| `shell` | ✅ | 已支持，shell_handler 历史实现 |
| `ssh` | ✅ | 已支持，复刻 shell 模式 |
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

> **仅 Agent 模式生效**（`_session_id > 0 and _writer 非空`）。Workflow 模式下两个字段被忽略。

## 各节点 content_for_pattern 语义

`content_for_pattern` 是传给正则匹配的"内容字符串"。每个工具节点定义自己的语义，必须能让用户在正则里写出意图明确的模式（例如 `rm\s+-rf`、`https?://[^/]+\.internal`）。

| Handler | content_for_pattern |
|---|---|
| `shell` / `ssh` | `command`（命令全文） |
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

> Python preset 工具名的 `python_executor_` 前缀容易漏写——必须从 connected-tools 复制，不要凭记忆。

## 审批触发流程

1. **白名单检查**：工具名是否在 `approval_required_tools` 列表中
2. **正则检查**：`content_for_pattern` 非空 + 模式非空 + 任一正则命中
3. **任一命中** → 调用 `tool_approval_service.register(...)` → 推送 `tool_approval_required` SSE
4. 前端弹窗 → 用户选 `approved` / `rejected` → `POST /agent/.../tool_approval`
5. 后端 `emit(action)` 唤醒 Future → 工具按结果执行 / 返回拒绝文本

**返回语义**：

| 后端返回 | 工具表现 |
|---|---|
| `approved` | 放行，正常执行 |
| `rejected` | 返回 `{"error": "用户拒绝了工具 X 的执行"}`，LLM 收到错误后可换工具或放弃 |
| `timeout`（5 分钟） | 同 rejected |

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

### MCP 已知限制

MCP 工具列表来自 `mcp_tool_manager._tools_cache`——**server 从未测试连接过时缓存为空，下拉为空**。

- **临时方案**：`allow-create` 自由输入完整工具名
- **未来优化**：节点配置 UI 增加"刷新 MCP 工具"按钮（触发 server test connection 后重读缓存）

不在本轮范围。

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

- 仅 Agent 模式（`_session_id > 0 and _writer 非空`）生效
- `content_for_pattern` 为空时短路跳过正则
- `_ensure_patterns_compiled`：用 `tuple(patterns)` 内容做 cache key；非法正则单条跳过 + `logger.warning`
- 各 handler 在自己的执行入口（run / execute）调一次

## 排查清单

- [ ] 审批没触发 → 确认是 Agent 模式（Workflow 模式忽略）
- [ ] 工具名不匹配 → 用 connected-tools 复制真实名称，别用 `node_key`
- [ ] 正则没命中 → 检查 `content_for_pattern` 是什么（python 是 code，api 是 method+url+body 前 200 字）
- [ ] 5 分钟超时被拒 → 拉长审批等待或拆解为更小的命令
- [ ] MCP 下拉为空 → server 还没测试连接；用 `allow-create` 手填或先去 MCP 管理页测试
- [ ] Python preset 工具名前缀漏写 → 是 `python_executor_xxx` 不是 `xxx`
- [ ] 老 flow 没配审批字段 → `approval_required_tools` / `approval_required_patterns` 默认空，行为不变，不报错
