# Scheduled Task API 参考

Base URL: `http://127.0.0.1:8000` | 响应: `{code:1, msg, data}` 成功 / `{code:0, msg}` 失败

## 接口总览

| 方法 | 路径 | 说明 |
|------|------|------|
| POST | `/api/scheduled-task/page` | 分页列表 |
| GET | `/api/scheduled-task/get/{id}` | 详情 |
| POST | `/api/scheduled-task/create` | 创建 |
| POST | `/api/scheduled-task/update` | 更新 |
| GET | `/api/scheduled-task/delete/{id}` | 软删除 |
| POST | `/api/scheduled-task/toggle/{task_id}` | 启停切换 |
| POST | `/api/scheduled-task/trigger/{task_id}` | 手动触发 |
| POST | `/api/scheduled-task/logs/page` | 日志查询 |

所有接口需 session cookie 认证。

---

## 定时任务 CRUD

### POST /api/scheduled-task/create

创建定时任务（`is_enabled` 默认 0，需手动 toggle 启用）。

**请求体：**

| 字段 | 类型 | 必填 | 默认 | 说明 |
|------|------|:----:|:----:|------|
| `name` | string | ✅ | — | 任务名称（全局唯一） |
| `cron_expression` | string | ✅ | — | 5 字段 Cron 表达式 |
| `target_type` | string | ✅ | — | `flow` / `agent` |
| `target_id` | int | ❌ | — | 目标流程 ID |
| `input_data` | object | ❌ | null | 预设输入参数 |
| `is_enabled` | int | ❌ | 0 | 0=禁用，1=启用 |

**响应：**
```json
{
  "code": 1,
  "data": {
    "id": 1,
    "name": "每日数据汇总",
    "cron_expression": "0 8 * * *",
    "target_type": "flow",
    "target_id": 1,
    "input_data": {"message": "请汇总昨日数据"},
    "is_enabled": 0,
    "next_run_time": null,
    "last_run_time": null,
    "last_run_status": null,
    "create_time": "2026-06-21T08:00:00"
  }
}
```

### POST /api/scheduled-task/update

更新定时任务（`exclude_unset`，未传字段保持不变）。

**请求体：**

| 字段 | 类型 | 必填 | 说明 |
|------|------|:----:|------|
| `id` | int | ✅ | 任务 ID |
| `name` | string | ❌ | 名称 |
| `cron_expression` | string | ❌ | Cron 表达式 |
| `target_type` | string | ❌ | 目标类型 |
| `target_id` | int | ❌ | 目标 ID |
| `input_data` | object | ❌ | 输入参数 |
| `is_enabled` | int | ❌ | 启用状态 |

更新后自动重新注册到调度器（如果已启用）。

### POST /api/scheduled-task/page

分页查询。

**请求体：**
```json
{
  "page": 1,
  "page_size": 20,
  "condition": {
    "name": "每日",
    "is_enabled": null,
    "target_type": null
  }
}
```

**查询条件：**

| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 名称关键词（等值匹配，非模糊） |
| `is_enabled` | int | 精确匹配启用状态 |
| `target_type` | string | 精确匹配目标类型 |

### GET /api/scheduled-task/get/{id}

获取任务详情（同列表响应格式）。

### GET /api/scheduled-task/delete/{id}

软删除（设置 `is_delete=1`），同时从调度器移除。

---

## 启停与触发

### POST /api/scheduled-task/toggle/{task_id}

切换启用/禁用状态（`is_enabled` 1↔0）。

- 无请求体
- 启用时：校验目标 Flow 不含 human 节点 → 注册到 APScheduler → 写入 `next_run_time`
- 禁用时：从调度器移除 → `next_run_time` 置 null

**响应：**
```json
{
  "code": 1,
  "data": {
    "id": 1,
    "is_enabled": 1,
    "next_run_time": "2026-06-22T08:00:00"
  }
}
```

### POST /api/scheduled-task/trigger/{task_id}

