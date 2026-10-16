# 判断回溯录（ERR-XXX，原「踩坑错误册」）

> 任务结束后触发"新坑即录"：凡本轮新踩/复现且解决耗时 ≥ 40min 的坑，强制记录到本系统记忆工具。

本 reference 收口于 Step 6 复盘沉淀阶段。

**定位升级**：本册不只记"坑的结果"（根因/修复/防范）来省时间，更要把每一条变成**决策失败样本**——记录"当时基于什么证据判错、正确判据应是什么、为什么没想到那个信号"，让诊断从"查关键词命中"升级为"见过这种错判长什么样"的模式匹配。

**v2 重构说明（2026-09-21）**：原 `{PROJECT_ROOT}/.ai-memory/error_index.md` 索引文件 + `errors/ERR-XXX.md` 单条文件全部废弃，改用本系统记忆工具。检索靠 `memory_search`，写入靠 `memory_save`，升级/合并靠 `memory_delete` + `memory_save` 替代。

---

## 单条模板（替代 errors/ERR-XXX.md）

调用 `memory_save`：

```python
memory_save(
    title="ERR-XXX: {错误一句话描述}",
    content="""# ERR-XXX: {错误一句话描述}

- **根因分类**: [十类之一]
- **触发条件**: 
- **排查路径**: 
- **错判点**: （当时基于什么证据判错了——误信了哪个信号、忽略了哪个反例）
- **正确判据**: （应该用什么信号才对——可证伪的判定标准）
- **盲区**: （为什么之前没想到这个信号——认知/经验缺口，而非技术盲区）
- **修复方式（可复用）**: 
- **防范措施**: 
- **规避方案**: 
- **关联知识库**: （列出相关技术或工具的官方文档关键词 / sector）
- **来源会话**: （可选，标记首次发生的会话 ID 或日期）
- **Recurrence-Count**: 1（复现次数）
- **First-Seen**: （YYYY-MM-DD，首次发生日期）
- **Last-Seen**: （YYYY-MM-DD，最近复现日期）
- **Tasks**: 1（跨不同任务数，Recurrence>=3 且 Tasks>=2 触发 promote 建议）
- **Pattern-Key**: （可选，稳定去重键，如 cms.routing.table_loaded）
""",
    category="knowledge",
    importance=5,
    tier="hot",
    keywords=[
        "err-ledger",
        "err-id:ERR-XXX",
        "category:{根因分类}",  # 框架行为假设/平台差异/配置冲突/缓存竞态/工具行为/业务逻辑/安全沙箱/环境变量/算法正确性/设计取舍
        "project:{PROJECT_ROOT}",  # 项目隔离
    ]
)
```

**分类标准（共十类）**：框架行为假设 / 平台差异 / 配置冲突 / 缓存/竞态 / 工具行为 / 业务逻辑 / 安全沙箱 / 环境变量 / **算法正确性** / **设计取舍(taste)**。

- **算法正确性**：环检测 / 图遍历 / 递归 / 状态机 / 排序等"跑通但结果错"类缺陷（如环检测三次没写对）。此前此类坑进不了册子，属分类缺口，必须单独归类。
- **设计取舍(taste)**：取舍权衡判断失误（过度设计 vs 简化、抽象边界、API 形态、是否引入依赖等），属审美/判断力层面而非纯技术错误。

> **Recurrence 字段语义**：单条 ERR 记录的 Recurrence-Count / Tasks 由本系统记忆搜索计数代理——`memory_search(query=ERR-ID)` 返回的关联条目数 / 跨会话数即对应指标，无需手写。如需精确统计可在 `content` 中保留字段供人工查阅。

禁止字段：本模板仅含技术信息。禁止记录密码 / Token / API Key / SSH Key / 账号凭证 / 生产 IP / 内部域名。

---

## 索引检索（替代 error_index.md 全文扫描）

### 诊断阶段触发

