# 项目记忆管理 — 基于本系统记忆工具的跨会话沉淀

> **v2 重构说明（2026-09-21）**：原 `.ai-memory/` 文件树（`project_memory.md` / `handoff.md` / `daily.md` / `topics.md` / `session_memory_*.jsonl` / `user_profile.md`）整体废弃，全部改用本系统内置的记忆工具集（`memory_save` / `memory_search` / `memory_list` / `memory_get` / `memory_delete`）。理由：
>
> 1. 文件体系需要手动管目录、路径探测、UTF-8 编码、跨 IDE 兼容、gitignore 策略，运维成本高；
> 2. 本系统记忆工具自带向量检索 + 三层分层（hot/warm/cold），无需自建索引；
> 3. 同一工具跨项目、跨会话、跨 IDE 通用，无需 `{PROJECT_ROOT}` / `{USER_PROFILE}` 探测协议。
>
> **唯一例外**：依赖影响面分析（`references/project-knowledge-graph.md`）是纯方法论，用 `file_search` + `file_read` 现场推导，不落任何持久化文件。本 reference 不涉及影响面分析。

## 目录

- [输入要求](#输入要求)
- [记忆分层映射（原 .ai-memory 文件 → 新 memory 工具）](#记忆分层映射原-ai-memory-文件--新-memory-工具)
- [执行流程（按记忆类型分步）](#执行流程按记忆类型分步)
- [记忆治理原则](#记忆治理原则)
- [读取截断规则](#读取截断规则)
- [与其他 reference 的对接](#与其他-reference-的对接)
- [输出格式](#输出格式)
- [质量标准](#质量标准)
- [失败回退机制](#失败回退机制)
- [关联 reference](#关联-reference)

---

## 输入要求

1. **记录类型**（必填）：session总结 / 决策记录 / 规范沉淀 / 已知问题清单 / 换机交接 / 记忆恢复 / 写后即记 / 主题摘要
2. **内容详情**（必填）：具体要记录的信息
3. **项目标识**（可选，建议）：项目名称或路径关键词，写入 `keywords` 便于检索

---

## 记忆分层映射（原 .ai-memory 文件 → 新 memory 工具）

| 原文件（已废弃） | 原层级 | 新工具调用 | category | importance | tier | 典型标题前缀 |
|----------------|--------|-----------|----------|------------|------|--------------|
| `{USER_PROFILE}/.ai-memory/user_profile.md` | L4 用户级 | `memory_save` | `preference` | 5 | hot | （无） |
| `{PROJECT_ROOT}/.ai-memory/project_memory.md` Decision Record | L3 项目决策 | `memory_save` | `decision` | 5 | hot | `决策：` |
| `{PROJECT_ROOT}/.ai-memory/project_memory.md` Project Convention | L3 项目规范 | `memory_save` | `knowledge` | 4 | warm | `规范：` |
| `{PROJECT_ROOT}/.ai-memory/project_memory.md` Known Issues / Lessons Learned | L3 项目教训 | `memory_save` | `lesson` | 4 | warm | `教训：` / `已知问题：` |
| `{PROJECT_ROOT}/.ai-memory/project_memory.md` Glossary | L3 项目术语 | `memory_save` | `knowledge` | 4 | warm | `术语：` |
| `{PROJECT_ROOT}/.ai-memory/handoff.md` | L3 交接级 | `memory_save` | `task` | 5 | hot | `Handoff：` |
| `{PROJECT_ROOT}/.ai-memory/{YYYYMMDD}/session_memory_{id}.jsonl` | L2 会话级 | `memory_save` | `task` | 4 | warm | `Session：` |
| `{PROJECT_ROOT}/.ai-memory/{YYYYMMDD}/daily.md`（写后即记协议） | L1 工作级 | `memory_save` | `event` | 2-3 | cold/warm | `[HH:mm] [类型]` |
| `{PROJECT_ROOT}/.ai-memory/{YYYYMMDD}/topics.md` | L1 对话级 | `memory_save` | `event` | 2 | cold | `Topics：` |
| `.ai-memory/error_index.md` | Error 索引 | `memory_search` + `memory_save` | `knowledge` | 5 | hot | `ERR-XXX: ...` |
| `.ai-memory/errors/ERR-XXX.md` | Error 单条 | `memory_save` | `knowledge` | 5 | hot | `ERR-XXX: ...` |
| `.ai-memory/_handoff_snapshot.md`（PreCompact） | 压缩快照 | 取消，改用 `memory_search` 实时取当前活跃任务 | — | — | — | — |

**关键设计要点**：

- `importance` 1-5 控制显示优先级与 tier（5=hot, 3-4=warm, 1-2=cold），命中即自动升温
- `keywords` 是项目隔离的关键字段，**必须填项目标识**（如 `["project: D:/my-proj", "tech: Laravel"]`）便于 `memory_search` 过滤
- 同一项目多条记录共享 keywords 前缀，构成"虚拟项目记忆库"
- 取消 `{YYYYMMDD}` 目录结构：旧版按日分目录是为了文件粒度管理，新版用 `importance` + `keywords` 自然分层

---

## 执行流程（按记忆类型分步）

### 第一步：写后即记协议（替代原 daily.md）

**触发条件**：实质性工作（Bug 修复 / 功能实现 / 代码审查 / 重构 / 技术选型 / 配置变更 / 文档生成 / 新约定）完成后，立即调用 `memory_save`，**不经用户确认、不等待会话结束**。

**调用格式**：

```python
memory_save(
    title="[HH:mm] [动作类型]: [一句话摘要]",  # 例："[14:35] Bug修复: 修复Token过期导致401未重试"
    content="""## [HH:mm] - [动作类型]: [一句话摘要]

- **项目**: {project_keyword}  # 例：D:/my-project
- **文件**: [修改的文件列表]
- **决策**: [如有]
- **验证**: [验证方式 + 结果]
- **会话ID**: [当前会话标识,可选]
""",
    category="event",
    importance=2,  # 默认 2（cold）；重大决策/未完成项 = 3（warm）；事故级 = 4
    tier="cold",
    keywords=["project:D:/my-project", "tech:Laravel", "phase:fix"]  # 项目隔离 + 阶段标签
)
```

**精简规则**（替代原 8000 字符阈值）：

- 当日写后即记条目 > 30 条时，提示用户：是否将关键决策/教训类条目升级为 `importance=4-5` 的 `decision`/`lesson` 记录
- > 50 条时，主动把纯流水条目改为 `memory_delete` 删除（保留 importance≥3 的条目）
- `importance=1` 的条目自动落入 cold 层，可通过 `memory_search` 检索但不主动加载

### 第二步：会话结束记录（Session Summary，替代原 session_memory.jsonl）

**调用时机**：每次会话结束 / 阶段交付完成 / 用户主动说"结束当前会话"时。

```python
memory_save(
    title="Session: {简短目标} ({YYYY-MM-DD})",  # 例："Session: 重构鉴权模块 (2026-09-21)"
    content="""## Session Summary

**日期**: {YYYY-MM-DD}
**会话目标**: [本次会话原计划完成什么]

### 已完成
- [任务1]: [简要说明] → [相关文件]
- [任务2]: [简要说明] → [相关文件]

### 关键决策
- **决策**: [决策内容]
  - **上下文**: [为什么做这个决策]
  - **影响**: [影响哪些后续工作]

### 遗留问题 / Blockers
- [问题1]: [描述] → [下一步行动]

### 下一步计划
1. [下一步任务]
2. [下一步任务]
""",
    category="task",
    importance=4,
    tier="warm",
    keywords=["project:D:/my-project", "session:2026-09-21", "phase:{当前阶段}"]
)
```

### 第三步：决策记录（Decision Record）

**调用时机**：关键技术决策拍板后（如技术选型、架构调整、库替换）。

```python
memory_save(
    title="决策: {决策名}",
    content="""## Decision Record: {决策名}

**日期**: {YYYY-MM-DD}
**问题**: [需要解决什么问题]

### 选项分析
| 选项 | 优势 | 劣势 | 复杂度 |
|------|------|------|--------|
| [选项A] | ... | ... | 低/中/高 |
| [选项B] | ... | ... | 低/中/高 |

### 决策
**选择**: [最终选项]
**理由**: [为什么选这个]
**Trade-offs**: [接受了什么代价]

### 影响范围
- [影响的模块/文件]
- [需要同步更新的文档]

### 撤销条件
[什么情况下可以重新考虑这个决策]
""",
    category="decision",
    importance=5,
    tier="hot",
    keywords=["project:D:/my-project", "decision:{决策标识}"]  # decision 标识用于检索去重
)
```

### 第四步：规范沉淀（Convention Capture）

**调用时机**：发现项目特定规范（编码风格、命名约定、架构约束）。

```python
memory_save(
    title="规范: {规范名}",
    content="""## Project Convention: {规范名}

**类型**: [编码规范/命名规范/架构约束/工具配置]
**适用范围**: [哪些模块/文件适用]

### 规范内容
[具体规则]

### 示例
**正确**:
`[代码示例]`

**错误**:
`[代码示例]`

### 发现来源
[哪次会话/哪个任务中发现]
""",
    category="knowledge",
    importance=4,
    tier="warm",
    keywords=["project:D:/my-project", "convention:{类型}"]  # 类型: coding/naming/arch/config
)
```

### 第五步：已知问题清单（Known Issues / Lessons Learned，替代原 daily.md 蒸馏）

```python
memory_save(
    title="已知问题: {问题名}",
    content="""## Known Issue: {问题名}

**发现日期**: {YYYY-MM-DD}
**问题类型**: [Bug模式 / 兼容性陷阱 / 依赖风险 / 性能反模式 / 安全漏洞]
**严重度**: [致命 / 严重 / 一般 / 提示]

### 现象
[具体表现，含错误信息或日志片段]

### 根因
[根本原因分析]

### 规避方案
[具体的代码层面规避措施]

### 相关文件
- [文件路径:行号]

### 状态
[未修复 / 已修复 / 已规避 / 待验证]
""",
    category="lesson",
    importance=4,
    tier="warm",
    keywords=["project:D:/my-project", "issue:{类型}"]  # 类型: bug-pattern/compat/dep/perf/security
)
```

### 第六步：术语表维护（Glossary）

```python
memory_save(
    title="术语: {术语名}",
    content="""## Glossary Entry: {术语名}

**规范名称**: [唯一推荐名称]
**别名/变体**: [同义但不推荐的名称列表]
**定义**: [一句话定义]
**适用范围**: [哪些模块/文件使用此术语]
**禁止使用**: [明确不应使用的名称]
**来源**: [需求文档/用户口头约定/行业标准]
""",
    category="knowledge",
    importance=4,
    tier="warm",
    keywords=["project:D:/my-project", "glossary", "term:{术语名}"]
)
```

### 第七步：换机/换 IDE 交接（Handoff Protocol，替代原 handoff.md）

**触发条件**：

- 用户明确说"暂停 / 下次继续 / 换电脑 / 关闭 IDE / 稍后接着做"
- 本轮存在未完成主线任务，且已产生文件修改或关键决策
- 任务依赖本地环境状态、运行命令、验证结果或人工确认
- 多 IDE 协同开发时，任何会影响下一位执行者的上下文变化

```python
memory_save(
    title="Handoff: {当前目标} ({YYYY-MM-DD HH:mm})",
    content="""## Handoff Checkpoint

**更新时间**: {YYYY-MM-DD HH:mm}
**当前目标**: [用户原始需求的核心交付物]
**当前阶段**: [需求/设计/实现/验证/交付]
**完成度**: [按已验证主线数计算的百分比]

### 已完成
- [已完成项]: [验证证据] -> [相关文件]

### 未完成
- [未完成项]: [下一步动作] -> [相关文件]

### 关键决策
- [决策]: [理由 / 影响范围 / 撤销条件]

### 恢复入口
- **首读文件**: [恢复时优先读取的文件]
- **关键命令**: [lint/test/build/启动命令]
- **验证路径**: [继续前必须跑的验证]

### 阻塞项
- [阻塞原因]: 需 [角色] 提供 [具体输入]
""",
    category="task",
    importance=5,
    tier="hot",
    keywords=["project:D:/my-project", "handoff", "active:true"]  # active:true 标记当前活跃交接
)
```

**会话恢复时定位当前交接**：

```python
# 新会话开始时，先取当前活跃交接
memory_search(
    query="handoff active",
    tier="hot",
    categories=["task"],
    keywords=["project:D:/my-project"],  # 实际通过 keywords 参数过滤
    max_results=1
)
```

### 第八步：新会话记忆恢复（替代三层加载策略）

新会话开始时，按 L1 → L2 → L3 顺序加载记忆（用本系统工具替代原文件读取）：

#### L1 元数据加载（~50 tokens，hot 层）

```python
# 取当前项目的所有 hot 记忆概览
memory_list(
    tier="hot",
    category=None,  # 不限，看全貌
    limit=10
)
# 重点筛选 keywords 含 "project:D:/my-project" 的条目
```

**输出格式**（在 Step 1.1 引用）：

```
项目: [从 keywords 解析]
当前阶段: [从最近 Session Summary 解析]
活跃任务: [从 Handoff 解析]
上次会话: [从最近 Session Summary 解析]
交接状态: [存在 Handoff 记录 / 无]
```

#### L2 主体加载（~200 tokens，warm 层）

```python
# 取最近一次会话总结 + 当前活跃交接
memory_search(
    query="Session Summary {昨日日期}",  # 或 "上次会话"
    tier="warm",
    categories=["task"],
    max_results=2
)
```

**输出格式**：

```markdown
## 上次Session Summary
[最近一次的summary核心内容]

## Handoff Checkpoint
[当前活跃交接的未完成项]
```

#### L3 参考加载（按需，~500 tokens each）

```python
# 按当前任务相关决策/规范按需加载
memory_search(
    query="{当前任务关键词}",
    tier=["hot", "warm"],
    categories=["decision", "knowledge", "lesson"],
    max_results=5
)
```

**加载策略**：

1. 始终加载 L1
2. 如果存在活跃 Handoff，优先加载 Handoff 内容
3. 如果当前任务与上次会话连续，加载 L2
4. 如果涉及历史决策或规范，按需加载 L3
5. 总 token 预算控制在 1000 以内，超出时丢弃 L3

> **加载失败处理**：memory_search 返回空结果时正常进入意图识别，不阻塞。

### 第九步：错误索引（Error Ledger，替代 error_index.md + errors/ERR-XXX.md）

**新坑即录**：

```python
# 单条错误记录（替代 errors/ERR-XXX.md）
memory_save(
    title="ERR-XXX: {错误一句话描述}",
    content="""## ERR-XXX: {错误一句话描述}

**根因分类**: [十类之一: 框架行为假设/平台差异/配置冲突/缓存竞态/工具行为/业务逻辑/安全沙箱/环境变量/算法正确性/设计取舍]
**触发条件**: 
**排查路径**: 
**错判点**: （当时基于什么证据判错了）
**正确判据**: （应该用什么信号才对）
**盲区**: （为什么之前没想到这个信号）
**修复方式（可复用）**: 
**防范措施**: 
**Recurrence-Count**: 1
**First-Seen**: {YYYY-MM-DD}
**Last-Seen**: {YYYY-MM-DD}
**Tasks**: 1
""",
    category="knowledge",
    importance=5,
    tier="hot",
    keywords=["project:D:/my-project", "err-ledger", "err-id:ERR-XXX", "category:{根因分类}"]
)
```

**索引检索（替代 error_index.md 全文扫描）**：

```python
# 诊断阶段查现有错误（按触发关键词 / 根因分类 / 错判形状）
memory_search(
    query="{触发关键词 或 根因分类}",
    tier="hot",
    categories=["knowledge"],
    max_results=10
)
# 再按标题前缀 "ERR-" 过滤

# 升级 Recurrence-Count：找到条目后调用 memory_get 读取，再 memory_delete 旧条目 + memory_save 新值
```

**升级规则触发**（替代 recurrence_promote.py）：每次新增或更新 ERR-XXX 时检查：

- Recurrence-Count ≥ 3 且 Tasks ≥ 2 且 30 天窗口 → 提示用户是否升级为全局 lesson（category=lesson, importance=5, keywords 加 `promoted:true`）
- 用户确认后 memory_delete 旧 ERR-XXX 条目 + memory_save 新 lesson

### 第十步：用户偏好（User Profile，替代 user_profile.md）

```python
memory_save(
    title="{偏好名}",
    content="""## User Preference: {偏好名}

**类型**: [技术栈 / 编码风格 / 沟通偏好 / 禁忌]
**适用范围**: [跨项目 / 指定项目]

### 内容
[具体偏好]

### 发现来源
[哪次会话/用户明确说明]
""",
    category="preference",
    importance=5,
    tier="hot",
    keywords=["user-profile", "type:{类型}", "scope:{cross-project|project-specific}"]
)
```

---

## 记忆治理原则

### 优先级规则

1. **L4 用户偏好 > L3 项目约束 > L2/L1 操作记录**：冲突时优先更具体、更高风险的约束
2. **未验证信息只留在 L1**（importance ≤ 2, category=event），不得沉淀为 L3（decision/knowledge/lesson）
3. **敏感信息（健康/财务/身份/凭据/密钥）不得记录**。开发场景例外：可记"位置与访问方式"，不记"值"

### 静默应用 vs 显式说明

- **静默应用**：用户长期偏好（preference）、项目固定路径（decision）、已验证技术约束（knowledge）、上轮已确认的下一步（task）
- **显式说明**：记忆与当前请求冲突、可能过期、会影响文件修改或交付判断

### 记忆膨胀治理

- `memory_list(tier="cold", limit=100)` 扫描冷记忆，超过 100 条时建议清理
- 清理规则：纯操作流水直接 `memory_delete`；关键决策/教训升级为 `decision`/`lesson`
- 清理动作必须经用户确认

### 记忆冲突处理

- 多个互斥规范时按时间 + 证据强度 + 风险等级裁决
- 旧规则用 `memory_delete` 删除（不要覆盖，新规则覆盖旧规则会让审计链断裂）
- 必要时保留旧条目，标题加 `[过期]` 前缀

---

## 读取截断规则

| 场景 | 阈值 | 读取动作 |
|------|------|---------|
| `memory_list(tier="hot")` | ≤ 20 条 | 全量返回 |
| `memory_list(tier="hot")` | > 20 条 | 取最近访问 + 项目相关过滤 |
| `memory_search` 单次 | max_results ≤ 5 | 直接全量 |
| `memory_search` 单次 | max_results > 5 | 减少或分批 |

新会话恢复的截断优先级：L1 元数据 > L2 session/handoff > L3 决策/规范，总 token > 1000 时优先丢弃 L3。

---

## 与其他 reference 的对接

| Reference | 对接点 |
|-----------|--------|
| `references/execution-safety.md` | session-id 机制已废弃，新会话靠 `memory_list(tier="hot")` 自动恢复 |
| `references/dev-navigation.md` | 阶段扫描不再读 `.ai-memory/`，改用 `memory_search` 按阶段关键词取最近记录 |
| `references/error-ledger.md` | ERR-XXX 改用 `memory_save(category="knowledge")` + keywords `err-ledger` |
| `references/root-cause-debugging.md` | 沉淀步骤改为 `memory_save(category="event", importance=2)` |
| `references/project-knowledge-graph.md` | **不涉及记忆体系**（已改为纯方法论：用 `file_search` 推导依赖 + `file_read` 复核，不落任何持久化文件） |

---

## 输出格式

各记忆类型的模板已在执行流程(第一至十步)中定义。**所有调用均通过 `memory_save` 工具完成，不再有文件路径、目录结构、UTF-8 编码、gitignore 策略等文件级运维**。

调用前确认 `category` 与 `importance` 正确（这两项决定后续检索效果），`keywords` 必填项目标识。

---

## 质量标准

- Session Summary 含会话目标/已完成/关键决策/遗留问题/下一步
- Decision Record 必须含"撤销条件"
- Convention Capture 必须有正/反例
- Handoff Checkpoint 必须能让新会话在不依赖旧上下文的情况下恢复当前主线
- 记忆恢复总 token ≤ 1000
- 敏感记忆上下文隔离，可记位置不记值
- 所有记忆类型必填 `keywords` 含项目标识，便于检索隔离

---

## 失败回退机制

| 步骤 | 失败条件 | 回退目标 | 最大重试 |
|------|---------|---------|---------|
| `memory_save` 失败 | 工具调用异常 | 重试一次，仍失败则降级为"输出内容让用户手动保存" | 1 |
| `memory_search` 超时 | 召回 0 条 | 放宽关键词重试，仍为空则正常进入意图识别 | 2 |
| `memory_list` 失败 | tier 加载异常 | 降级为 `memory_search(query="最近记忆", tier=["hot","warm"])` | 1 |
| 记忆冲突 | 多个互斥规范 | 按时间/证据强度裁决，旧规则用 `memory_delete` | 1 |
| 敏感信息污染 | 写入敏感值 | 立即 `memory_delete` 并通知用户 | 0 |

---

## 关联 reference

- **spec-driven-development** — 将 spec 和 design 决策记录为 `decision` 类记忆
- **software-project** — 通用项目边界、架构、发布记录为 `decision`/`knowledge`
- **website-project** — 站点配置、部署路径记录为 `knowledge`
- **task-decomposition-and-execution** — task summary 记录为 `task` 类记忆
- **tech-selection** — 选型决策记录为 `decision`
- **code-generation** — 编码前加载 `knowledge` 类项目规范，编码后写后即记