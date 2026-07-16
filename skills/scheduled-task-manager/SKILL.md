---
name: scheduled-task-manager
description: |
  创建、管理、触发和查询定时任务(Cron/Scheduled Task)。适用场景：
  (1) 用户要求创建定时任务，按 Cron 表达式周期执行流程或智能体
  (2) 用户要求在指定时间只执行一次流程或智能体（schedule_type=once）
  (3) 用户想查看、修改、启用/禁用或删除已有的定时任务
  (4) 用户需要手动触发定时任务立即执行
  (5) 用户想查看定时任务的执行日志和历史记录

  触发词：「创建定时任务」「管理定时任务」「cron调度」「周期执行」「单次执行」「指定时间执行」「执行一次」「手动触发」「定时任务日志」「定时任务」
---

# Scheduled Task Manager

服务器：`http://127.0.0.1:8000`

## 核心规则（必须遵守）

1. **任务名称全局唯一**：创建和更新时校验，不能与已有任务重名
2. **两种调度类型**（`schedule_type`）：
   - `cron`（默认）：循环执行，必须提供 `cron_expression`（5 字段）
   - `once`：在指定时间执行一次，必须提供 `run_at`（未来时间，格式 `YYYY-MM-DD HH:MM:SS`），到达时间后触发执行，**执行完毕自动禁用**（`is_enabled` 置 0）
3. **Cron 5 字段**（仅 `schedule_type=cron`）：格式为 `分 时 日 月 周`，无秒字段。支持 `*`、`/`、`-`、`,`、`?`（`?` 自动转为 `*`）
4. **目标 Flow 不能含 human 节点**：创建、更新、启用、手动触发四个时机均校验，包含 human 节点则拒绝
5. **启用后才注册到调度器**：`is_enabled=0` 时任务不执行，需 `toggle` 启用
6. **并发控制**：`max_instances=1`，同一任务上次未完成时跳过本次触发
7. **所有接口需登录态**：`/api/scheduled-task/*` 全部需要 session cookie 认证
8. **更新使用 `exclude_unset`**：未传字段保持不变，无法将字段更新为 `None`

## API 速查

| 方法 | 路径 | 用途 |
|------|------|------|
| POST | `/api/scheduled-task/page` | 分页列表 |
| GET | `/api/scheduled-task/get/{id}` | 详情 |
| POST | `/api/scheduled-task/create` | 创建 |
| POST | `/api/scheduled-task/update` | 更新 |
| GET | `/api/scheduled-task/delete/{id}` | 软删除 |
| POST | `/api/scheduled-task/toggle/{task_id}` | 启停切换 |
| POST | `/api/scheduled-task/trigger/{task_id}` | 手动触发 |
| POST | `/api/scheduled-task/logs/page` | 日志查询 |

## 创建定时任务流程

```
1. POST /api/scheduled-task/create    # 创建（is_enabled=0，未注册到调度器）
2. POST /api/scheduled-task/toggle/{id}  # 启用（注册到调度器，写入 next_run_time）
3. 等待调度触发（cron 周期 / once 到点） 或 POST /api/scheduled-task/trigger/{id} 手动触发
4. POST /api/scheduled-task/logs/page    # 查询执行日志
```

### 创建示例（循环执行）

```json
POST /api/scheduled-task/create
{
  "name": "每日数据汇总",
  "schedule_type": "cron",
  "cron_expression": "0 8 * * *",
  "target_type": "flow",
  "target_id": 1,
  "input_data": {
    "message": "请汇总昨日数据"
  },
  "is_enabled": 0
}
```

### 创建示例（执行一次）

```json
POST /api/scheduled-task/create
{
  "name": "月末一次性报表",
  "schedule_type": "once",
  "run_at": "2026-07-31 23:50:00",
  "target_type": "flow",
  "target_id": 1,
  "input_data": {
    "message": "生成本月报表"
  },
  "is_enabled": 1
}
```

> `once` 任务启用后到达 `run_at` 自动执行一次，执行完毕自动禁用。若服务重启时已过 `run_at` 但在 24 小时内，启动时会立即补执行。

### 启用

```json
POST /api/scheduled-task/toggle/1
```

无请求体，切换 `is_enabled` 状态（0→1 或 1→0）。启用时自动注册到 APScheduler 调度器。

### 手动触发

```json
POST /api/scheduled-task/trigger/1
```

立即执行一次（不受 `is_enabled` 影响），后台异步执行，立即返回日志记录。

## Cron 表达式

5 字段标准格式：`分 时 日 月 周`

| 字段 | 位置 | 范围 | 说明 |
|------|:----:|:----:|------|
| minute | 1 | 0-59 | 分钟 |
| hour | 2 | 0-23 | 小时 |
| day | 3 | 1-31 | 日期 |
| month | 4 | 1-12 | 月份 |
| day_of_week | 5 | 0-6 | 星期（0=周日） |

### 特殊字符

| 字符 | 含义 | 示例 |
|------|------|------|
| `*` | 任意 | `* * * * *` 每分钟 |
| `/` | 步长 | `*/5 * * * *` 每5分钟 |
| `-` | 范围 | `0 9-17 * * *` 9点到17点每小时 |
| `,` | 列表 | `0 8,12,18 * * *` 每天8/12/18点 |
| `?` | 不指定（自动转 `*`） | 兼容 Quartz 风格 |

### 预设

| 场景 | Cron |
|------|------|
| 每分钟 | `* * * * *` |
| 每小时整点 | `0 * * * *` |
| 每天 8 点 | `0 8 * * *` |
| 每天 0 点 | `0 0 * * *` |
| 每周一 9 点 | `0 9 * * 1` |
| 每月 1 号 | `0 0 1 * *` |

## 目标类型差异

| | Flow | Agent |
|--|------|-------|
| 执行方式 | `flow_executor_service.execute_stream` | 创建临时会话 → `chat_stream` |
| 会话标题 | 无（走 FlowExecution） | `[定时任务] {task_name}` |
| 关联记录 | `execution_id` → `flow_execution.id` | `session_id` → `agent_session.id` |
| 消息前缀 | 无 | `[定时任务，触发时间: YYYY-MM-DD HH:MM:SS (UTC)]` |
| human 节点 | 创建/启用/触发时校验，运行时自动取消并禁用任务 | 同上 |

## 执行日志

每次执行（定时触发或手动触发）生成一条 `ScheduledTaskLog` 记录：

| 字段 | 说明 |
|------|------|
| `status` | 0=运行中，1=成功，2=失败 |
| `trigger_type` | 1=定时触发，2=手动触发 |
| `duration_ms` | 执行耗时（毫秒） |
| `error_message` | 失败时的错误信息 |
| `execution_id` | Flow 目标时关联的执行记录 ID |
| `session_id` | Agent 目标时关联的会话 ID |

### 查询日志

```json
POST /api/scheduled-task/logs/page
{
  "page": 1,
  "page_size": 20,
  "condition": {
    "task_id": 1
  }
}
```

`task_id` 必填。

## 完整接口详情

见 [references/api.md](references/api.md)。