手动触发执行（不受 `is_enabled` 影响）。

- 校验目标 Flow 不含 human 节点
- 后台异步执行，立即返回日志记录
- 创建 `ScheduledTaskLog`（trigger_type=2 手动触发）

**响应：**
```json
{
  "code": 1,
  "data": {
    "id": 10,
    "task_id": 1,
    "status": 0,
    "trigger_type": 2,
    "start_time": "2026-06-21T08:30:00",
    "input_snapshot": {"message": "请汇总昨日数据"}
  }
}
```

---

## 日志查询

### POST /api/scheduled-task/logs/page

分页查询指定任务的执行日志。

**请求体：**
```json
{
  "page": 1,
  "page_size": 20,
  "condition": {
    "task_id": 1,
    "status": null,
    "trigger_type": null
  }
}
```

`task_id` 必填。

**响应：**
```json
{
  "code": 1,
  "data": {
    "total": 5,
    "items": [
      {
        "id": 10,
        "task_id": 1,
        "execution_id": 100,
        "session_id": null,
        "agent_id": null,
        "status": 1,
        "trigger_type": 1,
        "start_time": "2026-06-21T08:00:00",
        "end_time": "2026-06-21T08:00:05",
        "duration_ms": 5000,
        "error_message": null,
        "input_snapshot": {"message": "请汇总昨日数据"},
        "create_time": "2026-06-21T08:00:00"
      }
    ]
  }
}
```

---

## ScheduledTask 字段表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键 |
| `name` | string | 任务名称（全局唯一） |
| `cron_expression` | string | 5 字段 Cron 表达式 |
| `target_type` | string | 目标类型：`flow` / `agent` |
| `target_id` | int | 目标流程 ID |
| `input_data` | object | 预设输入参数 |
| `is_enabled` | int | 0=禁用，1=启用 |
| `next_run_time` | datetime | 下次执行时间（由调度器计算） |
| `last_run_time` | datetime | 上次执行时间 |
| `last_run_status` | int | 上次执行状态：1=成功，2=失败 |

## ScheduledTaskLog 字段表

| 字段 | 类型 | 说明 |
|------|------|------|
| `id` | int | 主键 |
| `task_id` | int | 关联定时任务 ID |
| `execution_id` | int | Flow 目标时关联 `flow_execution.id` |
| `session_id` | int | Agent 目标时关联 `agent_session.id` |
| `agent_id` | int | Agent 目标的 flow_id |
| `status` | int | 0=运行中，1=成功，2=失败 |
| `trigger_type` | int | 1=定时触发，2=手动触发 |
| `start_time` | datetime | 开始时间 |
| `end_time` | datetime | 结束时间 |
| `duration_ms` | int | 执行耗时（毫秒） |
| `error_message` | string | 错误信息 |
| `input_snapshot` | object | 输入参数快照 |

## 枚举说明

### TriggerType

| 值 | 含义 |
|:--:|------|
| 1 | 定时触发（Cron） |
| 2 | 手动触发 |

### LogStatus

| 值 | 含义 |
|:--:|------|
| 0 | 运行中 |
| 1 | 成功 |
| 2 | 失败 |

### ScheduledTaskTargetType

| 值 | 含义 |
|:---|------|
| `flow` | 流程 |
| `agent` | 智能体 |

## Cron 表达式参考

```
 ┌───── minute (0-59)
 │ ┌───── hour (0-23)
 │ │ ┌───── day (1-31)
 │ │ │ ┌───── month (1-12)
 │ │ │ │ ┌───── day_of_week (0-6, 0=周日)
 │ │ │ │ │
 * * * * *
```

| 字符 | 含义 |
|:----:|------|
| `*` | 任意值 |
| `/` | 步长，如 `*/5` 每 5 单位 |
| `-` | 范围，如 `9-17` 9 到 17 |
| `,` | 列表，如 `8,12,18` |
| `?` | 不指定（自动转为 `*`） |