在 Bug 诊断 / 代码审查 / PHP 内联 JS 等 session 中，Agent 优先按**错判形状**做模式匹配，而非只查触发关键词：

```python
# 1. 按触发关键词或根因分类检索
memory_search(
    query="{触发关键词 或 根因分类}",
    tier="hot",
    categories=["knowledge"],
    max_results=10
)
# 2. 再按标题前缀 "ERR-" 过滤（memory_search 不支持标题前缀过滤，在结果中筛选）

# 3. 若当前症状与某条 ERR 命中，重点比对单条中的 错判点 / 正确判据 / 盲区
#    调用 memory_get(memory_ids=[命中条目的 ID]) 读取全文
```

- 命中后读单条全文（`memory_get`），重点比对 **错判点 / 正确判据 / 盲区**
- 若当前症状与记录匹配 → 优先复用修复方式与正确判据，并对本结论先跑证伪门禁，计时从 0 开始
- 若相似但不完全匹配 → 标记为变体，修复后新建 ERR-ID（不覆盖原条目），并在新条目的错判点中说明与旧条目的差异

### 新坑即录

任一 session 中踩的新坑且排查耗时超过 40 分钟（从初步定位到 fix 完成），强制性操作：

1. 分配下一个可用的 ERR-ID（如 ERR-007）
2. 按单条模板调用 `memory_save(title="ERR-007: ...", category="knowledge", importance=5, tier="hot", keywords=["err-ledger", "err-id:ERR-007", "category:{分类}", "project:{PROJECT_ROOT}"])`
3. 升级检查：检索最近 30 天内同 `err-id` 或同 `Pattern-Key` 的记忆，若 `Recurrence-Count ≥ 3` 且 `Tasks ≥ 2` → 提示用户是否升级为全局 `lesson`（`memory_save(category="lesson", importance=5, tier="hot", keywords=["promoted:true", ...])`），并 `memory_delete` 旧 ERR 条目
4. 同步检查：是否需要写一条 `lesson` 类记忆沉淀根因（见 `project-memory-management.md` 第五步）

> **排查时间估算**：从首次发现症状到修复方案确认，按 Agent 与用户交互轮次×平均轮耗时估算；≥ 4 轮交互视为接近 40 分钟阈值，建议记录。

---

## 更新现有 ERR 条目（替代手动编辑 errors/ERR-XXX.md）

更新 Recurrence-Count 或 Last-Seen 时：

```python
# 1. 取旧条目详情
old = memory_get(memory_ids=[ERR条目的ID])

# 2. 修改 content（更新 Recurrence-Count / Last-Seen 等）

# 3. 删除旧条目 + 保存新版本（确保 keywords 含 err-id 便于检索去重）
memory_delete(memory_ids=[ERR条目的ID])
memory_save(title=新标题, content=新内容, category="knowledge", importance=5, tier="hot", keywords=原 keywords)
```

**为什么不直接更新**：本系统记忆工具暂不支持 in-place 更新，删除+重存是当前唯一保证审计链完整的方案。如未来工具支持 update，再切回原条目 update 模式。

---

## 归档策略

- **索引大小监控**：`memory_search(query="err-ledger", tier="hot", categories=["knowledge"])` 返回结果 > 50 条时触发归档提醒
- **单条 ERR 超过 500 行**：触发拆分提醒
- **合并规则**：连续 3 个 ERR-ID 同一根因 → 合并为一条（`memory_delete` 旧 3 条 + `memory_save` 新合并条），保留各变体修复方式

---

## 禁忌

- 不记录密码 / Token / API Key / 凭据 / 生产 IP
- 不经确认不写入（首次记录时 `memory_save` 一次性写完，不分步确认）
- 不跨项目复用（每项目独立索引，靠 `keywords=["project:{PROJECT_ROOT}"]` 隔离）
- 不用旧版 `error_index.md` / `errors/ERR-XXX.md` 文件路径（已废弃）